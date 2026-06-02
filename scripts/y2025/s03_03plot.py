"""시각화(최종보고)."""

import dataclasses as dc
import enum
import functools
from typing import TYPE_CHECKING, ClassVar

import cyclopts
import matplotlib.pyplot as plt
import polars as pl
import seaborn as sns
import xlsxwriter
from cyclopts.types import ExistingPath  # noqa: TC002
from matplotlib.figure import Figure
from matplotlib.layout_engine import ConstrainedLayoutEngine
from matplotlib.ticker import PercentFormatter

from scripts.y2025.common import Grade
from zeb import utils
from zeb.utils.cli import App

if TYPE_CHECKING:
    from collections.abc import Iterable


class V(enum.StrEnum):
    variable = 'variable'
    value_ = 'value'

    building_use = 'building-use'
    ownership = 'ownership'
    scale = 'scale'
    use = 'use'
    bldg = 'bldg'
    region = 'region'
    pv_installed = 'pv-installed'
    grade = 'grade'
    grade_index = 'grade-index'
    emission_reference = 'emission_reference'

    source = 'source'
    floor_area = '연면적'

    requirement = '소요량'
    emission = '배출량'


class Category:
    USE = ('단독주택', '공동주택', '교육사회', '상업', '기타')
    GRADE = ('Baseline', 'ZEB5', 'ZEB4', 'ZEB3', 'ZEB2', 'ZEB1', 'ZEB+')
    GRADE_SUB = ('Baseline', '준ZEB5', 'ZEB5', 'ZEB4', 'ZEB3', 'ZEB2', 'ZEB1', 'ZEB+')

    @staticmethod
    def order(name: str, v: Iterable):
        return (
            (pl)
            .col(name)
            .replace_strict({x: i for i, x in enumerate(v)}, return_dtype=pl.UInt8)
        )


app = App(
    help_on_error=True,
    config=[
        cyclopts.config.Toml(
            'config.toml',
            root_keys='emission-plot',
            use_commands_as_keys=x,
            allow_unknown=True,
        )
        for x in [False, True]
    ],
    result_action=['call_if_callable', 'print_non_int_sys_exit'],
)


@dc.dataclass
class _Base:
    src: ExistingPath  # emission.parquet
    dst: ExistingPath | None = None

    @functools.cached_property
    def _dst(self):
        return self.dst or self.src.parent / 'plot'


