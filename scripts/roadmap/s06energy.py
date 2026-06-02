"""2024-11-22 사용량 분석."""

from __future__ import annotations

import dataclasses as dc
import functools
import itertools
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Literal

import fastexcel
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pathvalidate
import pingouin as pg
import pint
import polars as pl
import polars.selectors as cs
import rich
import seaborn as sns
from cmap import Colormap
from loguru import logger
from matplotlib.figure import Figure
from matplotlib.layout_engine import ConstrainedLayoutEngine
from matplotlib.ticker import MaxNLocator, StrMethodFormatter
from xlsxwriter import Workbook
from zeb.roadmap.cpr import ChangePointRegression, CprError

from scripts.roadmap.config import Config as GlobalConfig
from zeb import utils
from zeb.utils.cli import App
from zeb.utils.terminal import Progress

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from matplotlib.axes import Axes
    from polars._typing import FrameType


def _join_frames(frame: FrameType, *frames: FrameType):
    for f in frames:
        frame = frame.join(f, on=list(set(frame.columns) & set(f.columns)), how='left')

    return frame


@dc.dataclass(frozen=True)
class Config:
    th: float = 18  # °C
    tc: float = 24  # °C

    root: Path = dc.field(default_factory=lambda: GlobalConfig.read().energy.root)

    building: str = '0000건물.xlsx'
    energy: str = '0000에너지사용량.xlsx'
    weather: str = '0000지역별월평균온도.xlsx'
    equipment: str = '../04구간분석/01정책효과분석.parquet'


@dc.dataclass
class Preprocess:
    app_no: str = '신청번호'

    units: Sequence[str] = ('kWh', 'MWh', 'MJ', 'Gcal', 'Mcal', 'Nm3')
    target_unit: str = 'kWh'

    conf: Config = dc.field(default_factory=Config)
    uc: dict[str, float] = dc.field(default_factory=dict)

    def __post_init__(self):
        ur = pint.UnitRegistry()
        ur.define('Nm3 = 10190 kcal')  # 천연가스 Nm³ (에너지열량 환산기준)
        self.uc = {
            unit: ur.Quantity(1.0, unit).to(self.target_unit).magnitude
            for unit in self.units
        }

    @staticmethod
    def multiline_header(
        source: str | Path,
        sheet_id: int | None = None,
        sheet_name: str | None = None,
        n: int = 2,
        **kwargs,
    ):
        return (
            pl
            .read_excel(
                source,
                sheet_id=sheet_id,
                sheet_name=sheet_name,
                read_options={'n_rows': n},
                has_header=False,
                **kwargs,
            )
            .transpose()
            .fill_null('')
            .select(pl.format('{}_{}', 'column_0', 'column_1').str.strip_chars('_'))
            .to_series()
            .to_list()
        )

    def _read_energy(
        self,
        source: str | Path,
        sheet_id: int | None = None,
        sheet_name: str | None = None,
        n_name_rows: int = 2,
    ):
        columns = self.multiline_header(
            source=source, sheet_id=sheet_id, sheet_name=sheet_name, n=n_name_rows
        )
        data = pl.read_excel(
            source,
            sheet_id=sheet_id,
            sheet_name=sheet_name,
            read_options={'skip_rows': n_name_rows},
            has_header=False,
        )
        data.columns = columns
        data = data.with_columns(pl.col(self.app_no).cast(pl.UInt32))

        bldg = data.drop('단위', cs.contains('사용량')).with_columns(
            pl.col('연면적').cast(pl.Float64)
        )
        energy = (
            data
            .select(self.app_no, '단위', cs.contains('사용량'))
            .unpivot(index=[self.app_no, '단위'])
            .with_columns(pl.col('variable').str.split('_'))
            .select(
                self.app_no,
                pl
                .col('단위')
                .replace({'KWH': 'kWh', 'Mwh': 'MWh', 'M3': 'Nm3'})
                .alias('unit'),
                pl.col('variable').list.get(0).alias('energy'),
                pl.col('variable').list.get(1).str.to_date('%Y%m').alias('date'),
                pl.col('value').cast(pl.Float64),
            )
        )

        if (d := bldg.filter(pl.col('건축물명').is_duplicated())).height:
            logger.warning(
                'sheet={} | duplicated buildings={}',
                (sheet_id, sheet_name),
                d.select(pl.col('건축물명').unique().sort()).to_series().to_list(),
            )

        return bldg, energy

    def _read_building(
        self,
        source: str | Path | None = None,
        additional: pl.DataFrame | None = None,
    ):
        source = source or self.conf.root / self.conf.building
        data = pl.read_excel(source).with_columns(pl.col(self.app_no).cast(pl.UInt32))

        if additional is not None:
            rich.print(additional)
            rich.print(data)
            logger.info('builidng.shape={}', data.shape)
            logger.info('energy.building.shape={}', additional.shape)
            data = data.join(
                additional,
                on=list(set(data.columns) & set(additional.columns)),
                how='left',
            )

        return data.sort(self.app_no)

    def _building(self, data: pl.DataFrame):
        building = (
            pl
            .read_excel(self.conf.root / self.conf.building)
            .with_columns(pl.col(self.app_no).cast(pl.UInt32))
            .with_columns()
        )
        equipment = (
            pl
            .scan_parquet(self.conf.root / self.conf.equipment)
            .select(pl.col(self.app_no).cast(pl.UInt32), '온열원설비 분류')
            .collect()
        )

        return _join_frames(building, equipment, data)

    def read_building_and_energy(self, source: Path | None = None):
        source = source or self.conf.root / self.conf.energy

        sheets = fastexcel.read_excel(source).sheet_names
        data = [
            self._read_energy(source, sheet_name=sheet)
            for sheet in sheets
            if sheet != '분석대상'
        ]

        bldg = pl.concat(d[0] for d in data).unique().sort(self.app_no)
        energy = pl.concat(d[1] for d in data)

        bldg = self._building(bldg)

        energy = (
            energy
            .select(
                self.app_no,
                'date',
                pl.col('energy').str.strip_suffix('사용량'),
                pl
                .col('value')
                .mul(pl.col('unit').replace_strict(self.uc, return_dtype=pl.Float64))
                .alias('value'),
                pl.lit(self.target_unit).alias('unit'),
                pl.col('value').alias('OriginalValue'),
                pl.col('unit').alias('OriginalUnit'),
            )
            .drop_nulls('value')
            .sort(self.app_no, 'date', 'energy')
        )

        if energy.select('신청번호', 'date', 'energy').is_duplicated().any():
            energy = energy.group_by(self.app_no, 'date', 'energy', 'unit').agg(
                pl.sum('value'), pl.col('OriginalUnit')
            )

        return bldg, energy

    def degree_day(self, path: Path | None = None):
        path = path or self.conf.root / self.conf.weather

        return (
            pl
            .read_excel(path)
            .rename({
                '지역': 'region',
                '년월': 'date',
                '지점': 'station',
                '평균기온(℃)': 'T',
            })
            .select(
                'region',
                'station',
                'date',
                pl.col('date').dt.month_end().dt.day().alias('days'),
                'T',
                pl.max_horizontal(0, pl.lit(self.conf.th) - pl.col('T')).alias('HDDm'),
                pl.max_horizontal(0, pl.col('T') - pl.lit(self.conf.tc)).alias('CDDm'),
            )
            .with_columns(
                pl.col('days').mul('HDDm').alias('HDD'),
                pl.col('days').mul('CDDm').alias('CDD'),
            )
            .with_columns((pl.col('HDD') + pl.col('CDD')).alias('HCDD'))
        )


