"""배출량 평가 관련 기타 분석."""

import dataclasses as dc
import functools
import warnings
from pathlib import Path
from typing import Annotated, ClassVar

import cyclopts
import cyclopts.validators as cv
import matplotlib.pyplot as plt
import polars as pl
import seaborn as sns
from cmap import Colormap
from matplotlib.layout_engine import ConstrainedLayoutEngine
from matplotlib.ticker import PercentFormatter

from scripts.y2025.common import Grade
from zeb import utils
from zeb.utils.cli import App

Directory = Annotated[Path, cv.Path(exists=True, file_okay=False, dir_okay=True)]


app = App(
    config=[
        cyclopts.config.Toml(
            'config.toml',
            root_keys='analysis',
            use_commands_as_keys=x,
            allow_unknown=True,
        )
        for x in [False, True]
    ],
    result_action=['call_if_callable', 'print_non_int_sys_exit'],
)


@app.command
def heat_gen(root: Path):
    """열 생산 케이스 통계."""
    by = [
        'building-use',
        'ownership',
        'scale',
        'scale1',
        'scale2',
        'use',
        'bldg',
        'region',
        'case',
        'grade',
    ]
    lf = (
        pl
        .scan_parquet(root / '02.data.parquet')
        .filter(
            pl.col('function') == '생산-면적당열',
            pl.col('grade') == 'Base',
            pl.col('region') == '중부1',
        )
        .group_by(by)
        .agg(pl.sum('requirement'))
        .with_columns(
            (pl.col('requirement') != 0)
            .replace_strict({True: '생산', False: '미생산'})
            .alias('열생산')
        )
    )

    summary = utils.pl.PolarsSummary(lf, group='building-use')
    summary.write_excel(root / '열 생산 케이스 통계.xlsx')


@app.command
@dc.dataclass
class PVArea:
    src: Directory
    dst: Directory | None = None

    _: dc.KW_ONLY

    scale: float = 0.9
    width: float = 27
    height: float = 12

    INDEX: ClassVar[tuple[str, ...]] = (
        'building-use',
        'ownership',
        'scale',
        'scale1',
        'scale2',
        'use',
        'bldg',
        'region',
        'grade',
    )

    @functools.cached_property
    def output(self):
        return self.dst or self.src / '03.pv-area'

    @functools.cached_property
    def palette(self):
        return tuple(Colormap('tol:medium-contrast').iter_colors())

    @functools.cached_property
    def raw(self):
        src = list(self.src.glob('00.*-zeb.parquet'))
        data = (
            pl
            .scan_parquet(src, include_file_paths='path')
            .select(
                pl
                .col('path')
                .str.extract(r'.*\\00.(residential|non-residential).*')
                .alias('building-use'),
                pl.all(),
            )
            .filter(pl.col('group').is_null())
            .collect()
            .pivot('variable', index=self.INDEX, values='value')
            .with_columns(
                pl.col('grade').replace({'Base': Grade.BASE}),
                pl.col('ownership').replace_strict({'공': '공공', '민': '민간'}),
                pl.col('use').replace_strict({
                    x[0]: x
                    for x in ['교육사회', '상업', '기타', '공동주택', '단독주택']
                }),
                scale=pl.format('규모{}', pl.col('scale').str.extract(r'^\w(\d)$')),
            )
        )

        return (
            (data)
            .unpivot(
                index=[x for x in data.columns if x not in {'PV면적', 'BIPV면적'}],
                value_name='pv-area',
            )
            .with_columns(pl.col('variable').str.strip_suffix('면적'))
        )

    def plot_full(self, area: str):
        data = (
            (self.raw)
            .filter(pl.col('grade') != Grade.BASE)
            .with_columns(ratio=pl.col('pv-area') / pl.col(area))
        )
        grades = [x for x in Grade if x in data['grade'].unique()]
        grid = (
            sns
            .catplot(
                data,
                x='ratio',
                y='grade',
                order=grades,
                hue='variable',
                row='scale',
                col='use',
                col_order=['교육사회', '상업', '기타', '공동주택', '단독주택'],
                kind='bar',
                height=1.25,
                margin_titles=True,
                aspect=16 / 9,
                width=0.9,
                facet_kws={'despine': False},
            )
            .set_titles(row_template='{row_name}', col_template='{col_name}')
            .set_axis_labels(f'PV면적 / {area}', '')
        )

        for ax in grid.figure.axes:
            ax.xaxis.set_major_formatter(PercentFormatter(xmax=1.0))

        if legend := grid.legend:
            legend.remove()

        grid.add_legend(loc='center', bbox_to_anchor=(0.9, 0.15))
        ConstrainedLayoutEngine().execute(grid.figure)

        return grid

    def plot(self, area: str):
        data = (
            (self.raw)
            .filter(pl.col('grade') != Grade.BASE, pl.col('variable') == 'BIPV')
            .with_columns(ratio=pl.col('pv-area') / pl.col(area))
        )

        grades = [x for x in Grade if x in data['grade'].unique()]
        grid = (
            sns
            .catplot(
                data,
                x='grade',
                order=grades,
                y='ratio',
                hue='scale',
                kind='point',
                col='use',
                col_order=['교육사회', '상업', '기타', '공동주택', '단독주택'],
                col_wrap=3,
                sharex=False,
                alpha=0.8,
                palette=self.palette,
                height=3.5,
                facet_kws={'despine': False},
            )
            .set_titles('')
            .set_titles('{col_name}', loc='left', weight=500)
            .set_axis_labels('', f'BIPV 면적 / {area}')
        )

        if legend := grid.legend:
            legend.set_title('')
            legend.set_frame_on(b=True)

        grid.figure.axes[0].yaxis.set_major_formatter(PercentFormatter(xmax=1.0))
        grid.figure.set_size_inches(self.width / 2.54, self.height / 2.54)
        ConstrainedLayoutEngine().execute(grid.figure)
        utils.mpl.move_grid_legend(grid)
        return data, grid

    def __call__(self):
        warnings.filterwarnings('ignore', 'The figure layout has changed to tight')
        utils.mpl.MplTheme(self.scale).grid().apply({'lines.solid_capstyle': 'butt'})

        self.output.mkdir(exist_ok=True)

        for a in ['외벽면적', '연면적', '대지면적']:
            grid = self.plot_full(a)
            grid.savefig(self.output / f'full-pv-area-{a}.png')
            plt.close(grid.figure)

            data, grid = self.plot(a)
            data.write_excel(self.output / f'pv-area-{a}.xlsx', column_widths=120)
            grid.savefig(self.output / f'pv-area-{a}.png')
            plt.close(grid.figure)


if __name__ == '__main__':
    app()
