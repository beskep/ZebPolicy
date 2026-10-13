"""2026-10-12 중간보고용 분석."""

import functools
import itertools
from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING, ClassVar, Literal

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import seaborn as sns
import structlog
from cmap import Colormap
from cyclopts.config import Toml
from matplotlib.figure import Figure

from zeb import utils
from zeb.utils.cli import App
from zeb.y2026.common import INDEX, PURPOSE, Electrification, Grade
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


def _describe(name: str = 'emission/gfa') -> pl.Expr:
    c = pl.col(name)
    return pl.struct(
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
    ).struct.unnest()


@app.command
def check_heat_generation(paths: Paths):
    pl.Config.set_tbl_cols(50)
    # TODO: ECO2 열 배출계수 0
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

    PREFIX: ClassVar[str] = ''
    OUTLIERS: ClassVar[tuple[str, ...]] = ('공.A2.N.교.2',)

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
            .filter(pl.col('grade').is_in([Grade.EXST, Grade.NOPV]).not_())
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
            .agg(pl.sum('emission'))
            .with_columns(
                pl
                .col('grade')
                .replace_strict(grade)
                .cast(pl.Enum(list(grade.values())))
                .alias('grade.kor'),
                pl
                .col('emission')
                .truediv(pl.col('연면적'))
                .alias('emission/gfa'),  # kg/m²
            )
        )

    @property
    def output(self):
        return self.paths.eco2.report

    @property
    def _suffix(self):
        return '' if self.outlier else ' NoOutlier'


