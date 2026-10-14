"""2026-10-12 중간보고용 분석."""

import functools
import itertools
import shutil
from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING, ClassVar, Literal

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import seaborn as sns
import structlog
from cmap import Colormap
from cyclopts.config import Toml
from matplotlib import patheffects
from matplotlib.figure import Figure

from zeb import utils
from zeb.utils.cli import App
from zeb.y2026.common import (
    INDEX,
    PURPOSE,
    Electrification,
    Grade,
    Owner,
    Purpose,
    Region,
)
from zeb.y2026.config import Paths  # ruff:ignore[typing-only-first-party-import]

if TYPE_CHECKING:
    from matplotlib.axes import Axes


SCOPE = Literal['직접', '간접', '직간접']

app = App(
    config=Toml(
        'env.toml',
        root_keys='2026',
        allow_unknown=True,
        use_commands_as_keys=False,
    )
)
logger = structlog.stdlib.get_logger()


def _describe(name: str = 'emission/gfa'):
    c = pl.col(name)
    return [
        c.len().name.suffix(':len'),
        c.count().name.suffix(':count'),
        c.null_count().name.suffix(':null_count'),
        c.mean().name.suffix(':mean'),
        c.std().name.suffix(':std'),
        c.min().name.suffix(':min'),
        c.quantile(0.25, interpolation='linear').name.suffix(':Q1'),
        c.median().name.suffix(':median'),
        c.quantile(0.75, interpolation='linear').name.suffix(':Q3'),
        c.max().name.suffix(':max'),
    ]


@app.command
def check_heat_generation(paths: Paths):
    pl.Config.set_tbl_cols(50)
    # NOTE: ECO2 열 배출계수 0
    return (
        pl
        .scan_parquet(paths.eco2.root / '02.emission.parquet')
        .filter(
            pl.col('scope') == 'generation.heat',
            pl.col('reference') == 'ECO2',
            pl.col('delivered') != 0,
        )
        .collect()
    )


@dataclass
class _Command:
    paths: Paths

    _: KW_ONLY

    outlier: bool = False
    figsize: tuple[float, float] = (25, 9.5)

    PREFIX: ClassVar[str] = ''
    OUTLIERS: ClassVar[tuple[str, ...]] = ('공.A2.N.교.2',)

    EXCLUDE_GRADE: ClassVar[tuple[Grade, ...]] = (Grade.NOPV, Grade.EXST)
    EXCLUDE_PURPOSE: ClassVar[tuple[Purpose, ...]] = ('단독주택',)

    def __post_init__(self):
        logger.info(self.__class__.__name__, outlier=self.outlier)

    @property
    def _raw(self):
        return (
            pl
            .scan_parquet(self.paths.eco2.root / '02.emission.parquet')
            .filter(pl.col('reference') == 'ECO2')
            .with_columns()
        )

    @property
    def emission(self):
        if self.outlier:
            raw = self._raw
        else:
            raw = self._raw.filter(pl.col('bldg').is_in(self.OUTLIERS).not_())

        grade = {
            Grade.EXST: '기존',
            Grade.BASE: 'Base',
            Grade.SUB5: '준ZEB5',
            Grade.ZEB5: 'ZEB5',
            Grade.ZEB4: 'ZEB4',
            Grade.ZEB3: 'ZEB3',
            Grade.ZEB2: 'ZEB2',
            Grade.ZEB1: 'ZEB1',
            Grade.ZEBP: 'ZEB+',
        }
        return (
            raw
            .filter(
                pl.col('grade').is_in(self.EXCLUDE_GRADE).not_(),
                pl.col('purpose').is_in(self.EXCLUDE_PURPOSE).not_(),
            )
            .with_columns(
                pl
                .col('scope')
                .replace_strict({
                    'direct': '직접',
                    'indirect': '간접',
                    # NOTE: 지열 열 생산량은 직접 배출량에서 제외
                    'generation.heat': '직접',
                    'generation.elec': '간접',
                })
                .cast(pl.Enum(['간접', '직접'])),
            )
            .group_by(*INDEX.CASE, 'scope')
            .agg(pl.sum('delivered', 'emission'))
            .with_columns(
                pl
                .col('grade')
                .replace_strict(grade)
                .cast(pl.Enum(list(grade.values())))
                .alias('grade.kor'),
                pl
                .col('delivered')
                .truediv(pl.col('연면적'))
                .alias('delivered/gfa'),  # kWh/m²
                pl
                .col('emission')
                .truediv(pl.col('연면적'))
                .alias('emission/gfa'),  # kg/m²
            )
        )

    @property
    def output(self):
        d = (
            self.paths.eco2.report / self.PREFIX
            if self.PREFIX
            else self.paths.eco2.report
        )
        d.mkdir(exist_ok=True)
        return d

    @property
    def _suffix(self):
        return '' if self.outlier else ' NoOutlier'


