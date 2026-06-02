"""인증 데이터 탐색적 분석 스크립트."""

import dataclasses as dc
import enum
import itertools
import warnings
from collections.abc import Sequence  # noqa: TC003
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

import cyclopts
import matplotlib.pyplot as plt
import more_itertools as mi
import pingouin as pg
import polars as pl
import polars.selectors as cs
import rich
import seaborn as sns
import xlsxwriter
from cmap import Colormap
from loguru import logger
from matplotlib.figure import Figure
from tqdm.rich import tqdm

from zeb import utils
from zeb.plot import boxstrip
from zeb.utils.cli import App

if TYPE_CHECKING:
    from matplotlib.axes import Axes

Plot = Literal['violin', 'box', 'boxstrip']

_DEFAULT_PATH = Path('[DEFAULT]')


class BuildingType(enum.StrEnum):
    RESIDENTIAL = '주거용'
    NON_RESIDENTIAL = '주거용이외'

    R = RESIDENTIAL
    NR = NON_RESIDENTIAL


class ResidentialType(enum.StrEnum):
    ALL = 'all'

    SINGLE = '단독주택'
    S = SINGLE

    MULTI = '공동주택'
    M = MULTI

    @property
    def suffix(self):
        if self is ResidentialType.ALL:
            return ''

        return f'({self})'


@dc.dataclass
class _Parser:
    source: str | Path
    building_type: BuildingType

    groups: Sequence[str] = (
        '용도',
        '지역구분',
        '단지분류',
        '전용면적 분류',
        '규모분류',
        '등급',
        '주체',
    )
    values: Sequence[str] = (
        '벽체_열관류율',
        '지붕_열관류율',
        '바닥_열관류율',
        '창호_열관류율',
        '평균조명에너지부하율',
        '설치용량_세대',
        '모듈면적비_전용면적',
        '모듈면적비',
        '지열_난방_효율',
        '지열_냉방_효율',
        '전열교환기_열회수효율_난방',
        '전열교환기_열회수효율_냉방',
    )
    equipment_groups: Sequence[str] = ('난방설비', '냉방설비', '급탕설비')
    equipment_values: Sequence[str] = (
        '온열원설비_효율',
        '냉열원설비_효율',
        '급탕설비_효율',
        '전열교환기_열회수효율_난방',
        '전열교환기_열회수효율_냉방',
    )

    data: pl.DataFrame = dc.field(init=False)

    def __post_init__(self):
        variables = {
            *self.groups,
            *self.values,
            *self.equipment_groups,
            *self.equipment_values,
        }
        cols = pl.read_excel(
            self.source,
            sheet_name=self.building_type,
            read_options={'n_rows': 1},
        ).columns
        cols = [x for x in cols if x in variables]  # 순서 보존

        cols_set = set(cols)
        self.groups = [x for x in self.groups if x in cols_set]
        self.values = [x for x in self.values if x in cols_set]

        values = (set(self.values) | set(self.equipment_values)) & cols_set

        self.data = (
            pl
            .read_excel(self.source, sheet_name=self.building_type, columns=cols)
            .with_columns(cs.string().replace({'': None}))
            .with_columns(cs.contains('율').cast(pl.Float64, strict=False))
            .with_columns(pl.col(values).replace({0: None}))
        )