@app.command
@dc.dataclass
class EDA(_Base):
    """EDA."""

    @functools.cached_property
    def categorized(self):
        return (
            (pl)
            .scan_parquet(self.src.parent / f'{self.src.stem}-categorized.parquet')
            .filter(pl.col(V.pv_installed))
        )

    def elec_ratio(self):
        """주거/비주거 전력 소요량 비율 검토."""
        index = [
            'building-use',
            'ownership',
            'scale',
            'scale1',
            'scale2',
            'use',
            'bldg',
            'region',
            'grade',
        ]
        src = self.src.parent / f'{self.src.stem}-categorized.parquet'
        data = (
            pl
            .scan_parquet(src)
            .filter(pl.col(V.pv_installed), pl.col('requirement') >= 0)
            .drop_nulls('source')
            .group_by([*index, 'source'])
            .agg(pl.sum('requirement'))
            .collect()
            .sort('building-use', 'grade')
            .with_columns(total=pl.sum('requirement').over(index))
            .filter(pl.col('source') == '전력')
            .with_columns(ratio=pl.col('requirement') / pl.col('total'))
        )

        grid = sns.displot(
            data,
            x='ratio',
            hue='grade',
            row='building-use',
            kind='hist',
            height=4,
            aspect=16 * 2 / 9,
        )
        grid.axes.ravel()[0].set_xlim(0, 1)
        grid.savefig(self._dst / '0001.전력비율.png')

    def residential_energy_source(self):
        """주거 건물 에너지원 비율."""
        index = [
            'building-use',
            'ownership',
            'scale',
            'scale1',
            'scale2',
            'use',
            'bldg',
            'region',
            'grade',
        ]

        data = (
            self.categorized
            .filter(
                pl.col(V.pv_installed),
                pl.col(V.emission_reference) == 'reference',
                pl.col('requirement') >= 0,
            )
            .drop_nulls('source')
            .group_by([*index, 'source'])
            .agg(pl.sum('requirement'))
            .with_columns(
                pl.col(V.building_use).replace({
                    'residential': '주거',
                    'non-residential': '비주거',
                }),
                requirement_total=pl.sum('requirement').over([*index]),
            )
            .with_columns(
                requirement_ratio=pl.col('requirement') / pl.col('requirement_total')
            )
            .sort(pl.all())
            .collect()
        )

        agg = (
            data
            .group_by(V.building_use, V.use, V.grade, 'source')
            .agg(pl.mean('requirement_ratio'))
            .sort(pl.all())
            .pivot(
                'source',
                index=[V.building_use, V.use, V.grade],
                values='requirement_ratio',
                sort_columns=True,
            )
        )

        with xlsxwriter.Workbook(self._dst / '0001.에너지원 비율.xlsx') as wb:
            data.write_excel(wb, '건물별', column_widths=100)
            agg.write_excel(wb, '유형별', column_widths=100)

    def count(self):
        """유형별 건물 수 -> 대표 모델 선정."""
        index = [V.building_use, V.ownership, V.scale, V.use]
        data = (
            pl
            .scan_parquet(self.src)
            .filter(
                pl.col(V.pv_installed),
                pl.col(V.region) == '중부1',
                pl.col(V.grade) == 'Baseline',
                pl.col(V.emission_reference) == 'ECO2',
            )
            .group_by(index)
            .len()
            .sort(index)
            .collect()
        )
        data.write_excel(self._dst / '0002.샘플 수.xlsx', column_widths=100)

    def requirement_by_source(self):
        index = [
            'building-use',
            'ownership',
            'scale',
            'scale1',
            'scale2',
            'use',
            'bldg',
            'region',
            'grade',
        ]
        source = ['전력', '지역난방', 'LNG', 'LPG', '등유']
        data = (
            (self.categorized)
            .with_columns(
                area=pl
                .col('value')
                .filter(pl.col('variable') == V.floor_area)
                .sum()
                .over(index)
            )
            .filter(
                pl.col(V.grade) == 'Baseline',
                pl.col(V.source).replace({'지역냉방': '지역난방'}).is_in(source),
            )
            .drop_nulls('requirement')
            .group_by([*index, V.source, 'area'])
            .agg(pl.sum('requirement'))
            .with_columns(
                pl.col('requirement').truediv('area').alias(V.requirement),
                pl.col(V.use).replace({'공동': '공동주택', '단독': '단독주택'}),
            )
            .collect()
        )

        fig = Figure()
        ax = fig.subplots()
        sns.barplot(
            data,
            x='use',
            y=V.requirement,
            hue='source',
            ax=ax,
            order=['단독주택', '공동주택', '교육사회', '상업', '기타'],
            hue_order=source,
        )
        ax.set_xlabel('')
        ax.set_ylabel('연간 에너지 소요량 [kWh/m²]')
        if legend := ax.get_legend():
            legend.set_title('')

        fig.savefig(self._dst / '0003.에너지원별 소요량.png')

    def __call__(self):
        self._dst.mkdir(exist_ok=True)
        self.elec_ratio()
        self.residential_energy_source()
        self.count()
        self.requirement_by_source()