@dc.dataclass
class EuiCalculator:
    file_building: str = '0001building'
    file_degree_day: str = '0001degree_day'
    file_energy: str = '0001energy'
    file_outlier: str = '0101outlier'

    app_no: str = '신청번호'

    conf: Config = dc.field(default_factory=Config)

    @functools.cached_property
    def eui(self):
        root = self.conf.root
        app_no = self.app_no

        degree_day = (
            pl
            .scan_parquet(root / f'{self.file_degree_day}.parquet')
            .with_columns(
                pl.col('date').dt.year().alias('year'),
                pl.col('date').dt.month().alias('month'),
            )
            .select(
                pl.col('region').alias('지역'),
                'year',
                'month',
                pl.col('HCDD').alias('DD'),
            )
        )

        return (
            pl
            .scan_parquet(root / f'{self.file_energy}.parquet')
            .drop(cs.starts_with('Original'), 'unit')
            .with_columns(
                pl.col('date').dt.year().alias('year'),
                pl.col('date').dt.month().alias('month'),
            )
            .join(
                pl.scan_parquet(root / f'{self.file_outlier}.parquet').select(
                    app_no, 'year', 'outlier'
                ),
                on=[app_no, 'year'],
            )
            .filter(pl.col('outlier').is_null())
            .drop('outlier')
            .join(
                pl.scan_parquet(root / f'{self.file_building}.parquet').select(
                    app_no, '연면적', pl.col('지역2').alias('지역'), '건축물명'
                ),
                on=app_no,
                how='left',
            )
            .rename({'value': 'energy_use'})
            .with_columns((pl.col('energy_use') / pl.col('연면적')).alias('EUI'))
            .join(degree_day, on=['year', 'month', '지역'], how='left')
            .join(
                degree_day
                .filter(pl.col('지역') == '서울')
                .drop('지역')
                .rename({'DD': 'DDseoul'}),
                on=['year', 'month'],
            )
            .with_columns(
                pl
                .col('DDseoul')
                .truediv(pl.col('DD'))
                .replace({float('inf'): 1.0})
                .fill_nan(1.0)
                .alias('adj_coef')
            )
            .with_columns((pl.col('EUI') * pl.col('adj_coef')).alias('EUIadj'))
            .select(
                app_no,
                '건축물명',
                '연면적',
                '지역',
                'date',
                pl.col('DD').alias('DegDay'),
                pl.col('DDseoul').alias('DegDaySeoul'),
                'energy',
                'energy_use',
                'EUI',
                'EUIadj',
            )
            .sort(app_no, 'date')
            .collect()
        )

    def pivot_eui(self, interval: Literal['month', 'year'], *, by_energy: bool = True):
        index = [self.app_no, '건축물명', '연면적', '지역']
        sort_by = [pl.col(self.app_no)]
        if by_energy:
            index.append('energy')
            sort_by.append(
                pl.col('energy').replace({'energy_use': 0, 'EUI': 1, 'EUIadj': 2})
            )

        energy = ['energy_use', 'EUI', 'EUIadj']
        eui = (
            self.eui
            .with_columns(
                pl.col('date').dt.year().alias('date')
                if interval == 'year'
                else pl.col('date').dt.strftime('%Y-%m')
            )
            .group_by([*index, 'date'])
            .agg(pl.sum(*energy))
        )

        return (
            eui
            .unpivot(energy, index=[*index, 'date'])
            .pivot(
                'date', index=[*index, 'variable'], values='value', sort_columns=True
            )
            .sort(sort_by)
        )


@dc.dataclass
class _CprBldg:
    app_no: int
    building: str
    outlier: bool

    def __post_init__(self):
        self.building = pathvalidate.sanitize_filename(
            self.building, replacement_text='-'
        )

    @classmethod
    def from_frame(cls, frame: pl.DataFrame):
        return cls(
            frame.item(0, '신청번호'),
            frame.item(0, '건축물명'),
            any(x != 'normal' for x in frame.select('outlier').to_series()),
        )

    def name(self):
        outlier = '_HasOutlier' if self.outlier else ''
        return f'{self.app_no}_{self.building}{outlier}'