@dc.dataclass
class _Describe(_Parser):
    percentiles: Sequence[float] = (0.1, 0.25, 0.5, 0.75, 0.9)

    def __post_init__(self):
        self.data = pl.read_parquet(self.source)

        cols = set(self.data.columns)
        self.groups = [x for x in self.groups if x in cols]
        self.values = [x for x in self.values if x in cols]
        self.equipment_groups = [x for x in self.equipment_groups if x in cols]
        self.equipment_values = [x for x in self.equipment_values if x in cols]

    def _data(self, columns: Sequence[str]):
        return self.data.select(columns).filter(~pl.all_horizontal(pl.all().is_null()))

    def desc_values(self):
        def it():
            for group in self.groups:
                yield (
                    utils.pl
                    .PolarsSummary(
                        self._data([group, *self.values]),
                        group=group,
                        percentiles=self.percentiles,
                    )
                    .describe()
                    .select(pl.lit(group).alias('group'), pl.all())
                    .rename({f'group:{group}': 'group value'})
                )

        return pl.concat(it())

    def desc_equipment(self):
        groups = [x for x in ['단지분류', '규모분류', '등급'] if x in self.data.columns]
        groups_powerset = list(mi.powerset(groups))

        def describe(groups: Sequence[str], equipment: str):
            group_type = f'설비+{"+".join(groups)}' if groups else '설비'
            return (
                utils.pl
                .PolarsSummary(
                    self._data([*groups, equipment, *self.equipment_values]),
                    group=[*groups, equipment],
                    percentiles=self.percentiles,
                )
                .describe()
                .select(pl.lit(group_type).alias('group'), pl.all())
            )

        for eq in self.equipment_groups:
            desc = pl.concat((describe(g, eq) for g in groups_powerset), how='diagonal')
            desc = desc.select(cs.starts_with('group'), ~cs.starts_with('group'))
            yield eq, desc

    def desc_transmittance(self, group: Sequence[str]):
        values = [x for x in self.values if '열관류율' in x]
        group = [x for x in group if x in self.groups]
        desc = utils.pl.PolarsSummary(
            self._data([*group, *values]),
            group=group,
            percentiles=self.percentiles,
        ).describe()
        return group, desc

    def describe(self, path: str | Path, column_widths: int = 100):
        with xlsxwriter.Workbook(path) as wb:
            self.desc_values().write_excel(wb, column_widths=column_widths)

            for eq, desc in self.desc_equipment():
                desc.write_excel(wb, worksheet=eq, column_widths=column_widths)

            for group in [
                ['등급'],
                ['지역구분'],
                ['지역구분', '등급'],
                ['등급', '규모분류', '단지분류'],
            ]:
                g, desc = self.desc_transmittance(group)
                sheet = f'열관류율 {"-".join(g)}'
                desc.write_excel(wb, worksheet=sheet, column_widths=column_widths)


app = App(
    config=cyclopts.config.Toml(
        'config.toml', root_keys='eda', allow_unknown=True, use_commands_as_keys=False
    )
)


@app.command
def parse(
    output: Path | None = None,
    root: Path = _DEFAULT_PATH,
    source: Path = _DEFAULT_PATH,
):
    output = output or root
    console = rich.get_console()

    for kind in BuildingType:
        parser = _Parser(root / source, building_type=kind)
        console.print(kind, parser.data.glimpse(return_as_string=True))

        parser.data.write_parquet(output / f'0000.{kind}.parquet')
        parser.data.write_excel(output / f'0000.{kind}.xlsx')


@app.command
def describe(
    output: Path | None = None,
    root: Path = _DEFAULT_PATH,
):
    output = output or root

    for kind in BuildingType:
        desc = _Describe(root / f'0000.{kind}.parquet', building_type=kind)
        desc.describe(output / f'0001.{kind}.xlsx')
        utils.pl.PolarsSummary(desc.data, percentiles=desc.percentiles).write_excel(
            output / f'0002.{kind}-describe.xlsx'
        )


@dc.dataclass
class _Anova:
    root: Path
    building_type: BuildingType
    residential_type: ResidentialType = ResidentialType.ALL

    groups: tuple[str, ...] = ('등급', '규모분류')
    equipment: tuple[str, ...] = ('난방설비', '냉방설비', '급탕설비')

    data: pl.DataFrame = dc.field(init=False)
    values: list[str] = dc.field(init=False)

    def __post_init__(self):
        data = pl.read_parquet(self.root / f'0000.{self.building_type}.parquet')

        if (
            self.building_type is BuildingType.RESIDENTIAL
            and self.residential_type is not ResidentialType.ALL
        ):
            data = data.filter(pl.col('용도') == self.residential_type)

        self.data = data
        self.values = [x for x in self.data.columns if '효율' in x]

    def anova(self, groups: Sequence[str], equipment: str):
        data = (
            self.data
            .with_columns(pl.concat_str(groups, separator='/').alias('group'))
            .unpivot(
                self.values, index=['group', equipment], variable_name='efficiency'
            )
            .with_columns()
        )

        def it():
            for by, df in data.group_by([equipment, 'efficiency']):
                anova = pl.from_pandas(
                    pg.anova(df.to_pandas(), dv='value', between='group')
                )
                yield anova.select(
                    pl.lit(by[0]).alias('equipment2'),
                    pl.lit(by[1]).alias('efficiency'),
                    pl.all(),
                )

        return pl.concat(it(), how='diagonal_relaxed')

    def _it(self):
        for groups, equipment in itertools.product(
            mi.powerset(self.groups), self.equipment
        ):
            if not groups:
                continue

            yield (
                self
                .anova(groups, equipment)
                .select(
                    pl.lit('/'.join(groups)).alias('between'),
                    pl.lit(equipment).alias('equipment'),
                    pl.all(),
                )
                .with_columns()
            )

    def __call__(self):
        data = pl.concat(self._it(), how='diagonal').sort(pl.all())
        data.write_excel(
            self.root
            / f'0100.ANOVA {self.building_type}{self.residential_type.suffix}.xlsx',
            column_widths=120,
        )