@app.command
@dataclass
class Baseline(_Command):
    PREFIX: ClassVar[str] = '01.Base배출량'

    @functools.cached_property
    def data(self):
        return (
            self.emission
            .filter(pl.col('grade') == Grade.BASE)
            .with_columns(
                pl
                .when(pl.col('scale.c').is_null())
                .then(pl.col('scale.a'))
                .otherwise(pl.col('scale.c'))
                .alias('scale'),
            )
            .collect()
        )

    def plot(self, elec: Electrification, y: Literal['scale', 'owner']):
        data = (
            self.data
            .filter(pl.col('electrification') == elec)
            .with_columns(
                pl.format('{} {}', 'purpose', 'scale').alias('purpose-scale'),
                pl.format('{} {}', 'purpose', 'owner').alias('purpose-owner'),
            )
            .sort('purpose', 'scale')
        )

        with plt.rc_context({'ytick.left': False, 'ytick.right': False}):
            fig = Figure(figsize=(*self.figsize, 'cm'))
            axes = fig.subplots(1, 2, sharex=True)

        ax: Axes
        for ax, scope in zip(axes, ('직접', '간접'), strict=True):
            sns.barplot(
                data.filter(pl.col('scope') == scope).to_dict(),
                x='emission/gfa',
                y=f'purpose-{y}',
                hue='purpose',
                ax=ax,
                dodge=False,
                legend=False,
                linewidth=0,
                capsize=0.25,
                err_kws={'linewidth': 1},
            )
            ax.set_title(f'{scope} 배출량')
            ax.set_ylabel('')
            ax.set_xlabel('탄소 배출량 [kg/m²yr]')

        path = self.output / f'{self.PREFIX}-{elec}-{y}{self._suffix}.svg'
        fig.savefig(path)
        fig.savefig(path.with_suffix('.png'))

    def __call__(self):
        for y in ('scale', 'owner'):
            self.plot('기존', y=y)
            self.plot('전환', y=y)

        for g in (('owner', 'scale'), ('owner',), ('scale',)):
            (
                self.data
                .group_by('electrification', 'purpose', *g, 'scope')
                .agg(_describe())
                .sort(pl.all())
                .write_csv(
                    self.output / f'{self.PREFIX}-stat-{"-".join(g)}{self._suffix}.csv',
                    include_bom=True,
                )
            )