@dc.dataclass
class CPR:
    paper: bool = True

    file_building: str = '0001building'
    file_degree_day: str = '0001degree_day'
    file_outlier: str = '0103outlier'
    file_eui: str = '0102EUI'
    file_seoul: str = '0000서울시공공건물에너지효율등급CPR'

    conf: Config = dc.field(default_factory=Config)

    def read_zeb_data(self, buildings: Collection[str] | None = None) -> pl.LazyFrame:
        root = self.conf.root
        weather = (
            pl
            .scan_parquet(root / f'{self.file_degree_day}.parquet')
            .select('region', 'date', 'T')
            .rename({'region': '지역'})
        )
        outlier = (
            pl
            .scan_parquet(root / f'{self.file_outlier}.parquet')
            .select('신청번호', 'year', 'outlier')
            .with_columns()
        )
        data = (
            pl
            .scan_parquet(root / f'{self.file_eui}.parquet')
            .group_by('신청번호', '건축물명', '지역', 'date')
            .agg(pl.sum('EUIadj'))
            .with_columns(pl.col('date').dt.year().alias('year'))
            .join(weather, on=['지역', 'date'], how='left')
            .join(outlier, on=['신청번호', 'year'], how='left')
        )
        if buildings is not None:
            data = data.filter(pl.col('건축물명').is_in(buildings))
        return data

    def read_seoul_data(self, buildings: Collection[str] | None = None) -> pl.LazyFrame:
        """
        서울시 2024년 공공건물 에너지효율등급 평가 데이터.

        Parameters
        ----------
        buildings : Collection[str] | None

        Returns
        -------
        pl.LazyFrame
        """
        data = pl.scan_parquet(self.conf.root / f'{self.file_seoul}.parquet').select(
            pl.col('건물1').alias('건축물명'),
            pl.col('temperature').alias('T'),
            (pl.col('value') / pl.col('연면적') / 3.6).alias('E'),  # MJ to kWh/m2
            'date',
        )
        if buildings is not None:
            data = data.filter(pl.col('건축물명').is_in(buildings))
        return data

    def cpr_zeb(
        self,
        data: pl.DataFrame,
        bldg: _CprBldg,
        *,
        plot_dir: Path | None = None,
    ):
        try:
            model = ChangePointRegression(
                data, temperature='T', energy='EUIadj'
            ).optimize_multi_models()
        except CprError as e:
            logger.warning(f'{e!r}')
            return None

        if plot_dir is not None:
            fig = Figure()
            ax = fig.subplots()
            style: dict = {
                'scatter': (
                    {'hue': None, 'alpha': 0.6}
                    if self.paper
                    else {'hue': 'year', 'palette': 'crest'}
                )
            }
            model.plot(ax=ax, style=style)

            ax.set_xlabel(
                '{} [°C]'.format(
                    'Average External Temperature' if self.paper else '월간 기온'
                )
            )
            ax.set_ylabel(
                '{} [kWh/m²]'.format('Energy Use' if self.paper else '월간 EUI')
            )
            ax.text(
                x=0.02,
                y=0.025,
                s=f'$r^2={model.model_dict["r2"]:.3g}$',
                transform=ax.transAxes,
            )

            if self.paper:
                ax.set_xlim(-5, 30)
            else:
                ax.legend(loc='center left', bbox_to_anchor=(1, 0.5))

            fig.savefig(plot_dir / f'{bldg.name()}.png')

        return model.model_frame.select(
            pl.lit(bldg.app_no).alias('신청번호'),
            pl.lit(bldg.building).alias('건축물명'),
            pl.lit(bldg.outlier).alias('이상치구분'),
            pl.all(),
        )

    def cpr_zeb_batch(self, plot_dir: Path | None = None):
        (
            utils.mpl
            .MplTheme(
                0.5 if self.paper else 1.0,
                font={
                    'family': 'serif' if self.paper else 'sans-serif',
                },
                fig_size=(3.6 * 1.2, 3.2 * 1.2) if self.paper else (16, 9),
            )
            .grid()
            .apply()
        )
        data = self.read_zeb_data(buildings=None)

        for app_no in Progress.iter(
            data.select(pl.col('신청번호').unique().sort()).collect().to_series()
        ):
            d = data.filter(pl.col('신청번호') == app_no).collect()
            bldg = _CprBldg.from_frame(d)
            logger.info(bldg)

            if (
                model := self.cpr_zeb(data=d, bldg=bldg, plot_dir=plot_dir)
            ) is not None:
                yield model

    @staticmethod
    def _cpr_compare(data: pl.DataFrame, ymax: float, ax: Axes | None = None):
        try:
            model = ChangePointRegression(data).optimize_multi_models()
        except CprError as e:
            logger.warning(f'{e!r}')
            return None

        if ax is not None:
            model.plot(ax=ax, style={'scatter': {'hue': 'year', 'palette': 'crest'}})
            ax.dataLim.update_from_data_y([0, ymax])
            ax.autoscale_view()

            ax.set_xlabel('월간 기온 [°C]')
            ax.set_ylabel('월간 EUI [kWh/m²]')
            text = ax.text(
                x=0.5,
                y=0.95,
                s=f'$r^2={model.model_dict["r2"]:.3g}$',
                transform=ax.transAxes,
                va='top',
                ha='center',
            )
            text.set_bbox({'facecolor': 'white', 'alpha': 0.75})

        return model

    def cpr_compare(
        self,
        save_dir: Path,
        conf: GlobalConfig | None = None,
        legend_loc: str | None = None,
    ):
        conf = conf or GlobalConfig.read()
        prefix = '(서울시)'

        seoul = self.read_seoul_data([
            x.removeprefix(prefix)
            for x in itertools.chain.from_iterable(conf.energy.cpr)
            if x.startswith(prefix)
        ])
        zeb = (
            self
            .read_zeb_data([
                x
                for x in itertools.chain.from_iterable(conf.energy.cpr)
                if not x.startswith(prefix)
            ])
            .drop('year')
            .rename({'EUIadj': 'E'})
        )
        data = (
            pl
            .concat([seoul, zeb], how='diagonal')
            .with_columns(
                pl.col('신청번호').fill_null(9999999999),
                pl.col('outlier').fill_null('normal'),
                pl.col('date').dt.year().alias('year'),
            )
            .collect()
        )

        for idx, group in enumerate(conf.energy.cpr):
            logger.info('group={}', group)

            max_e = (
                data.filter(pl.col('건축물명').is_in(group)).select(pl.max('E')).item()
            )
            for building in group:
                logger.info('building={}', building)

                d = data.filter(pl.col('건축물명') == building.removeprefix(prefix))
                if not d.height:
                    raise ValueError(building)

                name = f'group{idx + 1}_{_CprBldg.from_frame(d).name()}'

                fig, ax = plt.subplots()
                model = self._cpr_compare(d, ymax=max_e, ax=ax)

                if legend_loc is not None:
                    ax.legend(loc=legend_loc)
                    name = f'{name}_legend {legend_loc}'

                if model is not None:
                    model.model_frame.write_excel(save_dir / f'{name}.xlsx')
                    fig.savefig(save_dir / f'{name}.png')

                plt.close(fig)


DEFAULT_CONFIG = Config()

app = App(result_action=['call_if_callable', 'print_non_int_sys_exit'])
for s in ['prep', 'analyze', 'cpr']:
    app.command(App(s))