@app.command
def anova(
    kind: BuildingType,
    residential: ResidentialType = ResidentialType.ALL,
    root: Path = _DEFAULT_PATH,
):
    groups = (
        ('등급', '단지분류')
        if kind is BuildingType.RESIDENTIAL
        else ('등급', '규모분류')
    )

    warnings.filterwarnings(
        'ignore', message='invalid value encountered in scalar divide'
    )
    warnings.filterwarnings(
        'ignore', message='divide by zero encountered in scalar divide'
    )
    _Anova(root=root, building_type=kind, residential_type=residential, groups=groups)()


@dc.dataclass
class _Plotter:
    root: Path
    building_type: BuildingType
    residential_type: ResidentialType = ResidentialType.ALL

    groups: Sequence[str] = ('등급', '규모', '단지 분류', '전용면적 분류')
    equipment: Sequence[str] = ('난방설비', '냉방설비', '급탕설비')

    col_wrap: bool = False
    remap_equipment: bool = True
    draw_threshold: int = 2

    plot: Sequence[Plot] = ('boxstrip', 'box')
    direction: Sequence[Literal['v', 'h']] = ('h',)

    equipment_mapping: dict[str, str] = dc.field(
        default_factory=lambda: {
            '압축식(전기)': 'EHP',
            '압축식(LNG)': 'GHP',
            '압축식(LPG)': 'GHP',
            '가스보일러': '가스보일러',
            '가스보일러(LNG)': '가스보일러',
            '흡수식(지역난방)': '흡수식(지역난방)',
            '흡수식(LNG)': '흡수식(LNG)',
        }
    )

    data: pl.DataFrame = dc.field(init=False)
    values: list[str] = dc.field(init=False)

    def __post_init__(self):
        rename = {
            '규모분류': '규모',
            '단지분류': '단지 분류',
            '모듈면적비_전용면적': '모듈면적비',
        }

        data = (
            pl
            .scan_parquet(self.root / f'0000.{self.building_type}.parquet')
            .with_row_index()
            .rename(rename, strict=False)
            .collect()
        )
        self.values = [x for x in data.columns if '효율' in x]
        self.groups = tuple(x for x in self.groups if x in data.columns)
        index = [x for x in ['index', '모듈면적비', '용도'] if x in data.columns]

        data = (
            data
            .unpivot(
                self.values,
                index=[*index, *self.groups, *self.equipment],
                variable_name='efficiency',
            )
            .with_columns(
                pl
                .col('efficiency')
                .str.replace_all('_', ' ')
                .str.replace_many(
                    ['열회수효율 난방', '열회수효율 냉방'],
                    ['열회수효율 (난방)', '열회수효율 (냉방)'],
                )
            )
            .unpivot(
                self.equipment,
                index=[*index, *self.groups, 'efficiency', 'value'],
                variable_name='eq1',
                value_name='eq2',
            )
            .drop_nulls('eq2')
        )

        if (
            self.building_type is BuildingType.RESIDENTIAL
            and self.residential_type is not ResidentialType.ALL
        ):
            data = data.filter(pl.col('용도') == self.residential_type)

        if self.remap_equipment:
            data = data.with_columns(
                pl.col('eq2').replace_strict(self.equipment_mapping, default=None)
            ).drop_nulls('eq2')

        self.data = data

    def iter_data(self):
        for (eq, eff), df in self.data.group_by('eq1', 'efficiency'):
            if '전열교환기' in eff:
                continue
            if eq in {'급탕설비', '난방설비'} and '냉' in eff:
                continue
            if eq == '냉방설비' and any(x in eff for x in ['급탕', '온열', '난방']):
                continue

            yield (eq, eff), df

    @property
    def name(self):
        if self.building_type is BuildingType.NON_RESIDENTIAL:
            return str(self.building_type)

        return f'{self.building_type}{self.residential_type.suffix}'

    @staticmethod
    def _plot_dist(
        between: str,
        order: Sequence[str] | None = None,
        kind: Plot = 'violin',
        direction: Literal['h', 'v'] = 'h',
    ):
        match kind:
            case 'violin':
                fn = sns.violinplot
                kwargs = {
                    'split': True,
                    'linewidth': 0.75,
                    'linecolor': (0, 0, 0, 0.5),
                }
            case 'box':
                fn = sns.boxplot
                kwargs = {
                    'flierprops': {
                        'alpha': 0.75,
                        'markeredgecolor': '#9E9E9E',
                        'markeredgewidth': 0.75,
                        'markersize': 5,
                        'zorder': 0,
                    }
                }
            case 'boxstrip':
                fn = boxstrip
                kwargs = {'strip': {'size': 3, 'alpha': 0.5}}

        xy = (
            {'x': 'value', 'y': between}
            if direction == 'h'
            else {'y': 'value', 'x': between}
        )

        return {'func': fn, **xy, 'order': order, **kwargs}

    def _plot_eq(self, data: pl.DataFrame, efficiency_label: str = '효율'):
        for between, kind, direction in itertools.product(
            self.groups, self.plot, self.direction
        ):
            if kind == 'violin' and direction == 'v':
                continue

            d = (
                data.with_columns(pl.col(between).str.strip_suffix('등급'))
                if direction == 'v'
                else data
            )
            d = (
                (d)
                .with_columns(count=pl.count('value').over([between, 'eq2']))
                .filter(pl.col('count') > self.draw_threshold)
                .sort(
                    -pl
                    .col(between)
                    .str.count_matches('+', literal=True)
                    .cast(pl.Int16),
                    between,
                )
            )

            labels = [efficiency_label, '' if between == '등급' else None]
            if direction == 'v':
                labels.reverse()

            eq2_order = data['eq2'].unique().sort()
            col_wrap = int(utils.mpl.ColWrap(len(eq2_order))) if self.col_wrap else None
            plot = self._plot_dist(between=between, kind=kind, direction=direction)

            try:
                grid = (
                    sns
                    .FacetGrid(
                        d,
                        col='eq2',
                        col_order=eq2_order,
                        col_wrap=col_wrap,
                        sharex=False,
                        sharey=False,
                        despine=False,
                    )
                    .map_dataframe(**plot)
                    .set_titles('')
                    .set_titles('{col_name}', loc='left', weight=500)
                    .set_axis_labels(*labels)
                )
            except ValueError as e:
                logger.warning(e)
                continue
            else:
                yield (between, kind, direction), grid

    def plot_eq(self):
        output = self.root / f'0201.{self.name} 설비 효율 분포'
        output.mkdir(exist_ok=True)

        for (eq1, efficiency), data in self.iter_data():
            for (between, kind, direction), grid in self._plot_eq(data, efficiency):
                name = f'{kind}-{direction}-{between}-{eq1}-{efficiency}.png'
                logger.info(name)

                grid.savefig(output / name)
                plt.close('all')

    def plot_pv(self):
        output = self.root / f'0201.{self.name} 기타'
        output.mkdir(exist_ok=True)

        data = self.data.unique('index')
        color = sns.color_palette(n_colors=1)[0]

        for between in self.groups:
            d = (
                (data)
                .with_columns(count=pl.count('모듈면적비').over(between))
                .filter(pl.col('count') > self.draw_threshold)
            )
            order: list[str] = d[between].unique().to_list()
            order = sorted(order, key=lambda x: (-x.count('+'), x))

            fig = Figure()
            ax = fig.subplots()

            sns.boxplot(
                d,
                x='모듈면적비',
                y=between,
                order=order,
                ax=ax,
                flierprops={
                    'alpha': 0.75,
                    'markeredgewidth': 0.5,
                    'markersize': 4,
                    'zorder': 0,
                },
                color=color,
            )

            fig.savefig(output / f'{between}.png')