@app.command
@dataclass
class ByGrade(_Command):
    figsize: tuple[float, float] = (13.5, 11.5)

    PREFIX: ClassVar[str] = '02.등급별배출량'

    @functools.cached_property
    def data(self):
        data = (
            self.emission
            .with_columns(
                pl
                .col('emission/gfa')
                .filter(pl.col('grade') == Grade.BASE)
                .sum()
                .over([*INDEX.BUILDING, 'scope'])
                .alias('emission.baseline'),
            )
            .with_columns(
                (pl.col('emission/gfa') - pl.col('emission.baseline'))  # fmt
                .alias('emission.delta')
            )
            .collect()
        )

        assert (
            data
            .filter(pl.col('grade') == Grade.BASE)
            .select((pl.col('emission/gfa') - pl.col('emission.baseline')).eq(0).all())
            .item()
        )

        return data

    def plot(
        self,
        elec: Electrification,
        value: Literal['value', 'delta'],
        owner: Owner | None = None,
    ):
        existing = elec == '기존'
        delta = value == 'delta'

        grades = ['준ZEB5', 'ZEB5', 'ZEB4', 'ZEB3', 'ZEB2', 'ZEB1', 'ZEB+']
        data = (
            self.data
            .filter(
                pl.col('electrification') == elec,
                pl.col('grade') != (Grade.BASE if delta else ''),
            )
            .with_columns(
                pl.col('grade.kor').cast(
                    pl.Enum(grades if delta else ['Base', *grades])
                ),
                pl.col('emission.delta' if delta else 'emission/gfa').alias('value'),
            )
            .with_columns()
        )

        if owner:
            data = data.filter(pl.col('owner') == owner)

        with plt.rc_context({'ytick.left': False, 'ytick.right': False}):
            fig = Figure(figsize=(*self.figsize, 'cm'))
            axes = fig.subplots(2, 2, squeeze=False, sharex=True)

        purpose = [x for x in PURPOSE if x in data['purpose'].unique()]
        purpose = [*purpose, '전체']

        xlabel = 'Base 대비 탄소 배출량 변화' if delta else '탄소 배출량'

        ax: Axes
        for idx, (ax, p) in enumerate(zip(axes.ravel(), purpose, strict=False)):
            d = data if (p == '전체') else data.filter(pl.col('purpose') == p)
            sns.barplot(
                d.to_pandas(),
                x='value',
                y='grade.kor',
                hue='scope' if existing else None,
                ax=ax,
                legend=idx == 0,
                linewidth=0,
                capsize=0.25,
                err_kws={'linewidth': 1},
            )
            ax.set_xlabel(f'{xlabel} [kg/m²yr]')
            ax.set_ylabel('')
            ax.set_title(f'{p}{f" ({owner})" if owner else ""}')

            if legend := ax.get_legend():
                legend.set_title('')

        path = self.output / (
            f'{self.PREFIX}-{elec}-{owner or "전체"}'
            f'-{"Base편차" if delta else "배출량"}'
            f'{self._suffix}.svg'
        )
        fig.savefig(path)
        fig.savefig(path.with_suffix('.png'))

    def __call__(self):
        for e, v, o in itertools.product(
            ('기존', '전환'),
            ('value', 'delta'),
            ('공공', '민간', None),
        ):
            self.plot(e, v, o)

        for g in (('owner', 'purpose'), ('owner',), ('purpose',)):
            (
                self.data
                .rename({'emission/gfa': 'E/A', 'emission.delta': 'dE/A'})
                .group_by('electrification', *g, 'grade', 'scope')
                .agg(*_describe('E/A'), *_describe('dE/A'))
                .sort(pl.all())
                .with_columns(
                    pl.lit(None).alias(
                        'E/A=연면적당 배출량 [kg/m²], '
                        'dE/A=Baseline 대비 연면적당 배출량 변화 [kg/m²]'
                    )
                )
                .write_csv(
                    self.output / f'{self.PREFIX}-stat-{"-".join(g)}{self._suffix}.csv',
                    include_bom=True,
                )
            )