@app['prep'].command
def prep_convert(*, conf: Config = DEFAULT_CONFIG):
    """건물, 에너지, 도일 parquet 변환."""
    p = Preprocess(conf=conf)
    rich.print(p)

    root = conf.root

    bldg, energy = p.read_building_and_energy()
    bldg.write_parquet(root / '0001building.parquet')
    bldg.write_excel(root / '0001building.xlsx', autofit=True)

    energy.write_parquet(root / '0001energy.parquet')
    energy.write_excel(root / '0001energy.xlsx', autofit=True)

    dd = p.degree_day()
    rich.print(dd)
    dd.write_parquet(root / '0001degree_day.parquet')
    dd.write_excel(root / '0001degree_day.xlsx', column_widths=100)


@app['prep'].command
def prep_outlier_rule(
    *,
    last_year: int = 2023,
    january_threshold: float = 3,
    threshold: float = 30,
    n_month: int = 6,
    conf: Config = DEFAULT_CONFIG,
):
    app_no = '신청번호'
    he = '온열원설비 분류'

    equipment = pl.scan_parquet(conf.root / '0001building.parquet').select(app_no, he)

    energy = (
        pl
        .scan_parquet(conf.root / '0001energy.parquet')
        .with_columns(
            pl.col('date').dt.year().alias('year'),
            pl.col('value').fill_null(0),
        )
        .with_columns(pl.col('value').fill_null(0))
        .join(equipment, on=app_no, how='left')
        .collect()
    )

    if energy.select(pl.col(he).is_null().any()).item():
        raise ValueError

    # 2023년 에너지 총사용량이 0
    last_zero = (
        energy
        .filter(pl.col('year') == last_year)
        .group_by(app_no)
        .agg(pl.sum('value') == 0)
        .filter('value')
        .select(app_no, pl.lit(f'{last_year}년').alias('outlier_lastyear'))
    )

    # 1월 사용량이 기준치 이하 (중간부터 사용량 존재)
    january = (
        energy
        .filter(pl.col('date').dt.month() == 1)
        .group_by(app_no, 'year')
        .agg(pl.sum('value'))
        .filter(pl.col('value') < january_threshold)
        .select(app_no, 'year', pl.lit('1월').alias('outlier_january'))
    )

    # n개월 이상 사용량 동일
    consecutive = (
        energy
        .filter(pl.col('energy') == '전기')
        .sort(app_no, 'date')
        .with_columns(
            pl.col('value').rolling_std(n_month).over(app_no).eq(0).alias('zero_std')
        )
        .group_by(app_no, pl.col('date').dt.year().alias('year'))
        .agg(
            pl
            .col('zero_std')
            .any()
            .replace_strict(
                {True: f'{n_month}개월 동일', False: None}, return_dtype=pl.String
            )
            .alias('outlier_consecutive')
        )
    )

    # 난방설비 판단
    district = (
        energy
        .filter(pl.col(he) == '지역난방', pl.col('energy') == '지역난방')
        .group_by(app_no, 'year')
        .agg(pl.sum('value'))
        .filter(pl.col('value') < threshold)
        .select(app_no)
        .unique()
        .with_columns(pl.lit('지역난방').alias('outlier_district'))
    )
    gas = (
        energy
        .filter(
            pl.col(he).is_in(['GHP(LNG)', '가스보일러(LNG)']),
            pl.col('energy') == '도시가스',
        )
        .group_by(app_no, 'year')
        .agg(pl.sum('value'))
        .filter(pl.col('value') < threshold)
        .select(app_no)
        .unique()
        .with_columns(pl.lit('도시가스').alias('outlier_gas'))
    )

    outlier = (
        energy
        .with_columns(
            pl.format(
                '{}월', pl.col('date').dt.month().cast(pl.String).str.pad_start(2, '0')
            ).alias('month')
        )
        .group_by(app_no, he, 'year', 'month')
        .agg(pl.sum('value'))
        .pivot('month', index=[app_no, he, 'year'], values='value', sort_columns=True)
        .sort(app_no, 'year')
        .join(last_zero, on=app_no, how='left')
        .join(january, on=[app_no, 'year'], how='left')
        .join(consecutive, on=[app_no, 'year'], how='left')
        .join(district, on=app_no, how='left')
        .join(gas, on=app_no, how='left')
        .with_columns(
            pl.concat_list(cs.starts_with('outlier')).list.drop_nulls().alias('outlier')
        )
        .select(
            app_no,
            he,
            'year',
            pl
            .when(pl.col('outlier').list.len() == 0)
            .then(pl.lit(None))
            .otherwise(pl.col('outlier'))
            .alias('outlier'),
            pl.sum_horizontal(cs.ends_with('월')).alias('합계'),
            cs.ends_with('월'),
        )
    )

    rich.print(outlier)

    outlier.write_parquet(conf.root / '0101outlier.parquet')
    outlier.write_excel(conf.root / '0101outlier.xlsx', column_widths=100)


@app['prep'].command
def prep_eui(*, conf: Config = DEFAULT_CONFIG):
    root = conf.root

    calc = EuiCalculator(conf=conf)
    eui = calc.eui

    eui.write_parquet(root / '0102EUI.parquet')
    eui.write_excel(root / '0102EUI.xlsx', column_widths=100)

    pl.Config.set_tbl_cols(20)
    rich.print(eui)

    with Workbook(root / '0102EUI-pivot.xlsx') as wb:
        for interval, energy in itertools.product(['year', 'month'], [False, True]):
            pivot = calc.pivot_eui(interval=interval, by_energy=energy)  # type: ignore[arg-type]
            pivot.write_excel(
                wb,
                worksheet=f'{interval}-{"에너지별" if energy else "합계"}',
                column_widths=100,
            )