@app.command
@dc.dataclass
class Reduction(_Base):
    """용도별 평균 절감량."""

    figsize: tuple[float, float] = (16, 9)

    @functools.cached_property
    def raw(self):
        return (
            pl
            .scan_parquet(self.src)
            .filter(pl.col(V.pv_installed), pl.col(V.grade) != Grade.SUB5)
            .with_columns(pl.col(V.emission).truediv(V.floor_area).alias(V.emission))
            .unpivot(
                [V.requirement, V.emission],
                index=[V.use, V.grade, V.emission_reference, V.floor_area],
            )
            .filter(
                ~(
                    pl.col(V.variable).eq('소요량')
                    & pl.col(V.emission_reference).eq('reference')
                )
            )
            .with_columns(
                pl
                .when(pl.col(V.variable).eq('소요량'))
                .then(pl.lit(None))
                .otherwise(V.emission_reference)
                .alias(V.emission_reference)
            )
            .with_columns(
                pl
                .format(
                    '{}({})', V.variable, pl.col(V.emission_reference).fill_null('')
                )
                .str.strip_suffix('()')
                .alias('variable-reference')
            )
            .group_by([
                V.use,
                V.grade,
                V.emission_reference,
                V.variable,
                'variable-reference',
            ])
            .agg(pl.mean(V.value_))
            .with_columns(
                pl.col(V.use).replace({'단독': '단독주택', '공동': '공동주택'})
            )
            .sort(
                Category.order(V.use, Category.USE),
                Category.order(V.grade, Category.GRADE_SUB),
            )
            .collect()
        )

    def plot_value(self, variable: str):
        """소요량, 배출량 그래프."""
        data = self.raw.filter(pl.col('variable-reference') == variable)

        fig = Figure()
        ax = fig.subplots()
        sns.pointplot(data, x=V.grade, y=V.value_, hue=V.use, ax=ax, alpha=0.8)

        if legend := ax.get_legend():
            legend.set_title('')

        ax.set_xlabel('')
        ax.set_ylabel(
            '소요량 [kWh/m²]' if variable == '소요량' else '배출량 [$kgCO_2$/m²]'
        )

        if variable == '소요량':
            ax.set_ylim(0)

        return fig

    def plot_ratio(self, variable: str):
        """소요량, 배출량 저감률 그래프."""
        data = (
            self.raw
            .filter(pl.col('variable-reference') == variable)
            .with_columns(
                base=pl
                .col('value')
                .filter(pl.col(V.grade) == Grade.BASE)
                .sum()
                .over(V.use)
            )
            .filter(pl.col(V.grade) != Grade.BASE)
            .with_columns(reduction=pl.col('base').sub('value') / pl.col('base'))
        )

        fig = Figure()
        ax = fig.subplots()
        sns.pointplot(data, x=V.grade, y='reduction', hue=V.use, ax=ax, alpha=0.8)

        ax.set_ylim(0, 1 if variable == '소요량' else 1.2)
        ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0))

        if legend := ax.get_legend():
            legend.set_title('')

        ax.set_xlabel('')
        ax.set_ylabel('소요량 절감률' if variable == '소요량' else '배출량 저감률')

        return data, fig

    def __call__(self):
        utils.mpl.MplTheme(palette='tol:bright', fig_size=self.figsize).grid().apply()

        for v in ['소요량', '배출량(ECO2)', '배출량(reference)']:
            fig = self.plot_value(v)
            fig.savefig(self._dst / f'0101.value {v} {self.figsize}.png')

            ratio, fig = self.plot_ratio(v)
            ratio.write_excel(self._dst / f'0101.reduction-rate {v}.xlsx')
            fig.savefig(self._dst / f'0101.reduction-rate {v} {self.figsize}.png')