@app.command
def plot(
    kind: BuildingType,
    residential: ResidentialType = ResidentialType.ALL,
    root: Path = _DEFAULT_PATH,
    *,
    plot: Sequence[Literal['eq', 'pv']] = ('eq', 'pv'),
):
    if not plot:
        raise ValueError

    warnings.filterwarnings('ignore', message='The figure layout has changed to tight')
    plotter = _Plotter(root, building_type=kind, residential_type=residential)

    if 'eq' in plot:
        plotter.plot_eq()
    if 'pv' in plot:
        plotter.plot_pv()


@dc.dataclass
class _Plotter2:
    bldg: BuildingType
    root: Path = _DEFAULT_PATH
    figsize: tuple[float, float] = (13.5, 11)

    draw_threshold: int = 2

    def __post_init__(self):
        self.figsize = (self.figsize[0] / 2.56, self.figsize[1] / 2.56)

    @property
    def output(self):
        return self.root / f'0201.{self.bldg} 기타'

    @property
    def data(self):
        return pl.scan_parquet(self.root / f'0000.{self.bldg}.parquet').rename(
            {
                '규모분류': '규모',
                '단지분류': '규모',
                '모듈면적비_전용면적': '모듈면적비',
            },
            strict=False,
        )

    def prep(self, var: Literal['light', 'pv', 'he', 'gshp', 'gas-boiler']):
        match var:
            case 'light':
                data = self.data.rename({'평균조명에너지부하율': 'value'})
            case 'pv':
                data = self.data.rename({'모듈면적비': 'value'})
            case 'he' | 'gshp':
                on = (
                    ['전열교환기_열회수효율_난방', '전열교환기_열회수효율_냉방']
                    if var == 'he'
                    else ['지열_난방_효율', '지열_냉방_효율']
                )
                index = ['규모', '등급']
                data = self.data.unpivot(on, index=index).with_columns(
                    pl.col('variable').str.extract('((냉|난)방)').alias('hue')
                )
            case 'gas-boiler':
                data = (
                    self.data
                    .filter(pl.col('난방설비').str.starts_with('가스보일러'))
                    .rename({'온열원설비_효율': 'value'})
                    .filter(pl.col('value') > 0.6)  # noqa: PLR2004 # NOTE ㅋ
                )
                assert (
                    data
                    .select(pl.col('급탕설비').str.starts_with('가스보일러').all())
                    .collect()
                    .item()
                )

        return data.drop_nulls('value').collect()

    def plot(self, data: pl.DataFrame, xlabel: str):
        fig = Figure()
        axes = fig.subplots(2, 1, sharex=True, sharey=False)

        if 'hue' not in data.columns:
            hue = {}
        else:
            hue = {
                'hue': 'hue',
                'hue_order': ['난방', '냉방'],
                'palette': ['#EE8866', '#99DDFF'],
            }

        ax: Axes
        for between, ax in zip(['규모', '등급'], axes, strict=True):
            d = (
                (data)
                .with_columns(count=pl.count('value').over(between))
                .filter(pl.col('count') > self.draw_threshold)
            )
            order = sorted(
                d[between].unique().to_list(), key=lambda x: (-x.count('+'), x)
            )
            if hue:
                sns.boxplot(
                    d,
                    x='value',
                    y=between,
                    order=order,
                    ax=ax,
                    flierprops={
                        'alpha': 0.5,
                        'markeredgecolor': '#9E9E9E',
                        'markeredgewidth': 0.75,
                        'markersize': 5,
                        'zorder': 0,
                    },
                    **hue,
                )
            else:
                boxstrip(
                    d.to_pandas(),
                    x='value',
                    y=between,
                    order=order,
                    ax=ax,
                    strip={'size': 2, 'alpha': 0.75},
                )

            ax.set_xlabel(xlabel)
            ax.set_ylabel(between if between == '규모' else '')

            if hue:
                ax.legend(title=None)

        fig.set_size_inches(self.figsize)
        return fig

    def __call__(self):
        self.output.mkdir(exist_ok=True)

        for v, xlabel in [
            ['light', '평균조명에너지부하율 [W/m²]'],
            ['pv', '모듈면적비'],
            ['he', '전열교환기 효율'],
            ['gshp', '지열 히트펌프 COP'],
            ['gas-boiler', '가스보일러 효율'],
        ]:
            if self.bldg is BuildingType.NON_RESIDENTIAL and v == 'gas-boiler':
                continue

            data = self.prep(v)  # type: ignore[arg-type]
            fig = self.plot(data=data, xlabel=xlabel)
            fig.savefig(self.output / f'{v}.png')