@app['prep'].command
def prep_outlier_dist(
    *,
    min_eui: float = 30,
    tukey_k: float = 1.5,
    conf: Config = DEFAULT_CONFIG,
):
    """Threshold, IQR 기준 이상치 제외."""
    app_no = '신청번호'
    eui = pl.col('EUIadj')

    building = (
        pl
        .scan_parquet(conf.root / '0001building.parquet')
        .select(
            app_no,
            '건축물대장 PK',
            '건축물명',
            '등급',
            '건물용도',
            '건물주용도',
            '지역',
            '정책구간',
            '면적구간',
        )
        .collect()
    )

    energy = pl.scan_parquet(conf.root / '0102EUI.parquet')
    outlier = (
        energy
        .group_by(app_no, pl.col('date').dt.year().alias('year'))
        .agg(eui.sum())
        .with_columns(eui.quantile(0.25).alias('Q1'), eui.quantile(0.75).alias('Q3'))
        .with_columns(
            lower_threshold=pl.col('Q1') - tukey_k * (pl.col('Q3') - pl.col('Q1')),
            upper_threshold=pl.col('Q3') + tukey_k * (pl.col('Q3') - pl.col('Q1')),
        )
        .with_columns(
            pl
            .when(eui < min_eui)
            .then(pl.lit('outlier(threshold)'))
            .when(eui.is_between('lower_threshold', 'upper_threshold').not_())
            .then(pl.lit('outlier(tukey)'))
            .otherwise(pl.lit('normal'))
            .alias('outlier')
        )
        .sort(app_no, 'year')
        .collect()
        .join(building, on=app_no, how='left')
    )
    outlier = outlier.select([
        *building.columns,
        *(x for x in outlier.columns if x not in building.columns),
    ])

    rich.print(outlier)

    outlier.write_parquet(conf.root / '0103outlier.parquet')
    outlier.write_excel(conf.root / '0103outlier.xlsx', column_widths=100)

    desc = pl.concat(
        df
        .select(cs.numeric())
        .describe()
        .select(pl.lit(b[0]).alias('정책구간'), pl.all())
        for b, df in outlier
        .filter(pl.col('outlier') == 'normal')
        .sort('정책구간')
        .group_by('정책구간', maintain_order=True)
    )

    desc.write_excel(conf.root / '0103outlier-정책구간별 통계.xlsx', column_widths=100)

    fig, ax = plt.subplots()
    sns.histplot(outlier, x='EUIadj', hue='outlier', ax=ax)
    ax.set_xlabel('연간 EUI [kWh/m²]')
    fig.savefig(conf.root / 'plot-outlier-tukey.png')


def _analyze_eui(*, conf: Config):
    app_no = '신청번호'
    building = (
        pl
        .scan_parquet(conf.root / '0001building.parquet')
        .select(
            app_no,
            '등급',
            '지역',
            '건물용도',
            '건물주용도',
            '주체',
            '온열원설비 분류',
            '정책구간',
            pl.col('면적구간').replace_strict({
                'A1': 'A1',
                'A2': 'A1',
                'A3': 'A2',
                'A4': 'A3',
                'A5': 'A3',
            }),
        )
        .collect()
    )
    outlier = (
        pl
        .scan_parquet(conf.root / '0103outlier.parquet')
        .select(app_no, 'year', 'outlier')
        .collect()
    )

    index = [app_no, '건축물명', '연면적']
    eui = ['EUI전기', 'EUI도시가스', 'EUI지역난방']
    energy = (
        pl
        .scan_parquet(conf.root / '0102EUI.parquet')
        .with_columns(pl.format('EUI{}', 'energy').alias('energy'))
        .group_by([*index, pl.col('date').dt.year().alias('year'), 'energy'])
        .agg(pl.sum('EUI', 'EUIadj'))
        .with_columns(
            pl
            .col('EUI')
            .truediv(pl.col('EUI').sum().over(app_no, 'year'))
            .alias('EUIratio')
        )
        .unpivot(
            ['EUI', 'EUIadj', 'EUIratio'],
            index=[*index, 'year', 'energy'],
            variable_name='variable',
            value_name='value',
        )
        .collect()
        .join(outlier, on=[app_no, 'year'], how='left')
        .filter(pl.col('outlier') == 'normal')
        .pivot('energy', index=[*index, 'year', 'variable'], values='value')
        .with_columns(pl.sum_horizontal(pl.col(eui).fill_null(0)).alias('EUI합계'))
    )

    data = (
        energy
        .join(building, on=app_no, how='left')
        .select(
            cs.exclude('year', cs.starts_with('EUI')), 'year', cs.starts_with('EUI')
        )
        .sort(app_no, 'variable', 'year')
        .rename({'year': '연도'})
    )
    cols = [x for x in data.columns if x != 'variable']
    cols.insert(cols.index('연도'), 'variable')
    return data.select(cols)


def _analyze_eui_agg(data: pl.DataFrame, *, conf: Config):
    policy = '정책구간'
    with Workbook(conf.root / '0201EUI분석.xlsx') as wb:
        data.write_excel(wb, worksheet='tidy', column_widths=90)

        def agg(index: list[str], agg: Literal['mean', 'median'] = 'mean'):
            unpivot = data.filter(pl.col('variable') != 'EUIratio').unpivot(
                cs.starts_with('EUI'),
                index=[*index, policy, 'variable'],
                variable_name='energy',
            )

            avg = (
                unpivot
                .group_by([*index, policy, 'variable', 'energy'])
                .agg(pl.mean('value') if agg == 'mean' else pl.median('value'))
                .with_columns()
            )
            ratio = (
                unpivot
                .filter(pl.col('energy') != 'EUI합계')
                .group_by([*index, policy, 'variable', 'energy'])
                .agg(pl.sum('value'))
                .with_columns(
                    pl.col('value')
                    / pl.sum('value').over([*index, 'variable', policy]),
                    pl.col('variable').replace_strict({
                        'EUI': 'ratio',
                        'EUIadj': 'ratio_adj',
                    }),
                )
            )

            (
                pl
                .concat([avg, ratio])
                .pivot(
                    policy,
                    index=[*index, 'variable', 'energy'],
                    values='value',
                    sort_columns=True,
                )
                .sort([*index, 'variable', 'energy'])
                .write_excel(wb, worksheet=f'{"-".join(index)}-{agg}')
            )

        agg(['면적구간', '연도'], 'mean')
        agg(['면적구간', '연도', '주체'], 'mean')
        agg(['면적구간', '연도', '지역'], 'mean')
        agg(['면적구간', '연도', '등급'], 'mean')
        agg(['면적구간', '연도', '지역', '등급'], 'mean')