@app.command
@dataclass
class Reduction(_Command):
    """기존, 베이스 대비 등급별 감축량."""

    PREFIX: ClassVar[str] = '02.감축량'
    EXCLUDE_GRADE: ClassVar[tuple[Grade, ...]] = (Grade.NOPV,)

    @functools.cached_property
    def data(self):
        grade = pl.col('grade')
        emission = pl.col('emission/gfa')

        data = (
            self.emission
            .group_by([*INDEX.CASE, 'grade.kor'])  # scope 미구분
            .agg(emission.sum())
            .with_columns(
                emission
                .filter(grade == Grade.EXST)
                .sum()
                .over(INDEX.BUILDING)
                .alias(Grade.EXST),
                emission
                .filter(grade == Grade.BASE)
                .sum()
                .over(INDEX.BUILDING)
                .alias(Grade.BASE),
            )
            .unpivot(
                [Grade.EXST, Grade.BASE],
                index=[*INDEX.CASE, 'grade.kor', emission],
                variable_name='reference.grade',
                value_name='reference.value',
            )
            .collect()
        )

        assert (
            data
            .filter(grade == pl.col('reference.grade'))
            .select((emission - pl.col('reference.value')).eq(0).all())
            .item()
        )

        return (
            data
            .filter(grade.is_in([Grade.EXST, Grade.BASE]).not_())
            .with_columns(
                (pl.col('reference.value') - emission).alias('reduction'),
                pl.col('reference.grade').replace_strict({
                    Grade.EXST: '기존건물 대비',
                    Grade.BASE: 'Base 대비',
                }),
            )
            .sort(pl.all())
        )

    def plot(self, elec: Electrification):
        data = self.data.filter(pl.col('electrification') == elec)

        with plt.rc_context({'xtick.top': False, 'xtick.bottom': False}):
            fig = Figure(figsize=(*self.figsize, 'cm'))
            axes = fig.subplots(2, 2, sharey=True)

        purpose = data['purpose'].unique().sort().to_list()
        path_effects = [
            patheffects.Stroke(linewidth=2, foreground='w'),
            patheffects.Normal(),
        ]

        ax: Axes
        for idx, (ax, p) in enumerate(zip(axes.ravel(), purpose, strict=False)):
            sns.barplot(
                data.filter(pl.col('purpose') == p).to_dict(),
                x='grade.kor',
                y='reduction',
                hue='reference.grade',
                ax=ax,
                legend=idx == 0,
                linewidth=0,
                capsize=0.1,
                err_kws={'linewidth': 1},
            )

            for container in ax.containers:
                ax.bar_label(
                    container,
                    fmt='%.1f',
                    fontsize='small',
                    padding=2,
                    path_effects=path_effects,
                )

            ax.set_xlabel('')
            ax.set_ylabel('탄소 저감량 [kg/m²yr]')
            ax.set_title(p)

            if legend := ax.get_legend():
                legend.set_title('')

        path = self.output / (f'{self.PREFIX}-{elec}{self._suffix}.svg')
        fig.savefig(path)
        fig.savefig(path.with_suffix('.png'))

    def __call__(self):
        self.data.write_csv(self.output / f'{self.PREFIX}-raw.csv', include_bom=True)
        self.plot('기존')
        self.plot('전환')


