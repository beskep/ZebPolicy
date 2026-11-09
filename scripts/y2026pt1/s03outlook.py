"""에너지경제연구원 KEEI 2025 장기에너지전망 데이터 해석."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import cmap
import cyclopts
import numpy as np
import polars as pl
import ultraplot as uplt
from scipy.interpolate import PchipInterpolator

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

_CYCLE = 'tol:bright_alt'
_SOURCE = ('전력', '지역난방', 'LNG', 'LPG', '신재생&기타', '석탄')


@app.command
@dataclass
class Interpolate:
    """KEEI 전망 에너지 수요 보간."""

    paths: Paths

    src: Path = Path('data/2026/keei.outlook.csv')
    """KEEI 전망 원자료 (wide format)."""

    dst: Path = Path('data/2026/keei.outlook.01.demand.parquet')

    variable: str = '에너지 수요'
    start: int = 2024
    """첫 절점 연도."""

    _KEYS: ClassVar[tuple[str, ...]] = ('sector', 'variable', 'group')

    def _interpolate(self):
        data = (
            pl
            .read_csv(self.src)
            .filter(pl.col('variable') == self.variable)
            .unpivot(index=[*self._KEYS, 'unit'], variable_name='year')
            .with_columns(pl.col('year').cast(pl.Int64))
            .filter(pl.col('year') >= self.start)
            .drop_nulls('value')
            .sort(*self._KEYS, 'year')
        )

        if (unit := data['unit'].unique().item()) != '백만 toe':
            raise ValueError(unit)

        def pchip(group: pl.DataFrame) -> pl.DataFrame:
            # 절점 연도(year)를 x로 사용 → 절점 간격(6, 10, 10년)이 반영됨.
            # Fritsch-Carlson 기울기, 양 끝은 한쪽 3점 차분.
            x = group['year'].to_numpy()
            y = group['value'].to_numpy().astype(float)
            years = np.arange(x.min(), x.max() + 1)
            return (
                pl
                .DataFrame({
                    'year': years,
                    'value': PchipInterpolator(x, y)(years),
                    'observed': np.isin(years, x),  # 관측치(절점) 여부
                })
                .with_columns(pl.lit(group[k].first()).alias(k) for k in self._KEYS)
                .with_columns()
            )

        return (
            pl
            .concat(
                pchip(g) for g in data.partition_by(self._KEYS, maintain_order=True)
            )
            .select(*self._KEYS, 'year', 'observed', 'value')
            .with_columns(pl.col('value') * (10**6))  # 백만 toe -> toe
            .rename({'value': 'energy_demand_toe'})
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
                sub['energy_demand_toe'],
                label=group,
                cycle=_CYCLE,
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

        fig.savefig(self.paths.quantity.root / '01.outlook-energy-demand.png')

    def __call__(self):
        data = self._interpolate()
        data.write_parquet(self.dst)

        self._plot(data)


@app.command
@dataclass
class Emission:
    """ECO2 배출량 계수로 배출량 계산."""

    paths: Paths

    src: Path = Path('data/2026/keei.outlook.01.demand.parquet')
    dst: Path = Path('data/2026/keei.outlook.02.emission.parquet')

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
            .scan_parquet(self.src)
            .select(
                pl.col('sector').replace_strict({'서비스': '비주거', '가정': '주거'}),
                pl
                .col('group')
                .replace_strict({
                    '전기': '전력',
                    '지역난방': '지역난방',
                    '신재생&기타': '신재생&기타',
                    '석탄': '석탄',  # NOTE: 가정 부문 미량 존재 -- 무시
                    '석유': 'LPG',
                    '도시가스': 'LNG',
                })
                .alias('source'),
                'year',
                'observed',
                'energy_demand_toe',
            )
            .collect()
        )

        return (
            demand
            .join(factors, on='source', how='left')
            .with_columns(
                pl.col('source').cast(pl.Enum(_SOURCE)),
                pl
                .col('energy_demand_toe')
                .mul(pl.col('emission_factor'))
                .alias('emission_tCO2'),
            )
            .with_columns(
                pl.col('emission_tCO2').truediv(10**6).alias('emission_MtCO2')
            )
            .sort('sector', 'source', 'year')
        )

    def _plot(self, data: pl.DataFrame):
        sectors = ('비주거', '주거')
        fig, axes = uplt.subplots(nrows=1, ncols=2, sharey='limits')
        for (sector, source), sub in data.group_by(
            'sector', 'source', maintain_order=True
        ):
            ax = axes[sectors.index(sector)]
            ax.plot(
                sub['year'],
                sub['emission_MtCO2'],
                label=source,
                cycle=_CYCLE,
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

        fig.savefig(self.paths.quantity.root / '02.outlook-emission.png')

    def __call__(self):
        data = self._emission()
        data.write_parquet(self.dst)
        data.write_csv(self.dst.with_suffix('.csv'), include_bom=True)

        self._plot(data)

        return data


if __name__ == '__main__':
    cmap.register_mpl(_CYCLE)
    app()