@app.command
def plot2(plotter: Annotated[_Plotter2, cyclopts.Parameter('*')]):
    plotter()


@app.command
def pv(root: Path, percentiles: tuple[float, ...] = (0.1, 0.25, 0.50, 0.75, 0.9)):
    """규모, 등급별 PV 모듈면적비."""
    value = '모듈면적비'

    with xlsxwriter.Workbook(root / '모듈면적비 describe.xlsx') as wb:
        for kind in BuildingType:
            data = (
                pl
                .scan_parquet(root / f'0000.{kind}.parquet')
                .rename(
                    {
                        '모듈면적비_전용면적': value,
                        '단지분류': '규모',
                        '규모분류': '규모',
                    },
                    strict=False,
                )
                .select('등급', '규모', value)
                .collect()
            )

            for groups in mi.powerset(['등급', '규모']):
                if not groups:
                    continue

                (
                    utils.pl
                    .PolarsSummary(
                        data.select([*groups, value]),
                        group=groups,
                        percentiles=percentiles,
                    )
                    .describe()
                    .write_excel(wb, worksheet=f'{kind}-{"-".join(groups)}')
                )


@app.command
def area_error(src: Path, dst: Path | None = None):
    """연면적 추출 시 오류 케이스 정리."""
    from eco2.editor import Eco2Xml  # noqa: PLC0415

    dst = dst or src.parent / 'AREA-ERROR.txt'

    with dst.open('w') as f:
        for s in tqdm(tuple(src.glob('*'))):
            if s.suffix not in {'.tpl', '.tplx'}:
                continue

            area = Eco2Xml.read(s).area
            if area.floor is None:
                f.write(f'{s.stem} | {area.raw["floor"]} | {area=}\n')