@app.command
@dataclass
class Convert(_Command):
    """전전화 전환 비교."""

    PREFIX: ClassVar[str] = '03.전전화전환'

    @functools.cached_property
    def grade_colors(self):
        grades = self.emission.select('grade.kor').unique().collect().to_series().sort()
        colors = Colormap('cmocean:rain')(np.linspace(0.9, 0.1, len(grades)))
        return dict(zip(grades, colors, strict=True))

    @functools.cached_property
    def data(self):
        data = pl.concat([
            self.emission.with_columns(pl.lit('직간접').alias('scope')),
            self.emission.with_columns(pl.col('scope').cast(pl.String)),
        ])
        index = ['bldg', 'region', 'scope', 'grade', 'grade.kor']

        return (
            data
            .unpivot(
                ['delivered/gfa', 'emission/gfa'],
                index=['electrification', *index],
            )
            .group_by('electrification', 'variable', *index)
            .agg(pl.sum('value'))
            .pivot(
                'electrification',
                ['기존', '전환'],
                index=[*index, 'variable'],
                values='value',
            )
            .drop_nulls('전환')
            .with_columns(
                pl.mean_horizontal('기존', '전환').alias('mean'),
                (pl.col('전환') - pl.col('기존')).alias('diff'),
            )
            .sort(pl.all())
            .collect()
        )

    def plot(
        self,
        variable: Literal['소요량', '배출량'],
        scope: SCOPE,
        *,
        base_only: bool = False,
        bland_altman: bool = False,
    ):
        data = (
            self.data
            .filter(pl.col('scope') == scope)
            .filter(
                pl.col('variable')
                == ('delivered/gfa' if variable == '소요량' else 'emission/gfa')
            )
            .sort('grade')
        )

        if base_only:
            data = data.filter(pl.col('grade') == Grade.BASE)

        fig = Figure(figsize=(*self.figsize, 'cm') if bland_altman else None)
        ax = fig.subplots()

        sns.scatterplot(
            data.to_dict(),
            x='mean' if bland_altman else '기존',
            y='diff' if bland_altman else '전환',
            hue=None if base_only else 'grade.kor',
            palette=None if base_only else self.grade_colors,
            ax=ax,
            alpha=0.8,
        )

        unit = 'kWh/m²yr' if variable == '소요량' else 'kg/m²yr'
        if bland_altman:
            ax.set_xlabel(f'전환 전후 평균 {variable} [{unit}]')
            ax.set_ylabel(f'전환 시 {variable} 변화 [{unit}]')
            ax.axhline(y=0, c='k', lw=0.8, alpha=0.8, zorder=0)
        else:
            ax.set_xlabel(f'전환 전 {variable} [{unit}]')
            ax.set_ylabel(f'전환 후 {variable} [{unit}]')
            utils.mpl.equal_scale(ax)

        if not base_only:
            ax.legend(title='', ncols=2)

        kind = 'BlandAltman' if bland_altman else 'Identity'
        b = 'Base' if base_only else 'Grades'
        path = (
            self.output
            / f'{self.PREFIX}-{variable}-{scope}-{kind}-{b}{self._suffix}.svg'
        )
        fig.savefig(path)
        fig.savefig(path.with_suffix('.png'))

    def __call__(self):
        self.data.write_csv(self.output / f'{self.PREFIX}-raw.csv', include_bom=True)

        for variable, scope, o, a in itertools.product(
            ('소요량', '배출량'),
            ('간접', '직간접'),
            (False, True),
            (False, True),
        ):
            self.plot(variable, scope, base_only=o, bland_altman=a)


@app.command
def move_png(paths: Paths):
    src = paths.eco2.report
    dst = src / 'png'
    dst.mkdir(exist_ok=True)

    for p in src.glob('**/*.png'):
        if p.is_relative_to(dst):
            continue

        shutil.move(p, dst / p.name)


@app.command
def run(paths: Paths):
    paths.eco2.report.mkdir(exist_ok=True)

    for outlier in (False, True):
        Baseline(paths, outlier=outlier)()
        ByGrade(paths, outlier=outlier)()
        Reduction(paths, outlier=outlier)()
        Convert(paths, outlier=outlier)()

    move_png(paths)


@app.command
@dataclass
class ConvertExample(_Command):
    bldg: str = '공.A2.N.상.2'
    grade: Grade = Grade.ZEB5
    region: Region = '중부2'

    def __call__(self):
        data = self._raw.filter(
            pl.col('bldg') == self.bldg,
            pl.col('grade') == self.grade,
            pl.col('region') == self.region,
        ).collect()
        data.write_csv(self.output / 'convert-example.csv', include_bom=True)
        return data


if __name__ == '__main__':
    # paul tol light
    sns.set_palette([
        '#44BB99',
        '#77AADD',
        '#EE8866',
        '#EEDD88',
        '#FFAABB',
        '#99DDFF',
        '#BBCC33',
        '#AAAA00',
        '#DDDDDD',
    ])
    app()
