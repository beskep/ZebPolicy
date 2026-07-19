"""EG 공사비 해석."""

import contextlib
import dataclasses as dc
import enum
import functools
import re
from pathlib import Path  # ruff:ignore[typing-only-standard-library-import]
from typing import ClassVar

import cyclopts
import polars as pl
import seaborn as sns
import xlsxwriter
from cyclopts.types import ExistingPath  # ruff:ignore[typing-only-third-party-import]
from matplotlib.figure import Figure

from zeb import utils
from zeb.utils.cli import App

app = App(
    help_on_error=True,
    config=cyclopts.config.Toml('config.toml', root_keys='cost'),
)

_COLUMN = re.compile(r'(?P<column>.*?)(_\d+)?')


def _rename_column(text: str) -> str:
    return _COLUMN.sub(r'\g<column>', text)


@app.command
@dc.dataclass
class Parse:
    """EG 자료 해석."""

    src: ExistingPath
    dst: Path | None = None

    width: int = 8
    height: int = 25

    header_suffix: str = '(자립률)'
    cost: str = '전체공사비'
    column_widths: int = 100

    REPLACE: ClassVar[dict[str, str]] = {'전체공사비(에너지비율)': '전체공사비'}

    @functools.cached_property
    def raw(self):
        with contextlib.redirect_stderr(None):
            raw = pl.read_excel(
                self.src, has_header=False, drop_empty_rows=False, drop_empty_cols=False
            )

        return (
            raw
            .with_row_index('row')
            .unpivot(index='row')
            .select(
                'row',
                pl
                .col('variable')
                .str.extract(r'column_(\d+)')
                .cast(pl.UInt32)
                .add(-1)
                .alias('col'),
                'value',
            )
        )

    def read_table(self, row: int, col: int, region: str):
        use = self.raw.row(
            by_predicate=(pl.col('row') == row) & (pl.col('col') == col + 1)
        )
        table = pl.read_excel(
            self.src,
            read_options={
                'header_row': row + 1,
                'n_rows': self.height,
                'use_columns': [col + x for x in range(self.width)],
            },
        )
        aux = (
            pl
            .read_excel(
                self.src,
                read_options={
                    'header_row': row - 2,
                    'n_rows': 1,
                    'use_columns': [col + 1 + x for x in range(self.width - 1)],
                },
            )
            .rename(_rename_column)
            .with_columns(pl.lit(region).alias('region'))
        )

        return (
            (table)
            .rename({table.columns[0]: 'variable'})
            .rename(_rename_column)
            .select(
                pl.lit(region).alias('region'),
                pl.lit(use[-1]).alias('use'),
                pl.all(),
            )
            .unpivot(index=['region', 'use', 'variable'], variable_name='grade')
            .join(aux, on='region', how='left')
        )

    def _read_tables(self):
        head = self.raw.filter(pl.col('value').str.contains(self.header_suffix))
        for row in head.iter_rows():
            yield self.read_table(*row)

    def _cost(self, data: pl.DataFrame):
        index = ['region', 'use', 'variable']
        cost = data.filter(pl.col('variable') == self.cost)
        base = (
            (cost)
            .filter(pl.col('grade') == '1+')
            .select(*index, pl.col('value').alias('base'))
        )
        return (
            cost
            .rename({'value': 'cost'})
            .join(base, on=index, how='left')
            .select(
                *index,
                pl.all().exclude(*index, 'cost', 'base'),
                'cost',
                'base',
                (pl.col('cost') - pl.col('base')).alias('increase'),
            )
            .with_columns(increase_per_area=pl.col('increase').truediv('연면적(㎡)'))
        )

    def write(self, data: pl.DataFrame):
        dst = self.dst or self.src.parent / f'{self.src.stem}-tidy.xlsx'

        with xlsxwriter.Workbook(dst) as wb:
            data.write_excel(wb, 'raw', column_widths=self.column_widths)

            # 공사비
            cost = self._cost(data)
            cost.write_excel(wb, 'cost', column_widths=self.column_widths)

            # 등급별 공사비
            (
                (cost)
                .filter(pl.col('grade') != '1+')
                .group_by('grade', maintain_order=True)
                .agg(pl.mean('cost', 'base', 'increase', 'increase_per_area'))
                .with_columns(
                    pl.format('{}등급', pl.col('grade').str.strip_prefix('ZEB'))
                )
                .rename({
                    'grade': '등급',
                    'cost': '공사비',
                    'base': '기준공사비',
                    'increase': '증가액',
                    'increase_per_area': '증가액/연면적[원/m²]',
                })
                .write_excel(wb, 'cost-avg', column_widths=self.column_widths * 2)
            )

    def __call__(self):
        data = (
            (pl)
            .concat(self._read_tables(), how='vertical_relaxed')
            .with_columns(
                pl.col('region').str.strip_suffix(self.header_suffix),
                pl.col('variable').replace(self.REPLACE),
            )
        )

        self.write(data)

        return data


class V(enum.StrEnum):
    scenario = '시나리오'
    variable = '변수'
    use = '용도'

    emission = '온실가스[ktCO₂]'
    energy = '에너지[kTOE]'
    cost = '추가공사비[백만원]'