@app.command
@dc.dataclass
class Emission(_Base):
    """PV, 직접, 간접 배출량 비교."""

    figsize: tuple[float, float] = (20, 15)

    PV: ClassVar[str] = 'PV배출량'
    EMISSION_AGG: ClassVar[dict[str, str]] = {
        '직접배출량': '1.직접',
        '간접배출량': '2.간접',
        PV: '3.신재생',
        '배출량': '4.총',
    }
    EMISSION_PLOT: ClassVar[dict[str, str]] = {
        '직접배출량': '직접 배출량',
        '간접배출량': '간접 배출량',
        PV: '신재생 설비 배출 저감량',
        '배출량': '총 배출량',
    }

    @functools.cached_property
    def raw(self):
        grades = {g: i for i, g in enumerate(Grade)}
        return (
            pl
            .scan_parquet(self.src)
            .filter(pl.col(V.pv_installed), pl.col(V.grade) != Grade.SUB5)
            .unpivot(
                list(self.EMISSION_PLOT.keys()),
                index=[V.use, V.grade, V.emission_reference, V.floor_area],
            )
            .with_columns(pl.col(V.value_).truediv(V.floor_area).alias(V.value_))
            .group_by([
                V.use,
                V.grade,
                V.emission_reference,
                V.variable,
            ])
            .agg(pl.mean(V.value_))
            .with_columns(
                pl.col(V.use).replace({'단독': '단독주택', '공동': '공동주택'})
            )
            .sort(
                Category.order(V.use, Category.USE),
                Category.order(V.grade, Category.GRADE_SUB),
            )
            .collect()
            .insert_column(
                1,
                pl
                .col(V.grade)
                .replace_strict(grades, return_dtype=pl.UInt8)
                .alias(V.grade_index),
            )
        )

    def plot(self, reference: str):
        data = (
            (self.raw)
            .filter(pl.col(V.emission_reference) == reference)
            .with_columns(
                pl.col(V.variable).replace_strict(self.EMISSION_PLOT),
                pl
                .col(V.value_)
                .mul(pl.col(V.variable).replace_strict(self.PV, -1, default=1))
                .alias(V.value_),
            )
        )
        grid = (
            sns
            .FacetGrid(
                data,
                hue=V.use,
                col=V.variable,
                col_wrap=2,
                col_order=list(self.EMISSION_PLOT.values()),
                despine=False,
            )
            .map_dataframe(sns.pointplot, x=V.grade, y=V.value_, alpha=0.8)
            .set_titles('{col_name}', weight=500)
            .set_axis_labels('', '배출량 [$kgCO_2$/m²]')
            .add_legend(title='', frameon=True)
        )

        grid.figure.set_size_inches(self.figsize[0] / 2.54, self.figsize[1] / 2.54)
        utils.mpl.move_legend_fig_to_ax(
            grid.figure,
            grid.axes.ravel()[-1],
        )
        ConstrainedLayoutEngine().execute(grid.figure)

        return grid

    def agg(self):
        index = [V.emission_reference, V.use, V.grade_index, V.grade]
        raw = self.raw.with_columns(
            pl.col(V.variable).replace_strict(self.EMISSION_AGG)
        )

        # 배출량 평균
        avg = raw.pivot(V.variable, index=index, values=V.value_, sort_columns=True)
        diff = (
            raw
            .with_columns(
                pl
                .col(V.value_)
                .filter(pl.col(V.grade) == Grade.BASE)
                .sum()
                .over([V.use, V.emission_reference, V.variable])
                .alias(Grade.BASE)
            )
            .with_columns((pl.col(V.value_).sub(Grade.BASE)).alias(V.value_))
            .pivot(V.variable, index=index, values=V.value_, sort_columns=True)
        )
        ratio = (
            raw
            .with_columns(
                pl
                .col(V.value_)
                .filter(pl.col(V.grade) == Grade.BASE)
                .sum()
                .over([V.use, V.emission_reference, V.variable])
                .alias(Grade.BASE)
            )
            .with_columns((pl.col(V.value_).truediv(Grade.BASE)).alias(V.value_))
            .pivot(V.variable, index=index, values=V.value_, sort_columns=True)
        )
        with xlsxwriter.Workbook(self._dst / '0102.emission.xlsx') as wb:
            for df, name in zip(
                [avg, diff, ratio], ['배출량', '편차', '변화율'], strict=True
            ):
                df.sort(pl.all()).write_excel(wb, name, column_widths=100)

            ratio_rename = {x: f'{x} 변화율' for x in self.EMISSION_AGG.values()}
            (
                avg
                .join(ratio.rename(ratio_rename), on=index)
                .sort(pl.all())
                .write_excel(wb, '배출량&변화율', column_widths=100)
            )

    def __call__(self):
        (
            utils.mpl
            .MplTheme(context='paper', palette='tol:bright', fig_size=self.figsize)
            .grid()
            .apply()
        )

        self.agg()

        for v in ['ECO2', 'reference']:
            grid = self.plot(v)
            grid.savefig(self._dst / f'0102.value by scope {v} {self.figsize}.png')
            plt.close(grid.figure)


if __name__ == '__main__':
    utils.mpl.MplTheme().grid().apply()
    app()
