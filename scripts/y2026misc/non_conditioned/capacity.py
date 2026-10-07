import functools
from dataclasses import dataclass
from pathlib import Path  # ruff: ignore[typing-only-standard-library-import]
from typing import TYPE_CHECKING

import cyclopts
import matplotlib.pyplot as plt
import polars as pl
import seaborn as sns
from matplotlib.figure import Figure

if TYPE_CHECKING:
    from matplotlib.axes import Axes

app = cyclopts.App(
    config=cyclopts.config.Toml(
        'env.toml',
        root_keys=['non-conditioned', 'capacity'],
        allow_unknown=True,
        use_commands_as_keys=False,
    ),
    result_action=['call_if_callable', 'print_non_int_sys_exit'],
)


@app.command
def parse(root: Path, src: Path):
    src = root / src
    data = (
        pl
        .read_excel(src)
        .fill_null(strategy='forward')
        .unpivot(
            index=[
                'use',
                'certification',
                'owner',
                'application_number',
                'building',
                'variable',
            ],
            variable_name='case',
        )
        .with_columns(
            pl.col('case').str.extract_groups(r'^(?<case>\w+)\.(?<function>\w+)$')
        )
        .unnest('case')
    )

    data.write_parquet(root / '01.data.parquet')
    data.write_csv(root / '01.data.csv', include_bom=True)

    pl.Config.set_tbl_cols(20)

    return data


@app.command
@dataclass
class Eda:
    """난방 소요량, 1차에너지소요량 비교."""

    root: Path
    figsize: tuple[float, float] = (16, 5)  # cm

    @functools.cached_property
    def data(self):
        data = (
            pl
            .scan_parquet(self.root / '01.data.parquet')
            .with_columns(
                pl
                .concat_str(
                    'use',
                    'certification',
                    'owner',
                    'application_number',
                    'function',
                    separator='.',
                )
                .cast(pl.Categorical)
                .to_physical()
                .alias('index'),
            )
            .with_columns(
                pl
                .col('value')
                .filter(pl.col('case') == '원안')
                .sum()
                .over('index', 'variable', 'function')
                .alias('original')
            )
            .with_columns((pl.col('value') - pl.col('original')).alias('delta'))
            .collect()
        )

        assert (
            data
            .filter(pl.col('case') == '원안')
            .select(pl.col('delta').eq(0).all())
            .item()
        )

        return data

    def describe(self):

        (
            pl
            .concat([
                self.data,
                self.data.with_columns(pl.lit('전체용도').alias('use')),
            ])
            .with_columns(
                column=pl.concat_str(
                    'use', 'case', 'variable', 'function', separator='.'
                )
            )
            .unpivot(
                ['value', 'delta'],
                index=['index', 'column'],
                variable_name='_variable',
                value_name='_value',
            )
            .with_columns(column=pl.format('{}.{}', '_variable', 'column'))
            .pivot('column', index='index', values='_value')
            .drop('index')
            .describe(interpolation='linear')
            .unpivot(index='statistic')
            .pivot('statistic', index='variable', values='value')
            .sort('variable')
            .write_csv(self.root / '02.describe.csv', include_bom=True)
        )

    def plot_data(self, function: str, *, delta: bool):
        return (
            self.data
            .filter(pl.col('function') == function)
            .filter(pl.col('case') != ('원안' if delta else ''))
            .with_columns(pl.col('case').replace({'2안': '2안 (1.5배)'}))
        )

    def plot_sub(self, *, delta: bool = True):
        data = self.plot_data('난방', delta=delta)

        fig = Figure(figsize=(*self.figsize, 'cm'))
        axes = fig.subplots(1, 2, squeeze=False)

        ax: Axes
        for ax, v in zip(axes.ravel(), ('소요량', '1차소요량'), strict=True):
            sns.boxplot(
                data.filter(pl.col('variable') == v),
                x='delta' if delta else 'value',
                y='use',
                hue='case',
                ax=ax,
                legend=v == '소요량',
            )

            ax.set_xlabel(f'난방 {v}{" 변화" if delta else ""} [kWh/m²]')
            ax.set_ylabel('')

            if legend := ax.get_legend():
                legend.set_title('')

            if delta:
                ax.axvline(0, c='.6', ls='--', lw=0.8, zorder=0)
                ax.grid(visible=False)

        p = self.root / f'03.delivered.{"delta" if delta else "value"}.svg'
        fig.savefig(p)
        fig.savefig(p.with_suffix('.png'))

        return fig

    def plot_for_grade(self, *, delta: bool = True):
        data = self.plot_data('합계', delta=delta).filter(
            pl.col('variable') == '등급용1차소요량'
        )

        fig = Figure(figsize=(*self.figsize, 'cm'))
        ax = fig.subplots()

        sns.boxplot(data, x='delta' if delta else 'value', y='use', hue='case', ax=ax)

        ax.set_xlabel(f'등급용1차소요량{" 변화" if delta else ""} [kWh/m²]')
        ax.set_ylabel('')
        ax.legend(title='')

        if delta:
            ax.axvline(0, c='.6', ls='--', lw=0.8, zorder=0)
            ax.grid(visible=False)

        p = self.root / f'04.delivered.for-grade.{"delta" if delta else "value"}.svg'
        fig.savefig(p)
        fig.savefig(p.with_suffix('.png'))

    def __call__(self):
        self.describe()

        plt.style.use('custom.mplstyle')

        with plt.rc_context({
            'ytick.left': False,
            'ytick.right': False,
            'legend.fontsize': 'small',
        }):
            self.plot_for_grade(delta=True)
            self.plot_for_grade(delta=False)
            self.plot_sub(delta=True)
            self.plot_sub(delta=False)

        pl.Config.set_tbl_cols(20)

        return self.data


if __name__ == '__main__':
    app()