@app.command
@dc.dataclass
class Agg:
    """Agg."""

    src: ExistingPath
    dst: Path | None = None
    base: int = 2018
    cost_base: int = 2025
    max_year: int = 2050

    figsize: tuple[float, float] = (27.5, 7)

    @functools.cached_property
    def index(self):
        return [V.scenario, V.variable, V.use]

    @functools.cached_property
    def data(self):
        return (
            pl
            .read_excel(self.src)
            .unpivot(index=self.index, variable_name='year')
            .with_columns(pl.col('year').cast(pl.UInt16))
        )

    @functools.cached_property
    def energy_emission(self):
        return self.data.filter(pl.col(V.variable) != V.cost)

    @functools.cached_property
    def cost(self):
        cost = (
            (self.data)
            .filter(pl.col(V.variable) == V.cost)
            .with_columns(
                pl.col('value').truediv(100),
                pl.col(V.variable).replace_strict({
                    V.cost: V.cost.replace('백만원', '억원')
                }),
            )
        )

        # 2025년부터 누적 비용 산정
        cost = (
            (cost)
            .with_columns(
                pl
                .col('value')
                .filter(pl.col('year') == self.cost_base - 1)
                .sum()
                .over(self.index)
                .alias('base')
            )
            .with_columns(pl.col('value').sub('base').alias('cost'))
        )
        assert (
            cost
            .filter(pl.col('year') == self.cost_base - 1)
            .select(pl.col('cost').eq(0).all())
            .item()
        )
        return cost.drop('value', 'base').rename({'cost': 'value'})

    @functools.cached_property
    def tidy(self):
        return pl.concat([self.energy_emission, self.cost]).sort(pl.all())

    @functools.cached_property
    def agg(self):
        data = (
            (self.energy_emission)
            .with_columns(
                pl
                .col('value')
                .filter(pl.col('year') == self.base)
                .sum()
                .over(self.index)
                .alias('base'),
                pl
                .col('value')
                .filter(pl.col('year') == self.max_year)
                .sum()
                .over(self.index)
                .alias('value'),
            )
            .filter(pl.col('year') == self.max_year)
            .with_columns(diff=pl.col('value') - pl.col('base'))
            .with_columns(ratio=pl.col('diff').truediv('base'))
            .unpivot(['diff', 'base', 'value', 'ratio'], index=self.index)
            .with_columns(
                pl.col('variable').replace({
                    'base': '0.base',
                    'value': '1.value',
                    'diff': '2.diff',
                    'ratio': '3.ratio',
                })
            )
        )

        rename = {'기본': '0.기본'} | {f'강화{x}': f'{x}.강화{x}' for x in range(1, 6)}
        return (
            pl
            .concat(
                [data, self.cost.filter(pl.col('year') == self.max_year).drop('year')],
                how='diagonal',
            )
            .with_columns(
                pl.col(V.scenario).replace(rename),
                pl.col('variable').fill_null('value'),
            )
            .pivot(V.scenario, index=[V.variable, 'variable', V.use], values='value')
            .sort(pl.all())
        )

    def plot(self, *, flip: bool = False):
        cost = V.cost.replace('백만원', '억원')
        data = (
            self.agg
            .filter(
                (pl.col(V.variable).eq(V.emission) & pl.col('variable').eq('2.diff'))
                | (pl.col(V.variable) == cost)
            )
            .drop('variable', V.use)
            .unpivot(index=V.variable, variable_name=V.scenario)
            .group_by([V.variable, V.scenario])
            .agg(pl.sum('value'))
            .with_columns(
                pl.col(V.variable).replace({cost: 'cost', V.emission: 'emission'}),
                pl.col('value').abs(),
            )
            .pivot(V.variable, index=V.scenario, values='value')
            .with_columns(pl.col('cost') / 10000)
        )

        fig = Figure()
        ax = fig.subplots()

        for x, y in data.select(
            ['cost', 'emission'] if flip else ['emission', 'cost']
        ).iter_rows():
            ax.plot([0, x], [0, y], c='gray', ls='--', alpha=0.5)

        sns.scatterplot(
            data,
            x='cost' if flip else 'emission',
            y='emission' if flip else 'cost',
            ax=ax,
        )

        ax.set_xlim(0)
        ax.set_ylim(0)

        xy = ('온실가스 감축량 [$ktCO_2$]', '추가 공사비 [조원]')
        if flip:
            xy = xy[::-1]

        ax.set_xlabel(xy[0])
        ax.set_ylabel(xy[1])

        return fig

    def __call__(self):
        utils.mpl.MplTheme(fig_size=self.figsize).grid().apply(rc={'axes.ymargin': 0.1})
        stem = self.src.stem
        dst = self.dst or self.src.parent

        with xlsxwriter.Workbook(dst / f'{stem}-agg.xlsx') as wb:
            self.data.write_excel(wb, 'raw')
            self.tidy.write_excel(wb, 'tidy', column_widths=100)
            self.agg.write_excel(wb, 'agg', column_widths=100)

        fig = self.plot(flip=False)
        fig.savefig(dst / f'{stem}-plot.png')

        fig = self.plot(flip=True)
        fig.savefig(dst / f'{stem}-plot-flip.png')


if __name__ == '__main__':
    app()