@app.command
def uvalue(scale: float = 0.9, root: Path = _DEFAULT_PATH):
    """2025-09-09 발표자료용 부위별 열관류율."""
    data = (
        pl
        .read_excel(root / '1000.열관류율 분석 결과.xlsx')
        .rename({'법적기준(현행)': '법적기준'})
        .unpivot(index=['용도', '부위', '지역'])
    )

    output = root / '1000.uvalue'
    output.mkdir(exist_ok=True)

    (
        utils.mpl
        .MplTheme(scale, palette='tol:bright', fig_size=(9, 6.5))
        .grid()
        .apply({'axes.ymargin': 0.1})
    )

    for (use, part), df in data.group_by(['용도', '부위'], maintain_order=True):
        fig = Figure()
        ax = fig.subplots()
        sns.pointplot(
            df, x='variable', y='value', hue='지역', ax=ax, alpha=0.8, legend=False
        )
        ax.set_ylim(0)
        ax.set_xlabel('')
        ax.set_ylabel('')
        ax.set_title(f'{part} 열관류율 [W/m²K]', weight=500)
        fig.savefig(output / f'{use=!s}-{part=!s}.png')

    # legend
    fig = Figure()
    ax = fig.subplots()
    sns.pointplot(data, x='variable', y='value', hue='지역', ax=ax, alpha=0.8)
    ax.legend(title=None, ncols=4, loc='lower center', bbox_to_anchor=(0.5, 1))
    fig.set_size_inches(8, 4.5)
    fig.savefig(output / 'legend.png')


if __name__ == '__main__':
    utils.terminal.LogHandler.set()

    colors = Colormap('tol:light').color_stops.colors
    palette: list = [colors[x] for x in [2, 1, 0, 3, 4, 5, 6]]
    utils.mpl.MplTheme(palette=palette).grid().apply({'lines.solid_capstyle': 'butt'})

    app()
