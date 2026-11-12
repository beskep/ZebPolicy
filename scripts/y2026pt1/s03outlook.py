"""에너지경제연구원 KEEI 2025 장기에너지전망 데이터 해석."""

import functools
import warnings
from dataclasses import KW_ONLY, dataclass
from pathlib import Path
from typing import ClassVar
lazy from collections.abc import Sequence

import cmap
import cyclopts
import numpy as np
import pint
import polars as pl
import ultraplot as uplt
lazy from scipy.interpolate import PchipInterpolator

from zeb.cli import App
from zeb.emission import EmissionFactors
from zeb.y2026.config import Paths  # ruff: ignore[typing-only-first-party-import]

app = App(
    config=cyclopts.config.Toml(
        'env.toml',
        root_keys='2026',
        allow_unknown=True,
        use_commands_as_keys=False,
    )
)

_SOURCE = ('전력', '지역난방', 'LNG', 'LPG', '신재생&기타', '석탄')
_USE = ('상업용', '교육사회용', '기타', '단독주택', '공동주택')

_KEEI2ECO2 = {
    '전기': '전력',
    '지역난방': '지역난방',
    '석유': 'LPG',
    '도시가스': 'LNG',
    '신재생&기타': '신재생&기타',  # NOTE: PV는 이 값 대신 전력과 대조 -- 무시
    '석탄': '석탄',  # NOTE: 가정 부문 미량 존재 -- 무시
}


def _read_raw(src: Path, start: int, *, total: bool = False) -> pl.DataFrame:
    """KEEI 원자료(wide)를 long format으로 변환."""
    return (
        pl
        .read_csv(src)
        .unpivot(index=['sector', 'variable', 'group', 'unit'], variable_name='year')
        .with_columns(pl.col('year').cast(pl.Int64))
        .filter(
            pl.col('year') >= start,
            pl.col('group') != ('' if total else '합계'),
        )
        .drop_nulls('value')
    )


def _pchip(data: pl.DataFrame, keys: Sequence[str]) -> pl.DataFrame:
    """`keys`별로 절점(year, value)을 PCHIP 보간해 연도별 값을 반환."""

    def interpolate(group: pl.DataFrame) -> pl.DataFrame:
        # 절점 연도(year)를 x로 사용 → 절점 간격(6, 10, 10년)이 반영됨.
        # Fritsch-Carlson 기울기, 양 끝은 한쪽 3점 차분.
        x = group['year'].to_numpy()
        y = group['value'].to_numpy().astype(float)
        years = np.arange(x.min(), x.max() + 1)
        return pl.DataFrame({
            'year': years,
            'value': PchipInterpolator(x, y)(years),
            'observed': np.isin(years, x),  # 관측치(절점) 여부
        }).with_columns(pl.lit(group[k].first()).alias(k) for k in keys)

    return (
        pl
        .concat(interpolate(g) for g in data.partition_by(keys))
        .select(*keys, 'year', 'observed', 'value')
        .sort(*keys, 'year')
    )


@dataclass
class _Command:
    _: KW_ONLY
    paths: Paths

    start: int = 2024
    """첫 절점 연도."""

    @property
    def _workspace(self):
        return self.paths.quantity.outlook


@app.command
@dataclass
class InterpolateDemand(_Command):
    """KEEI 전망 에너지 수요 보간."""

    src: Path = Path('data/2026/keei.outlook.csv')
    """KEEI 전망 원자료 (wide format)."""

    dst: Path = Path('01.energy-demand.parquet')

    variable: str = '에너지 수요'

    _KEYS: ClassVar[tuple[str, ...]] = ('sector', 'variable', 'group')

    def _interpolate(self):
        data = _read_raw(self.src, self.start).filter(
            pl.col('variable') == self.variable
        )

        if (unit := data['unit'].unique().item()) != '백만 toe':
            raise ValueError(unit)

        return (
            _pchip(data, self._KEYS)
            .with_columns(pl.col('value') * (10**6))  # 백만 toe -> toe
            .rename({'value': 'energy_demand__toe'})
        )

    def _plot(self, data: pl.DataFrame):
        sectors = ('서비스', '가정')
        fig, axes = uplt.subplots(nrows=1, ncols=2, sharey='limits')
        for (sector, group), sub in data.group_by(
            'sector', 'group', maintain_order=True
        ):
            ax = axes[sectors.index(sector)]
            ax.plot(
                sub['year'],
                sub['energy_demand__toe'],
                label=group,
                lw=2,
                alpha=0.8,
            )

        for sector, ax in zip(sectors, axes, strict=True):
            ax.format(
                title=f'연도별 {sector} 추정 에너지 수요',
                xlabel='',
                ylabel=f'{self.variable} [toe]',
            )
            ax.legend()
            ax.set_ylim(0)

        return fig

    def __call__(self):
        self._workspace.mkdir(exist_ok=True)
        output = self._workspace / self.dst

        data = self._interpolate()
        data.write_parquet(output)
        data.write_csv(output.with_suffix('.csv'), include_bom=True)
        self._plot(data).save(output.with_suffix('.png'))