@app['analyze'].command
def analyze_eui(*, conf: Config = DEFAULT_CONFIG):
    data = _analyze_eui(conf=conf)
    data.write_parquet(conf.root / '0201EUIanalysis.parquet')
    data.write_excel(conf.root / '0201EUIanalysis.xlsx')

    index = data.drop(cs.starts_with('EUI'), 'variable').columns
    (
        data
        .unpivot(index=[*index, 'variable'], variable_name='energy')
        .pivot('variable', index=[*index, 'energy'], values='value', sort_columns=True)
        .sort('신청번호', 'energy')
        .write_excel(conf.root / '0201EUIanalysis-pivot.xlsx')
    )

    # 그룹별 정책구간 평균
    _analyze_eui_agg(data=data, conf=conf)

    # 주체/규모별 평균/에너지원별 비율 계산
    app_no = '신청번호'
    energy = ['EUI전기', 'EUI도시가스', 'EUI지역난방']
    group = ['주체', '면적구간', '연도', 'variable']
    joined = (
        data
        .unpivot(energy, index=[app_no, *group], variable_name='energy')
        .with_columns(pl.col('value').fill_null(0))
        .with_columns(pl.col('value').mean().over(group).alias('avg'))
        .with_columns(pl.col('value').sub(pl.col('avg')).alias('delta'))
    )
    dist = joined.group_by([app_no, *group]).agg(
        (pl.col('value') - pl.col('avg')).pow(2).mean().sqrt().alias('distance')
    )
    joined = (
        joined
        .join(dist, on=[app_no, *group], how='left', validate='m:1')
        .sort([app_no, *group])
        .pivot('energy', index=[app_no, *group, 'distance'], values='value')
        .with_columns(pl.col('distance').rank().over(group).alias('distance_rank'))
        .select([app_no, *group, *energy, 'distance', 'distance_rank'])
    )

    joined.write_excel(conf.root / '0201EUI그룹거리.xlsx')


@app['analyze'].command
def analyze_plot_eui_dist(*, conf: Config = DEFAULT_CONFIG):
    data = (
        pl
        .read_parquet(conf.root / '0102EUI.parquet')
        .with_columns(
            pl.col('date').dt.year().alias('year'),
            pl.col('date').dt.month().alias('month'),
        )
        .with_columns()
    )

    utils.mpl.MplTheme('paper', fig_size=(16 * 1.2, None)).grid().apply()
    hue_order = ['전기', '도시가스', '지역난방']
    group_by = ['신청번호', 'year']

    ax: Axes
    for interval in ['year', 'month']:
        fig, axes = plt.subplots(2, 2, sharex='col')

        group_by = (
            ['신청번호', 'year']
            if interval == 'year'
            else ['신청번호', 'year', 'month']
        )

        total = data.group_by(group_by).agg(pl.sum('EUIadj'))
        by_energy = data.group_by([*group_by, 'energy']).agg(pl.sum('EUIadj'))

        for i, df in enumerate([by_energy, total]):
            for log in [0, 1]:
                sns.histplot(
                    df,
                    x='EUIadj',
                    hue=None if i else 'energy',
                    hue_order=hue_order,
                    multiple='stack',
                    log_scale=bool(log),
                    ax=axes[i, log],
                )

        xlabel = (
            f'냉난방도일 보정 {"연간" if interval == "year" else "월간"} EUI [kWh/m²]'
        )
        for ax in axes[1]:
            ax.set_xlabel(xlabel)

        fig.savefig(conf.root / f'plot-EUI-dist-{interval}.png')


@app['analyze'].command
def analyze_outlier_summary(*, conf: Config = DEFAULT_CONFIG):
    """최종평가자료 - 이상치 개수."""
    app_no = '신청번호'
    outlier_rule = (
        pl
        .scan_parquet(conf.root / '0101outlier.parquet')
        .select(app_no, 'year', pl.col('outlier').fill_null([]))
        .rename({'outlier': 'outlier_rule'})
    )
    outlier_dist = (
        pl
        .scan_parquet(conf.root / '0103outlier.parquet')
        .select(app_no, 'year', pl.col('outlier').replace({'normal': None}))
        .rename({'outlier': 'outlier_dist'})
    )

    outlier = (
        outlier_rule
        .join(outlier_dist, on=[app_no, 'year'], how='left', validate='1:1')
        .with_columns(
            pl
            .col('outlier_rule')
            .list.concat('outlier_dist')
            .list.drop_nulls()
            .list.sort()
            .alias('outlier')
        )
        .with_columns(
            pl
            .when(pl.col('outlier').list.len() == 0)
            .then(pl.lit(None))
            .otherwise(pl.col('outlier'))
            .alias('outlier')
        )
        .collect()
    )

    rich.print(outlier)

    (
        outlier
        .group_by('outlier')
        .len()
        .sort(pl.col('outlier').list.get(0))
        .write_excel(conf.root / 'outlier_type.xlsx')
    )
    (
        outlier
        .group_by('year', 'outlier')
        .len()
        .pivot('year', index='outlier', values='len', sort_columns=True)
        .sort(pl.col('outlier').list.get(0))
        .write_excel(conf.root / 'outlier_year_type.xlsx')
    )


@app['analyze'].command
@dc.dataclass
class AnalyzePlotOutlier:
    paper: bool = True
    annual: bool = True

    scale: float = 0.0
    figsize: tuple[float, float] = (0, 0)
    palette: str | None = None
    lw: float | None = None

    conf: Config = dc.field(default_factory=Config)

    def __post_init__(self):
        self.scale = self.scale or (0.55 if self.paper else 1.0)

        if self.figsize == (0, 0):
            self.figsize = (8, 3.6) if self.paper else (16, 9)

        match self.palette, self.paper, self.annual:
            case None, False, _:
                self.palette = 'tol:bright'
            case None, True, True:
                self.palette = 'tol:medium-contrast'
            case None, True, False:
                self.palette = 'tol:muted'

        if self.paper and self.lw is None:
            self.lw = 1.25

    def outliers(self):
        outliers = [
            (2022072471, 'No use in 2023'),  # 2023년
            (2021051416, 'Partial Annual Use'),  # 1월
            (2020010389, 'Identical Monthly Use'),  # 6개월 전력 동일
            (2017010032, 'Tukey Outlier'),  # tukey
            (2017050823, 'EUI < 30 kWh/m²'),  # threshold
        ]

        if not self.annual:
            outliers.pop(0)

        return {x[0]: f'{i + 1}. {x[1]}' for i, x in enumerate(outliers)}

    def data(self, const_multiplier: float = 2.5):
        root = self.conf.root

        app_no = '신청번호'
        outliers = self.outliers()

        area = pl.scan_parquet(root / '0001building.parquet').select(app_no, '연면적')
        return (
            pl
            .scan_parquet(root / '0001energy.parquet')
            .filter(pl.col(app_no).is_in(outliers))
            .group_by(app_no, 'date')
            .agg(pl.sum('value'))
            .join(area, on=app_no, how='left')
            .select(app_no, 'date', pl.col('value').truediv('연면적').alias('EUI'))
            .with_columns(pl.col(app_no).replace_strict(outliers).alias('outlier-type'))
            .with_columns(
                pl
                .when(pl.col('outlier-type').str.contains('Identical'))
                .then(pl.col('EUI') * const_multiplier)
                .otherwise(pl.col('EUI'))
                .alias('EUI')
            )
            .sort('outlier-type', 'date')
            .collect()
        )

    def output(self):
        paper = '-paper' if self.paper else ''
        annual = '-annual' if self.annual else ''
        return self.conf.root / f'plot-outlier-example{paper}{annual}.png'

    def __call__(self):
        data = self.data()

        utils.mpl.MplConciseDate().apply()
        (
            utils.mpl
            .MplTheme(
                self.scale,
                font={'family': 'serif' if self.paper else 'sans-serif'},
                fig_size=self.figsize,
                palette=self.palette,
            )
            .grid(lw=0.5)
            .apply({'legend.fontsize': 'small'})
        )

        fig = Figure()
        ax = fig.add_subplot()
        sns.lineplot(
            data,
            x='date',
            y='EUI',
            hue='outlier-type',
            style='outlier-type',
            ax=ax,
            alpha=0.8,
            lw=self.lw,
        )

        if legend := ax.get_legend():
            legend.set_title('')

        ax.set_yscale('symlog')
        ax.autoscale_view()
        ax.set_xlabel('')
        ax.set_ylabel('Energy Use [kWh/m²]')

        fig.savefig(self.output())


