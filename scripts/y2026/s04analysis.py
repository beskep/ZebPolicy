import dataclasses as dc
import functools
import itertools
from typing import TYPE_CHECKING, ClassVar, Literal

import cmap
import cyclopts
import matplotlib as mpl
import matplotlib.pyplot as plt
import plotnine as gg
import polars as pl
import polars.testing
import seaborn as sns
import structlog
from matplotlib.figure import Figure

from zeb.utils.cli import App
from zeb.y2026.common import INDEX, Grade
from zeb.y2026.config import Paths  # ruff:ignore[typing-only-first-party-import]

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from matplotlib.axes import Axes

app = App(
    config=cyclopts.config.Toml(
        'env.toml', root_keys='2026', allow_unknown=True, use_commands_as_keys=False
    )
)
logger = structlog.stdlib.get_logger()


@app.command
@dc.dataclass
class Reduction:
    paths: Paths
    reference: Literal['eco2', 'reference'] = 'eco2'
    fmt: Literal['svg', 'png'] = 'svg'

    @functools.cached_property
    def raw(self):
        reference = 'ECO2' if self.reference == 'eco2' else 'reference'
        data = (
            pl
            .scan_parquet(self.paths.eco2.analysis / '02.emission.parquet')
            .filter(pl.col('reference') == reference)
            .with_columns(
                pl
                .when(pl.col('use') == '주거')
                .then(pl.lit('null'))
                .otherwise(pl.col('scale.a'))
                .alias('scale.a')
            )
            .group_by([*INDEX.CASE, 'scope'])
            .agg(pl.sum('emission'))  # kg
            .with_columns(
                pl
                .col('emission')
                .filter(pl.col('grade') == Grade.BASE)
                .sum()
                .over([*INDEX.BUILDING, 'scope'])
                .alias('emission.baseline')
            )
            .rename({'연면적': 'gfa'})
            .collect()
        )
        baseline = data.filter(pl.col('grade') == Grade.BASE)
        polars.testing.assert_series_equal(
            baseline['emission'], baseline['emission.baseline'], check_names=False
        )
        return data

    def _reduction_rate(self, index: Sequence[str]):
        return (
            self.raw
            .group_by(index)
            .agg(pl.sum('gfa', 'emission', 'emission.baseline'))
            .with_columns(
                pl
                .col('emission', 'emission.baseline')
                .truediv(pl.col('gfa'))
                .name.suffix('.norm')  # kg/m²
            )
        )

    def data(self):
        if (cache := self.paths.eco2.analysis / '03.group.parquet').exists():
            return pl.read_parquet(cache)

        # TODO: 단독주택 제외하고 계산
        indices = {
            'detailed': ['use', 'owner', 'scale.c', 'scale.a', 'purpose'],
            'broad': ['use', 'owner'],
        }
        data = pl.concat(
            [
                self._reduction_rate(['grade', 'scope', *v]).with_columns(
                    pl.lit(k).alias('group')
                )
                for k, v in indices.items()
            ],
            how='diagonal',
        ).sort(pl.col('grade').replace_strict(Grade.order()), pl.all())

        data.write_parquet(cache)
        data.write_csv(cache.with_suffix('.csv'), include_bom=True)

        return data

    def plot(self, *, generation: bool = True, detached: bool = True):
        data = self.data()

        if not generation:
            data = data.filter(pl.col('scope') != 'generation.elec')
        if not detached:
            data = data.filter(
                (pl.col('purpose') != '단독주택') | pl.col('purpose').is_null()
            )

        data = (
            data
            .filter(
                pl.col('grade') != Grade.BASE,
                pl.col('scope') != 'generation.heat',
            )
            .sort([
                pl.col('grade').replace_strict(Grade.order()),
                pl.col('scope').replace_strict({
                    'direct': 0,
                    'indirect': 1,
                    'generation.elec': 2,
                }),
            ])
            .with_columns(
                pl.col('grade').replace_strict(Grade.kor()),
                pl
                .col('emission.norm')
                .sub(pl.col('emission.baseline.norm'))
                .alias('emission.delta'),
                pl.format('{} {}', 'owner', 'use').alias('category'),
                pl.col('scope').replace_strict({
                    'direct': '직접 배출',
                    'indirect': '간접 배출',
                    'generation.elec': '전력 생산',
                }),
            )
        )

        with plt.rc_context({'ytick.left': False, 'ytick.right': False}):
            fig = Figure(figsize=(16 * 1.5, 9 * 1.5, 'cm'))
            axes = fig.subplots(2, 2, sharex='col')

        ax: Axes
        for ax, c in zip(
            axes.ravel(),
            ('공공 비주거', '공공 주거', '민간 비주거', '민간 주거'),
            strict=True,
        ):
            d = data.filter(pl.col('category') == c)
            sns.barplot(
                d.filter(pl.col('group') == 'broad'),
                x='emission.delta',
                y='grade',
                hue='scope',
                ax=ax,
                fill=False,
                linewidth=1,
            )
            sns.stripplot(
                d.filter(pl.col('group') == 'detailed'),
                x='emission.delta',
                y='grade',
                hue='scope',
                ax=ax,
                dodge=True,
                size=4,
                legend=False,
                alpha=0.5,
            )

            ax.margins(y=0.05)
            ax.set_title(c)
            ax.set_xlabel('Baseline 대비 배출량 [kg/m²·yr]')
            ax.set_ylabel('')
            ax.legend(title='')

        gen = 'generation' if generation else 'nogen'
        det = 'detached' if detached else 'nodetached'
        fig.savefig(self.paths.eco2.analysis / f'03.reduction.{gen}.{det}.{self.fmt}')

    def __call__(self):
        self.plot(generation=True, detached=True)
        self.plot(generation=False, detached=False)