@app.command
@dataclass
class InterpolateGrowth(_Command):
    """KEEI 전망 산출액 및 주거 면적 보간."""

    src: Path = Path('data/2026/keei.outlook.csv')
    """KEEI 전망 원자료 (wide format)."""

    dst: Path = Path('01.growth.parquet')

    def _interpolate(self):
        sector = pl.col('sector')
        var = pl.col('variable')
        group = pl.col('group')
        # TODO: 상업 서비스와 공공 서비스로 공공/민간 분리?
        data = (
            _read_raw(self.src, self.start, total=True)
            .filter(
                ((sector == '서비스') & (var == '에너지 수요') & (group == '합계'))
                | (
                    (sector == '가정')
                    & (var == '주요지표/형태별 주택')
                    & (group == '합계')
                )
                | (
                    (sector == '가정')
                    & (var == '주요지표')
                    & (group == '평균 주거 면적')
                )
            )
            .with_columns(pl.format('{} {}', 'group', 'unit').alias('variable'))
            .pivot('variable', index='year', values='value')
            .with_columns(
                pl
                .col('합계 백만호')
                .mul(pl.col('평균 주거 면적 m²'))
                .alias('주거_면적__m2'),
            )
            .select(
                'year',
                pl.col('합계 백만 toe').alias('비주거_에너지수요__toe'),
                '주거_면적__m2',
            )
            .unpivot(index='year')
        )
        return (
            _pchip(data, keys=['variable'])
            .with_columns(
                pl.col('variable').str.extract_groups(
                    r'^(?<sector>주거|비주거)_(?<variable>.*)$'
                )
            )
            .unnest('variable')
            .select('sector', 'year', 'observed', 'variable', 'value')
            .sort('sector', 'year')
        )

    @staticmethod
    def _plot(data: pl.DataFrame):
        fig, axes = uplt.subplots(nrows=2, ncols=1, refaspect=(16 * 2, 9), sharey=False)
        for ax, sector in zip(axes, ('비주거', '주거'), strict=True):
            sub = data.filter(pl.col('sector') == sector)
            observed = sub.filter(pl.col('observed'))
            ax.plot(x=sub['year'], y=sub['value'])
            ax.scatter(x=observed['year'], y=observed['value'])
            ax.format(
                xlabel='',
                ylabel={
                    '비주거': '비주거 에너지 수요 [toe]',
                    '주거': '주거 면적 [m²]',
                }[sector],
            )

        return fig

    def __call__(self):
        output = self._workspace / self.dst

        data = self._interpolate()
        data.write_parquet(output)
        data.write_csv(output.with_suffix('.csv'), include_bom=True)
        self._plot(data).save(output.with_suffix('.png'))