@app['analyze'].command
def analyze_eui_corr(*, year: int = 2023, conf: Config = DEFAULT_CONFIG):
    """규모별 EUI ~ 정책 spearman correlation 분석."""
    year_var = '연도'
    scale = '면적구간'
    policy = '정책구간'

    data = (
        pl
        .scan_parquet(conf.root / '0201EUIanalysis.parquet')
        .filter(
            pl.col('adj') == 'EUIadj',
            pl.col(policy) != 'P5',
            pl.col(year_var) == year,
        )
        .select(
            year_var,
            pl.col(scale).replace_strict({
                'A1': 'A1',
                'A2': 'A1',
                'A3': 'A2',
                'A4': 'A3',
                'A5': 'A3',
            }),
            policy,
            cs.starts_with('EUI'),
        )
        .unpivot(
            cs.starts_with('EUI'),
            index=[year_var, scale, policy],
            variable_name='energy',
        )
        .drop_nulls('value')
        .with_columns(
            pl.col('energy').str.strip_prefix('EUI'),
            pl.col(policy).str.strip_prefix('P').cast(pl.Float64).alias('policy'),
        )
        .collect()
    )

    rich.print(data)

    rich.print(
        data
        .group_by(scale, policy, 'energy')
        .agg(pl.mean('value'))
        .pivot(
            policy,
            index=['energy', scale],
            values='value',
            sort_columns=True,
        )
        .sort(
            pl.col('energy').replace_strict(
                {'합계': 1, '전기': 2, '도시가스': 3, '지역난방': 4},
                return_dtype=pl.Int8,
            ),
            scale,
        )
        .write_excel(conf.root / f'0202EUI-{year=}-pivot.xlsx')
    )

    dfs: list[pl.DataFrame] = []
    for e, s in itertools.product(
        ['합계', '전기', '도시가스', '지역난방'],
        ['A1', 'A2', 'A3'],
    ):
        filtered = data.filter(pl.col(scale) == s, pl.col('energy') == e)

        if not filtered.height:
            continue

        dfs.append(
            pl.from_pandas(
                pg.corr(
                    x=filtered.select('policy').to_series(),
                    y=filtered.select('value').to_series(),
                    method='spearman',
                )
            ).select(pl.lit(e).alias('energy'), pl.lit(s).alias(scale), pl.all())
        )

    corr = pl.concat(dfs)
    rich.print(corr)
    corr.write_excel(conf.root / f'0202EUI-corr-{year=}.xlsx')


@app['analyze'].command
@dc.dataclass
class AnalyzePlotEui:
    year: int = 2023
    paper: bool = True

    _: dc.KW_ONLY

    agg: bool = False
    corr: bool = True

    scale: float = 0.0
    figsize: tuple[float, float] = (0, 0)

    conf: Config = dc.field(default_factory=Config)

    @dc.dataclass
    class Variables:
        year: str = '연도'
        scale: str = '면적구간'
        policy: str = '정책구간'
        energy: dict[str, str] = dc.field(
            default_factory=lambda: {
                '합계': 'Total',
                '전기': 'Electricity',
                '도시가스': 'Gas',
                '지역난방': 'District\nHeating',
            }
        )
        xlabel: str = '정책구간'
        ylabel: str = '에너지 사용량'
        title: str = '규모'

    def __post_init__(self):
        self.scale = self.scale or (0.7 if self.paper else 1.0)
        self.figsize = (
            ((17.5, 3.8) if self.paper else (27, 6))
            if self.figsize == (0, 0)
            else self.figsize
        )

    @functools.cached_property
    def v(self):
        kwargs = (
            {
                'xlabel': 'Policy Section',
                'ylabel': 'Energy Use',
                'title': 'Building Size',
            }
            if self.paper
            else {}
        )
        return self.Variables(**kwargs)  # type: ignore[arg-type]

    def data(self):
        year = self.year
        year_var = self.v.year
        scale = self.v.scale
        policy = self.v.policy

        data = (
            pl
            .scan_parquet(self.conf.root / '0201EUIanalysis.parquet')
            .filter(
                pl.col('variable') == 'EUIadj',
                pl.col(policy) != 'P5',
                pl.col(year_var) == year,
            )
            .select(year_var, scale, policy, cs.starts_with('EUI').fill_null(0))
            .unpivot(
                cs.starts_with('EUI'),
                index=[year_var, scale, policy],
                variable_name='energy',
            )
            .with_columns(pl.col('energy').str.strip_prefix('EUI'))
            .collect()
        )

        if self.agg:
            (
                data
                .group_by(scale, policy, 'energy')
                .agg(
                    pl.mean('value').alias('mean'),
                    pl.median('value').alias('median'),
                    pl.std('value').alias('std'),
                )
                .unpivot(['mean', 'median', 'std'], index=[scale, policy, 'energy'])
                .pivot(
                    policy,
                    index=[scale, 'energy', 'variable'],
                    values='value',
                    sort_columns=True,
                )
                .sort(pl.all())
                .write_excel(self.conf.root / f'0202EUI-{year=}.xlsx')
            )

        return data

    def corr_test(self, data: pl.DataFrame):
        console = rich.get_console()

        energy = pl.col('energy')
        scale = pl.col(self.v.scale)
        policy = pl.col(self.v.policy)

        for s in ['A1', 'A2', 'A3']:
            filtered = (
                data
                .filter(energy == '합계')
                .filter(scale == s)
                .with_columns(
                    policy.str.strip_prefix('P').cast(pl.Int8).alias('policy')
                )
            )
            console.print(f'{s=}')
            console.print(
                pg.corr(
                    x=filtered.select('policy').to_numpy().ravel(),
                    y=filtered.select('value').to_numpy().ravel(),
                    method='spearman',
                )
            )

    def plot(self, data: pl.DataFrame):
        (
            utils.mpl
            .MplTheme(
                self.scale, font={'family': 'serif' if self.paper else 'sans-serif'}
            )
            .grid(lw=0.6, alpha=0.4)
            .apply({
                'lines.solid_capstyle': 'butt',
                'axes.xmargin': 0.01,
                'axes.ymargin': 0.01,
                'legend.labelspacing': 1.2,
            })
        )
        palette = (
            ['#444', *Colormap('tol:muted')([5, 6, 3])]
            if self.paper
            else ['#666', *Colormap('tol:light')([3, 6, 0])]
        )

        if self.paper:
            data = data.with_columns(pl.col('energy').replace_strict(self.v.energy))
            hue_order = list(self.v.energy.values())
        else:
            hue_order = list(self.v.energy.keys())

        grid = (
            sns
            .catplot(
                data,
                x=self.v.policy,
                y='value',
                hue='energy',
                hue_order=hue_order,
                col=self.v.scale,
                col_order=[f'A{x + 1}' for x in range(3)],
                kind='point',
                errorbar='se',
                seed=42,
                order=[f'P{x + 1}' for x in range(4)],
                palette=palette,
                alpha=0.75,
                sharex=False,
                dodge=True,
                lw=1.5,
                markers=['o', '^', 'v', '.'],
                markersize=4,
            )
            .despine(left=True, bottom=True)
            .set_xlabels(self.v.xlabel)
            .set_ylabels(f'{self.v.ylabel} [kWh/m²]')
            .set_titles(f'{self.v.title} {{col_name}}')
        )

        for ax, idx in zip(grid.axes.flat, 'abc', strict=True):
            ax.set_title(f'{idx}) {ax.get_title()}', weight=600, y=-0.65)

        if grid.legend is not None:
            grid.legend.remove()

        grid.add_legend(title='', loc='center left', bbox_to_anchor=(1, 0.5))
        grid.figure.set_size_inches(self.figsize[0] / 2.54, self.figsize[1] / 2.54)
        ConstrainedLayoutEngine().execute(grid.figure)

        year = self.year
        paper = '-paper' if self.paper else ''
        grid.savefig(self.conf.root / f'plot-EUI-{year=}{paper}.png')

    def __call__(self):
        data = self.data()

        if self.corr:
            self.corr_test(data)

        self.plot(data)