@app.command
@dataclass
class Baseline(_Command):
    figsize: tuple[float, float] = (25, 9.5)

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

    def plot(self, elec: Electrification):
        data = (
            self.data
            .filter(pl.col('electrification') == elec)
            .with_columns(pl.format('{} {}', 'purpose', 'scale').alias('purpose-scale'))
            .sort('purpose', 'scale')
        )

        with plt.rc_context({'ytick.left': False, 'ytick.right': False}):
            fig = Figure(figsize=(*self.figsize, 'cm'))
            axes = fig.subplots(1, 2, sharex=True)

        ax: Axes
        for ax, scope in zip(axes, ('직접', '간접'), strict=True):
            sns.barplot(
                data.filter(pl.col('scope') == scope).to_pandas(),
                x='emission/gfa',
                y='purpose-scale',
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

        self.output.mkdir(exist_ok=True)
        fig.savefig(self.output / f'{self.PREFIX}-{elec}{self._suffix}.svg')

    def __call__(self):
        self.plot('기존')
        self.plot('전환')

        (
            self.data
            .group_by('electrification', 'purpose', 'scale', 'scope')
            .agg(_describe())
            .sort(pl.all())
            .write_csv(
                self.output / f'{self.PREFIX}-stat{self._suffix}.csv', include_bom=True
            )
        )


@app.command
@dataclass
class ByGrade(_Command):
    figsize: tuple[float, float] = (25, 9.5)

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

    def plot(self, elec: Electrification, value: Literal['value', 'delta']):
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

        with plt.rc_context({'ytick.left': False, 'ytick.right': False}):
            fig = Figure(figsize=(*self.figsize, 'cm'))
            axes = fig.subplots(
                *((2, 3) if existing else (2, 2)),
                squeeze=False,
                sharex=True,
            )

        purpose = PURPOSE if existing else ('상업', '교육사회', '기타')
        xlabel = 'Base 대비 탄소 배출량 변화' if delta else '탄소 배출량'

        ax: Axes
        for ax, p in zip(axes.ravel(), ('전체', *purpose), strict=True):
            a = p == '전체'
            d = data if a else data.filter(pl.col('purpose') == p)
            sns.barplot(
                d.to_pandas(),
                x='value',
                y='grade.kor',
                hue='scope' if existing else None,
                ax=ax,
                legend=a,
                linewidth=0,
                capsize=0.25,
                err_kws={'linewidth': 1},
            )
            ax.set_xlabel(f'{xlabel} [kg/m²yr]')
            ax.set_ylabel('')
            ax.set_title(p)

            if legend := ax.get_legend():
                legend.set_title('')

        name = (
            f'{self.PREFIX}-{elec}-{"Base편차" if delta else "배출량"}'
            f'{self._suffix}.svg'
        )
        fig.savefig(self.output / name)

    def __call__(self):
        for e, v in itertools.product(('기존', '전환'), ('value', 'delta')):
            self.plot(e, v)

        (
            self.data
            .group_by('electrification', 'purpose', 'grade', 'scope')
            .agg(_describe())
            .sort(pl.all())
            .write_csv(
                self.output / f'{self.PREFIX}-stat{self._suffix}.csv', include_bom=True
            )
        )


@app.command
@dataclass
class Convert(_Command):
    """전전화 전환 비교."""

    figsize: tuple[float, float] = (25, 9.5)

    PREFIX: ClassVar[str] = '03.전전화전환'

    def __post_init__(self):
        super().__post_init__()
        self._data = functools.cache(self.__data)

    @functools.cached_property
    def grade_colors(self):
        grades = self.emission.select('grade.kor').unique().collect().to_series().sort()
        colors = Colormap('cmocean:rain')(np.linspace(0.9, 0.1, len(grades)))
        return dict(zip(grades, colors, strict=True))

    def __data(self, scope: SCOPE):
        if scope == '직간접':
            data = self.emission
        else:
            data = self.emission.filter(pl.col('scope') == scope)

        index = ['bldg', 'region', 'grade', 'grade.kor']

        return (
            data
            .group_by('electrification', *index)
            .agg(pl.sum('emission/gfa'))
            .pivot(
                'electrification', ['기존', '전환'], index=index, values='emission/gfa'
            )
            .drop_nulls('전환')
            .with_columns(
                pl.mean_horizontal('기존', '전환').alias('mean'),
                (pl.col('전환') - pl.col('기존')).alias('diff'),
            )
            .collect()
        )

    def plot(
        self,
        scope: SCOPE,
        *,
        base_only: bool = False,
        bland_altman: bool = False,
    ):
        data = self._data(scope).sort('grade')

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

        if bland_altman:
            ax.set_xlabel('전환 전후 평균 배출량 [kg/m²yr]')
            ax.set_ylabel('전환 시 배출량 변화 [kg/m²yr]')
            ax.axhline(y=0, c='k', lw=0.8, alpha=0.8, zorder=0)
        else:
            ax.set_xlabel('전환 전 배출량 [kg/m²yr]')
            ax.set_ylabel('전환 후 배출량 [kg/m²yr]')
            utils.mpl.equal_scale(ax)

        if not base_only:
            ax.legend(title='', ncols=2)

        kind = 'BlandAltman' if bland_altman else 'Identity'
        b = 'Base' if base_only else 'Grades'
        fig.savefig(self.output / f'{self.PREFIX}-{scope}-{kind}-{b}{self._suffix}.svg')

    def __call__(self):
        (
            self.emission
            .group_by('electrification', 'scope', 'bldg', 'region', 'grade')
            .agg(pl.sum('emission/gfa'))
            .pivot(
                'electrification',
                ['기존', '전환'],
                index=['scope', 'bldg', 'region', 'grade'],
                values='emission/gfa',
            )
            .drop_nulls('전환')
            .with_columns(
                pl.mean_horizontal('기존', '전환').alias('mean'),
                (pl.col('전환') - pl.col('기존')).alias('diff'),
            )
            .sort(pl.all())
            .collect()
            .write_csv(self.output / f'{self.PREFIX}-raw.csv', include_bom=True)
        )

        for scope, o, a in itertools.product(
            ('간접', '직간접'),
            (False, True),
            (False, True),
        ):
            self.plot(scope, base_only=o, bland_altman=a)


@app.command
def run(paths: Paths):
    for outlier in (False, True):
        Baseline(paths, outlier=outlier)()
        ByGrade(paths, outlier=outlier)()
        Convert(paths, outlier=outlier)()


if __name__ == '__main__':
    plt.style.use('custom.mplstyle')
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