@functools.lru_cache
def colors(name: str = 'tol:bright'):
    return [c.rgba.to_hex() for c in cmap.Colormap(name).iter_colors()]


@app.command
@dc.dataclass
class Trend:
    paths: Paths
    reference: Literal['eco2', 'reference'] = 'eco2'
    fmt: Literal['svg', 'png'] = 'svg'

    PURPOSE: ClassVar[tuple[str, ...]] = (
        '교육사회',
        '상업',
        '기타',
        '단독주택',
        '공동주택',
    )
    VAR: ClassVar[dict[str, tuple[str, str]]] = {
        'requirement': ('소요량', 'kWh/m²'),
        'emission': ('탄소배출량', 'kg/m²'),
    }

    @functools.cached_property
    def data(self):
        ref = self.reference.upper() if self.reference == 'eco2' else self.reference
        index = [
            'bldg',
            'use',
            'owner',
            'scale.c',
            'scale.a',
            'purpose',
            'index',
            'region',
        ]
        data = (
            pl
            .scan_parquet(self.paths.eco2.analysis / '02.emission.parquet')
            .filter(pl.col('reference') == ref)
            .rename({'연면적': 'gfa'})
            .with_columns(pl.col('value').truediv('gfa').alias('requirement'))
            .with_columns(
                (pl.col('emission_factor') * pl.col('requirement')).alias('emission')
            )
            .group_by([*index, 'grade'])
            .agg(pl.sum('requirement', 'emission'))
            .unpivot(['requirement', 'emission'], index=[*index, 'grade'])
            .collect()
        )
        base = (
            data
            .filter(pl.col('grade') == 'Base')
            .drop('grade')
            .rename({'value': 'value.base'})
        )
        return (
            data
            .join(
                base,
                on=[*index, 'variable'],
                how='left',
                validate='m:1',
                nulls_equal=True,
            )
            .with_columns((pl.col('value') / pl.col('value.base')).alias('value.ratio'))
            .with_columns()
        )

    @functools.cached_property
    def grade_order(self):
        return {x: i for i, x in enumerate(Grade)}

    @functools.cached_property
    def purpose_order(self):
        return {x: i for i, x in enumerate(self.PURPOSE)}

    @functools.cached_property
    def output(self):
        d = self.paths.eco2.analysis / 'trend'
        d.mkdir(exist_ok=True)
        return d

    def desc(self, group: Collection[str]):
        group = [*group, 'grade', 'variable']

        if 'scale' not in group:
            data = self.data
        else:
            data = self.data.with_columns(
                scale=pl
                .when(pl.col('scale.c').is_null())
                .then(pl.col('scale.a'))
                .otherwise(pl.col('scale.c'))
            )

        def it():
            for value, df in data.group_by(group, maintain_order=True):
                index = (pl.lit(v).alias(g) for v, g in zip(value, group, strict=True))
                yield df.select('value').describe().select(*index, pl.all())

        return (
            pl.concat(it()).pivot('statistic', index=group, values='value').sort(group)
        )

    def plot(self, variable: Literal['requirement', 'emission'], *, normalize: bool):
        value = 'value.ratio' if normalize else 'value'

        data = (
            self.data
            .filter(pl.col('variable') == variable)
            .group_by('grade', 'purpose')
            .agg(
                pl.median(value),
                pl.std(value).alias('std'),
                pl.std(value).truediv(pl.len()).alias('se'),
                ymin=pl.col(value).quantile(0.25),
                ymax=pl.col(value).quantile(0.75),
            )
            .with_columns(
                pl.col('grade').replace_strict(self.grade_order).alias('gradeorder'),
                pl
                .col('purpose')
                .replace_strict(self.purpose_order)
                .alias('purposeorder'),
            )
        )

        width = 0.8
        v, u = self.VAR[variable]
        ylabel = f'Baseline 대비 {v}' if normalize else f'{v} [{u}]'
        p = (
            gg.ggplot(
                data,
                gg.aes(
                    'reorder(grade, gradeorder)',
                    value,
                    fill='reorder(purpose, purposeorder)',
                    ymin='ymin',
                    ymax='ymax',
                ),
            )
            + gg.geom_col(position='dodge', width=width)
            + gg.geom_errorbar(position='dodge', width=width, alpha=0.8)
            + gg.scale_fill_manual(colors(), name='')
            + gg.labs(
                x='', y=ylabel, fill='', title=f'등급·용도별 {v} 중위수 (IQR 에러바)'
            )
            + gg.coord_flip()
            + gg.theme_bw(base_family='Noto Sans KR')
            + gg.theme(
                figure_size=(16 / 2.54, 12 / 2.54),
                panel_grid_major_y=gg.element_blank(),
                plot_title=gg.element_text(ha='left'),
            )
        )
        fig = p.draw()
        ax = fig.get_axes()[0]
        ax.invert_yaxis()

        fig.savefig(
            self.output / f'{variable}{".ratio" if normalize else ""}.{self.fmt}'
        )

    def __call__(self):
        for group in (
            ('use', 'purpose'),
            ('use', 'purpose', 'owner'),
            ('use', 'purpose', 'scale'),
            ('use', 'purpose', 'scale', 'owner'),
        ):
            desc = self.desc(group)
            g = '+'.join(group)
            desc.write_csv(
                self.paths.eco2.analysis / f'04.describe.{g}.csv', include_bom=True
            )

        mpl.rcParams['savefig.dpi'] = 300
        for v, n in itertools.product(('requirement', 'emission'), (False, True)):
            self.plot(v, normalize=n)

        return self.data.glimpse(return_type='string')


if __name__ == '__main__':
    plt.style.use('custom.mplstyle')
    app()
