import dataclasses as dc
import functools
import itertools
from typing import TYPE_CHECKING, ClassVar, Literal

import cmap
import cyclopts
import matplotlib as mpl
import plotnine as gg
import polars as pl
import structlog

from zeb.utils.cli import App
from zeb.y2026.common import Grade
from zeb.y2026.config import Paths  # ruff:ignore[typing-only-first-party-import]

if TYPE_CHECKING:
    from collections.abc import Collection

app = App(
    config=cyclopts.config.Toml(
        'env.toml', root_keys=['2026', 'paths'], use_commands_as_keys=False
    )
)
logger = structlog.stdlib.get_logger()


@functools.lru_cache
def colors(name: str = 'tol:bright'):
    return [c.rgba.to_hex() for c in cmap.Colormap(name).iter_colors()]


@app.command
@dc.dataclass
class Trend:
    paths: Paths
    emission_reference: Literal['eco2', 'reference'] = 'eco2'

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
        ref = (
            self.emission_reference.upper()
            if self.emission_reference == 'eco2'
            else self.emission_reference
        )
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
            .scan_parquet(self.paths.analysis / '02.emission.parquet')
            .filter(
                pl.col('grade') != Grade.NOPV,
                pl.col('reference') == ref,
            )
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
        d = self.paths.analysis / 'trend'
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

        fig.savefig(self.output / f'{variable}{".ratio" if normalize else ""}.png')

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
                self.paths.analysis / f'03.describe.{g}.csv', include_bom=True
            )

        mpl.rcParams['savefig.dpi'] = 300
        for v, n in itertools.product(('requirement', 'emission'), (False, True)):
            self.plot(v, normalize=n)

        return self.data.glimpse(return_type='string')


if __name__ == '__main__':
    app()