@app.command
@dataclass
class _Split(_Command):
    """보간한 에너지 수요를 세부 용도별로 분리.

    서비스(비주거)는 업종별 산출액, 가정(주거)은 주택 유형별 주택 수에 비례해 배분한다.
    배분 지표도 에너지 수요와 같은 방식(PCHIP)으로 보간한 뒤 연도별 비중을 계산한다.
    모든 에너지원에 같은 비중을 적용하므로, 용도별 에너지원 구성 차이는 반영되지 않는다.
    """

    raw: Path = Path('data/2026/keei.outlook.csv')
    """KEEI 전망 원자료 (wide format). 배분 지표를 읽는다."""

    src: Path = Path('01.energy-demand.parquet')
    """보간한 에너지 수요 (`Interpolate` 결과)."""

    dst: Path = Path('02.split.parquet')

    start: int = 2024

    _DRIVER: ClassVar[dict[str, str]] = {
        '서비스': '업종별 산출액',  # 조원
        '가정': '주요지표',  # 백만호 (단독, 아파트, 공동주택)
    }
    """부문별 배분 지표 (원자료의 `variable`)."""

    _USE_MAP: ClassVar[dict[str, str]] = {
        # 서비스
        '도소매': '상업용',  # 판매시설, 근린생활시설
        '숙박음식': '상업용',  # 숙박시설, 근린생활시설(음식점)
        # NOTE: 운수보관은 운수시설(상업용) 기준. 창고시설(기타)도 포함됨
        '운수보관': '상업용',
        # NOTE: 정보통신은 데이터센터(방송통신시설) 기준. 업무시설(상업용)도 포함됨
        '정보통신': '기타',
        # NOTE: 공공행정및국방은 공공업무시설 기준. 국방·교정시설(기타)도 포함됨
        '공공행정및국방': '상업용',
        '교육서비스': '교육사회용',  # 교육연구시설
        '의료복지': '교육사회용',  # 의료시설, 노유자시설
        '예술&스포츠&레저': '교육사회용',  # 문화및집회시설, 운동시설
        '기타서비스': '상업용',  # 금융·부동산·전문서비스 등 업무시설 중심
        # 가정
        # NOTE: KEEI '단독'은 다가구·다중주택 포함.
        # use.csv는 다가구주택을 공동주택으로 분류함
        '단독': '단독주택',
        '아파트': '공동주택',
        '공동주택': '공동주택',  # KEEI '공동주택' = 아파트 외 공동주택(연립, 다세대 등)
    }

    def _shares(self) -> pl.DataFrame:
        """부문·연도별 용도 비중."""
        is_driver = pl.any_horizontal(
            (pl.col('sector') == sector) & (pl.col('variable') == variable)
            for sector, variable in self._DRIVER.items()
        )
        data = _read_raw(self.raw, self.start).filter(
            is_driver,
            pl.col('group').is_in(list(self._USE_MAP)),
        )

        if missing := set(self._USE_MAP) - set(data['group']):
            msg = f'배분 지표 누락: {sorted(missing)}'
            raise ValueError(msg)

        return (
            _pchip(data, ('sector', 'variable', 'group'))
            .with_columns(pl.col('group').replace_strict(self._USE_MAP).alias('use'))
            .group_by('sector', 'use', 'year')
            .agg(pl.col('value').sum().alias('driver'))
            .with_columns(
                pl
                .col('driver')
                .truediv(pl.col('driver').sum().over('sector', 'year'))
                .alias('share')
            )
        )

    def _split(self) -> pl.DataFrame:
        demand = pl.read_parquet(self._workspace / self.src)
        shares = self._shares()

        data = (
            demand
            .join(shares, on=['sector', 'year'], how='left', validate='m:m')
            .with_columns(
                pl.col('use').cast(pl.Enum(_USE)),
                pl.col('energy_demand_toe').mul(pl.col('share')),
            )
            .select(
                'sector',
                'variable',
                'group',
                'use',
                'year',
                'observed',
                'share',
                'energy_demand_toe',
            )
            .sort('sector', 'group', 'use', 'year')
        )

        if data['share'].null_count():
            msg = '비중이 없는 연도가 있음 (배분 지표와 에너지 수요의 연도 범위 확인)'
            raise ValueError(msg)

        # 분리 전후 합계 일치 확인
        check = (
            data
            .group_by('sector', 'group', 'year')
            .agg(pl.col('energy_demand_toe').sum())
            .join(
                demand,
                on=['sector', 'group', 'year'],
                suffix='_total',
                validate='1:1',
            )
        )
        if not np.allclose(
            check['energy_demand_toe'], check['energy_demand_toe_total'], rtol=1e-9
        ):
            msg = '분리 전후 에너지 수요 합계 불일치'
            raise ValueError(msg)

        return data

    @staticmethod
    def _plot(data: pl.DataFrame):
        uses = ('상업용', '교육사회용', '기타', '공동주택', '단독주택')

        fig, axes = uplt.subplots(nrows=2, ncols=3, sharey='limits')
        for (use, group), sub in data.group_by('use', 'group', maintain_order=True):
            ax = axes[uses.index(use)]
            ax.plot(
                sub['year'],
                sub['energy_demand_toe'],
                label=group,
                lw=2,
                alpha=0.8,
            )

        for use, ax in zip(uses, axes, strict=False):
            ax.format(
                title=f'연도별 {use} 용도별 추정 에너지 수요',
                xlabel='',
                ylabel='에너지 수요 [toe]',
            )
            ax.legend()
            ax.set_ylim(0)

        return fig

    def __call__(self):
        # TODO: 추후 삭제
        warnings.warn(
            '현재 계획상 필요하지 않음.',
            category=DeprecationWarning,
            stacklevel=2,
        )

        output = self._workspace / self.dst

        data = self._split()
        data.write_parquet(output)
        data.write_csv(output.with_suffix('.csv'), include_bom=True)
        self._plot(data).save(output.with_suffix('.png'))

        return data