@app['analyze'].command
@dc.dataclass
class AnalyzePlotDegreeday:
    paper: bool = True

    scale: float = 0.0
    figsize: tuple[float, float] = (0, 0)
    palette: str | None = None

    conf: Config = dc.field(default_factory=Config)

    def __post_init__(self):
        if self.figsize == (0, 0):
            self.figsize = (8, 3) if self.paper else (22, 8)

        self.scale = self.scale or (0.55 if self.paper else 1.0)
        self.palette = self.palette or ('tol:muted' if self.paper else 'tol:vibrant')

    def data(self):
        region = {
            '파주': 'Paju',
            '서울': 'Seoul',
            '부산': 'Busan',
            '제주': 'Jeju',
        }
        return (
            pl
            .scan_parquet(self.conf.root / '0001degree_day.parquet')
            .filter(pl.col('region').is_in(region))
            .select('region', 'date', 'HCDD')
            .sort(
                pl.col('region').replace_strict(
                    {x: i for i, x in enumerate(region)}, return_dtype=pl.Int8
                )
            )
            .with_columns(
                pl.col('region').replace(region if self.paper else {}),
                month=pl.col('date').dt.replace(year=2000)
                if self.paper
                else pl.col('date').dt.month(),
            )
            .collect()
        )

    def __call__(self):
        paper = self.paper
        (
            utils.mpl
            .MplTheme(
                self.scale,
                font={'family': 'serif' if self.paper else 'sans-serif'},
                palette=self.palette,
                fig_size=self.figsize,
            )
            .grid(lw=0.6, alpha=0.2)
            .apply()
        )
        data = self.data()

        fig = Figure()
        ax = fig.add_subplot()
        ax.set_xmargin(0)

        sns.lineplot(
            data,
            x='month',
            y='HCDD',
            hue='region',
            style='region' if paper else None,
            errorbar='se',
            ax=ax,
            lw=1,
        )
        ax.set_ylim(0)
        ax.set_xlabel('')
        ax.set_ylabel(
            '{} [°C·day]'.format('Degree Days' if paper else '월간 냉난방도일')
        )

        if paper:
            ax.xaxis.set_major_locator(mdates.MonthLocator())
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%b'))
        else:
            ax.xaxis.set_major_locator(MaxNLocator(12, steps=[1]))
            ax.xaxis.set_major_formatter(StrMethodFormatter('{x:.0f}월'))

        legend = ax.get_legend()
        assert legend
        legend.set_title('')

        root = self.conf.root
        name = f'plot-degree-day-{"paper" if paper else "notebook"}'
        fig.savefig(root / f'{name}-legend.png')

        legend.remove()
        fig.savefig(root / f'{name}.png')


@app['cpr'].command
def cpr(*, plot: bool = True, paper: bool = True, conf: Config = DEFAULT_CONFIG):
    plot_dir = conf.root / 'CPR'
    if plot:
        plot_dir.mkdir(exist_ok=True)

    cpr = CPR(paper=paper, conf=conf)
    models = list(cpr.cpr_zeb_batch(plot_dir=plot_dir if plot else None))
    pl.concat(models).write_excel(conf.root / '0301CPR.xlsx')


@app['cpr'].command
def cpr_compare(*, conf: Config = DEFAULT_CONFIG):
    save_dir = conf.root / 'CPR비교'
    save_dir.mkdir(exist_ok=True)

    utils.mpl.MplTheme(fig_size=(12, 9)).grid().apply()
    cpr = CPR(conf=conf)
    cpr.cpr_compare(save_dir=save_dir)


if __name__ == '__main__':
    utils.terminal.LogHandler.set()
    utils.mpl.MplTheme().grid().apply()

    app()
