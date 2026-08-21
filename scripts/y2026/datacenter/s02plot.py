import dataclasses as dc
import functools
from pathlib import Path  # ruff:ignore[typing-only-standard-library-import]
from typing import TYPE_CHECKING, Literal

import cyclopts
import plotnine as gg
import polars as pl
import seaborn as sns
import structlog
from cmap import Colormap
from matplotlib.figure import Figure

from zeb import utils
from zeb.utils.cli import App

if TYPE_CHECKING:
    from matplotlib.axes import Axes

app = App(
    config=cyclopts.config.Toml(
        'env.toml',
        root_keys=['2026', 'datacenter'],
        allow_unknown=True,
        use_commands_as_keys=False,
    )
)
logger = structlog.stdlib.get_logger()


@app.command
@dc.dataclass
class Consumption:
    root: Path
    plot: Path

    @functools.cached_property
    def data(self):
        return (
            pl
            .scan_parquet(self.root / '05.consumption.parquet')
            .filter(
                pl.col('variable') == '1차에너지소요량',
                pl.col('function') != '합계',
            )
            .with_columns(
                pl
                .col('function')
                .replace_strict('냉방', 'cooling', default='others')
                .alias('function.group'),
            )
            .collect()
        )

    def _plot(self, param: Literal['TC', 'QE']):
        data = (
            self.data
            .filter(pl.col('case.variable') == param)
            .with_columns(pl.col('case.value').cast(pl.UInt32))
            .sort('case.value', descending=param == 'QE')
        )

        match param:
            case 'TC':
                data = (
                    data
                    .filter(pl.col('case.value').is_in([26, 28, 30]))
                    .with_columns(pl.format('{}℃', 'case.value').alias('case'))
                    .with_columns()
                )
            case 'QE':
                data = data.with_columns(pl.format('{}Wh', 'case.value').alias('case'))

        function_group = ['cooling', 'others']
        palette = Colormap('cmocean:matter')([0.5, 0.65, 0.8]).tolist()
        fig = Figure((32 * 0.8, 8.8 * 0.8, 'cm'))
        axes: list[Axes] = fig.subplots(1, len(function_group), width_ratios=(1, 3.2))
        for group, ax in zip(function_group, axes, strict=True):
            sns.boxplot(
                data.filter(pl.col('function.group') == group),
                x='function',
                order=None if group == 'cooling' else ['난방', '조명', '환기', '급탕'],
                y='value',
                hue='case',
                ax=ax,
                width=0.6,
                showmeans=True,
                meanprops={
                    'marker': 'D',
                    'markerfacecolor': 'w',
                    'markeredgecolor': 'dimgray',
                },
                palette=palette,
            )

            ax.set_ylim(0)
            ax.legend(title='', loc='upper right', fontsize='x-small')
            ax.set_xlabel('')
            ax.set_ylabel('1차에너지소요량 [kWh/m²]')

        fig.savefig(self.plot / f'consumption.{param}.png')
        fig.savefig(self.plot / f'consumption.{param}.svg')

    def __call__(self):
        (
            utils.mpl
            .MplTheme(font_scale=1.2)
            .grid(show=False)
            .tick(axis='y')
            .apply({'xtick.major.size': 2.5, 'ytick.major.size': 2.5})
        )

        self._plot('TC')
        self._plot('QE')


@app.command
@dc.dataclass
class Visualize:
    root: Path
    plot: Path

    @functools.cached_property
    def _theme(self):
        return gg.theme(dpi=300) + gg.theme_bw(base_family='Source Han Sans KR')

    @functools.cached_property
    def data(self):
        return (
            pl
            .scan_parquet(self.root / '03.report.tidy.parquet')
            .with_columns(
                pl.col('variable').str.extract_groups(
                    r'^(?<variable>1차에너지소요량)/'
                    r'(?<function>\w+)(?:/(?<source>.*))?$'
                )
            )
            .unnest('variable')
            .drop_nulls('variable')
            .with_columns(pl.col('value').str.replace_all(',', '').cast(pl.Float64))
            .collect()
        )

    def plot_set_temperature(self, variable: str, default: float):
        data = (
            self.data
            .filter(
                pl.col('source').is_null(),
                pl.col('function') != '합계',
                pl.col('case.variable').is_in(['raw', variable]),
            )
            .with_columns(pl.col('case.value').fill_null(default))
            .group_by('case.variable', 'case.value', 'function')
            .agg(pl.mean('value'))
            .sort(pl.all())
            .with_columns(
                pl.format('{}℃', pl.col('case.value').cast(pl.UInt8)).alias('case'),
            )
        )

        p = (
            gg.ggplot(data, gg.aes('case', 'value', fill='function'))
            + gg.geom_col()
            + gg.labs(fill='')
            + self._theme
        )

        fig = p.draw()
        fig.savefig(self.plot / f'SetTemp.{variable}.png')
        fig.savefig(self.plot / f'SetTemp.{variable}.svg')

    def __call__(self):
        self.plot.mkdir(exist_ok=True)

        self.plot_set_temperature('TC', 26)
        self.plot_set_temperature('TH', 20)


if __name__ == '__main__':
    app()