@app.command
@dataclass
class Emission(_Command):
    """ECO2 배출량 계수로 배출량 계산."""

    src: Path = Path('01.energy-demand.parquet')
    dst: Path = Path('02.emission.parquet')

    demand_unit: str = 'kWh'

    @functools.cached_property
    def _emission(self):
        factors = (
            EmissionFactors
            .read(unit='tonne/toe')
            .dataframe()
            .filter(pl.col('reference') == 'ECO2')
            .drop('reference')
        )
        demand = (
            pl
            .scan_parquet(self._workspace / self.src)
            .select(
                pl.col('sector').replace_strict({'서비스': '비주거', '가정': '주거'}),
                pl.col('group').replace_strict(_KEEI2ECO2).alias('source'),
                'year',
                'observed',
                'energy_demand__toe',
            )
            .collect()
        )

        return (
            demand
            .join(factors, on='source', how='left')
            .with_columns(
                pl.col('source').cast(pl.Enum(_SOURCE)),
                pl
                .col('energy_demand__toe')
                .mul(pl.col('emission_factor'))
                .alias('emission__tCO2'),
            )
            .with_columns(
                pl.col('emission__tCO2').truediv(10**6).alias('emission__MtCO2')
            )
            .sort('sector', 'source', 'year')
        )

    @functools.cached_property
    def _emission_by_scope(self):
        ur = pint.UnitRegistry()
        k = ur.Quantity(1.0, 'toe').to(self.demand_unit).magnitude
        demand = f'demand__{self.demand_unit}'
        return (
            self._emission
            .with_columns(
                pl.col('source').replace_strict(
                    {
                        '전력': 'indirect',
                        '지역난방': 'indirect',
                        'LNG': 'direct',
                        'LPG': 'direct',
                    },
                    default=None,
                ),
                pl.col('energy_demand__toe').mul(k).alias(demand),
            )
            .drop_nulls('source')
            .group_by('sector', 'source', 'year', 'observed')
            .agg(pl.sum(demand, 'emission__MtCO2'))
            .sort('sector', 'source', 'year')
        )

    def _plot(self):
        sectors = ('비주거', '주거')
        total = (
            self._emission
            .group_by('sector', 'source', 'year')
            .agg(pl.col('emission__MtCO2').sum())
            .sort('sector', 'source', 'year')
        )
        fig, axes = uplt.subplots(nrows=1, ncols=2, sharey='limits')
        for (sector, source), sub in total.group_by(
            'sector', 'source', maintain_order=True
        ):
            ax = axes[sectors.index(sector)]
            ax.plot(
                sub['year'],
                sub['emission__MtCO2'],
                label=source,
                lw=2,
                alpha=0.8,
            )

        for sector, ax in zip(sectors, axes, strict=True):
            ax.format(
                title=f'연도별 {sector} 추정 배출량',
                xlabel='',
                ylabel=r'탄소 배출량 [$\mathrm{MtCO_2}$]',
            )
            ax.legend()
            ax.set_ylim(0)

        return fig

    def __call__(self):
        output = self._workspace / self.dst

        self._emission.write_parquet(output)
        self._emission.write_csv(output.with_suffix('.csv'), include_bom=True)
        self._plot().save(output.with_suffix('.png'))

        self._emission_by_scope.write_csv(
            output.parent / f'{output.stem}_scope.csv', include_bom=True
        )

        return self._emission


if __name__ == '__main__':
    cmap.register_mpl('tol:bright_alt')
    uplt.rc['mathtext.fontset'] = 'custom'
    app()
