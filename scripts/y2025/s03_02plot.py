"""시각화(중간보고)."""

import dataclasses as dc
import functools
import itertools
import re
import warnings
from typing import TYPE_CHECKING, ClassVar, Literal

import cyclopts
import matplotlib.pyplot as plt
import polars as pl
import seaborn as sns
from cyclopts.types import ExistingDirectory  # noqa: TC002
from loguru import logger
from matplotlib import patheffects
from matplotlib.container import BarContainer
from matplotlib.figure import Figure
from matplotlib.ticker import PercentFormatter

from scripts.y2025.common import Grade, ZebPalette
from zeb import utils
from zeb.utils.cli import App

if TYPE_CHECKING:
    from collections.abc import Callable

    from matplotlib.axes import Axes

BuildingUse = Literal['residential', 'non-residential']
InspectVariableType = Literal['value', 'delta', 'ratio']
InspectVariable = Literal['소요량', '소비량', '탄소배출량']
Area = Literal['floor', 'light']


BUILDING_USES: tuple[BuildingUse, ...] = ('residential', 'non-residential')
PLOT_VARIABLE_TYPES: tuple[InspectVariableType, ...] = ('value', 'delta', 'ratio')

CASE_PATTERN = r'(공|민)(-C\d)?-A\d-([가-힣]|\d)+-\d+'


class _NullError(ValueError):
    pass


app = App(
    config=[
        cyclopts.config.Toml(
            'config.toml',
            root_keys='emission',
            use_commands_as_keys=x,
            allow_unknown=True,
        )
        for x in [False, True]
    ],
    result_action=['call_if_callable', 'print_non_int_sys_exit'],
    help_on_error=True,
)


def _bar_label(
    ax: Axes,
    fmt: str | Callable = '%.1f',
    padding: float = 10,
    fontsize: str | float = 'x-small',
):
    path_effects = [patheffects.withStroke(linewidth=2, foreground='#FFFE')]

    for container in ax.containers:
        if not isinstance(container, BarContainer):
            raise TypeError(container)

        ax.bar_label(
            container,
            fmt=fmt,
            padding=padding,
            fontsize=fontsize,
            weight=450,
            color='#0008',
            path_effects=path_effects,
        )


@dc.dataclass(frozen=True)
class InspectCase:
    use: BuildingUse
    between: str
    group: str | None

    @classmethod
    def iter(cls):
        for use in BUILDING_USES:
            for b, g in [
                ('scale', None),
                ('ownership', None),
                ('use', None),
                ('function', None),
                ('source', None),
                ('grade', None),
                ('source', 'use'),
            ]:
                yield cls(use, b, g)

    def __str__(self):
        use = self.use
        between = self.between
        group = self.group
        return f'{use=!s} {between=!s} {group=!s}'.removesuffix(' group=None')


@dc.dataclass
class InspectVars:
    type: InspectVariableType
    value: InspectVariable
    case: InspectCase
    unit: str

    @property
    def value_label(self):
        match self.type:
            case 'value':
                label = f'{self.value} [{self.unit}]'
            case 'delta':
                label = f'{self.value} 저감량 [{self.unit}]'
            case 'ratio':
                label = f'{self.value} 저감률'

        return label

    @property
    def between_label(self):
        labels = {
            'function': '',
            'scale': '규모',
            'ownership': '소유',
            'use': '용도',
            'source': '에너지원',
            'grade': '등급',
        }
        return labels.get(self.case.between, self.case.between)

    @property
    def title(self):
        use = {'residential': '주거', 'non-residential': '비주거'}[self.case.use]
        s = f'{self.between_label}별 {use} {self.value}'
        return s.replace('소유별', '소유 유형별')


@app.command
@dc.dataclass(frozen=True)
class Inspect:
    """
    Baseline 대비 시뮬레이션 결과 분포 검토를 위한 그래프.

    유형별 절감률은 별도 산정 필요.
    """

    src: ExistingDirectory
    dst: ExistingDirectory | None = None

    _: dc.KW_ONLY

    emission_reference: Literal['ECO2', 'reference'] = 'reference'

    pv: bool = True
    bipv_limit: float | None = None
    """외벽면적 대비 BIPV 면적 한계."""

    area: Area = 'floor'
    kind: Literal['box', 'bar'] = 'bar'
    title: bool = True
    annot: bool = True

    width: float = 15
    height: float = 12

    palette: ZebPalette = dc.field(default_factory=ZebPalette)

    INDEX: ClassVar[tuple[str, ...]] = (
        'building-use',
        'ownership',
        'scale',
        'scale1',
        'scale2',
        'use',
        'bldg',
        'region',
    )
    KWARGS: ClassVar[dict] = {
        'box': {
            'flierprops': {
                'alpha': 0.5,
                'markeredgecolor': '#757575',
                'markeredgewidth': 0.5,
                'markersize': 3,
                'zorder': 0,
            },
        },
        'bar': {
            'width': 0.85,
            'linewidth': 0.5,
            'edgecolor': 'whitesmoke',
            'err_kws': {'color': '#0008', 'linewidth': 0.8},
            'capsize': 0.4,
        },
    }

    @property
    def _raw(self):
        return (
            pl
            .scan_parquet(self.src / '01.emission-categorized.parquet')
            .filter(
                pl.col('pv-installed') == self.pv,
                pl.col('emission_reference') == self.emission_reference,
            )
            .with_columns(pl.col('scale1').fill_null('-'))
        )

    @property
    def raw(self):
        # 주거의 경우 scale1, scale2당 각각 건물 번호(bldg)가 할당되기 때문에
        # scale1, scale2 모두 index로 사용해야 함
        lf = self._raw.join(
            self._area.lazy(), on=[*self.INDEX, 'grade'], how='left', validate='m:1'
        )

        if self.bipv_limit:
            lf = lf.filter(pl.col('bipv-area') <= self.bipv_limit * pl.col('wall-area'))

        return lf

    @functools.cached_property
    def output(self):
        kind = '' if self.kind == 'box' else '-bar'
        pv = '' if self.pv else '-NoPV'
        return self.dst or self.src / f'01.inspect{kind}{pv}'

    @functools.cached_property
    def _bipv_suffix(self):
        if self.bipv_limit is None:
            return ''

        return f' BIPV le {self.bipv_limit}'

    @functools.cached_property
    def _area(self):
        variables = {
            '연면적': 'floor-area',
            '사용면적(조명)': 'light-area',
            'BIPV면적': 'bipv-area',
            '외벽면적': 'wall-area',
        }
        index = [*self.INDEX, 'grade']
        return (
            self._raw
            .filter(pl.col('variable').is_in(variables.keys()))
            .select(*index, pl.col('variable').replace_strict(variables), 'value')
            .collect()
            .pivot('variable', index=index, values='value', sort_columns=True)
        )

    @functools.cached_property
    def requirement(self):
        lf = (
            self.raw.filter(
                # '그래프' 파일에 저장된 연간 소요량
                pl.col('variable') == '소요량',
                pl.col('group').str.starts_with('그래프-연간'),
            )
            # NOTE
            # '그래프' 파일에 표시된 소요량은 신재생 생산량이 *제외된 값*임
            # `합계`에 생산량을 더해주기 위해
            # 음수로 표기된 `신재생에너지`를 양수로 변환
            .with_columns(
                pl
                .when(pl.col('function') == '신재생')
                .then(pl.col('value').abs())
                .otherwise(pl.col('value'))
                .alias('value')
            )
        )

        index = [*self.INDEX, 'function']
        base = (
            lf
            .filter(pl.col('grade') == Grade.BASE)
            .select([*index, 'value'])
            .rename({'value': Grade.BASE})
        )

        return lf.join(base, on=index, how='left', validate='m:1').collect()

    @functools.cached_property
    def emission(self):
        lf = (
            self.raw
            .filter(
                # '계산결과' 파일
                pl.col('group') == '1차에너지 소요량',
                pl.col('function') != '생산-열',
            )
            .drop('value')
            .drop_nulls('source')
            .with_columns(value=pl.col('emission') / pl.col(f'{self.area}-area'))
        )

        index = [*self.INDEX, 'variable', 'function', 'source']
        base = (
            lf
            .filter(pl.col('grade') == Grade.BASE)
            .select([*index, 'value'])
            .rename({'value': Grade.BASE})
            .unique()
        )
        return lf.join(base, on=index, how='left', validate='m:1').collect()

    def _plot_data(self, data: pl.DataFrame, case: InspectCase, variables: InspectVars):
        data = (
            (data)
            .filter(pl.col('building-use') == case.use)
            .with_columns(
                between=pl.col(case.between)
                if isinstance(case.between, str)
                else pl.concat_str(case.between, separator='-'),
            )
            .group_by([*self.INDEX, 'grade', 'between'], maintain_order=True)
            .agg(pl.sum('value', Grade.BASE))
        )

        if variables.type != 'value':
            data = (
                (data)
                .with_columns(
                    # '저감량', '저감률' 양수로 계산
                    delta=pl.col(Grade.BASE) - pl.col('value'),
                    ratio=1 - pl.col('value') / pl.col(Grade.BASE),
                )
                .filter(pl.col('grade') != Grade.BASE)
            )

        if data['between'].is_null().all():
            raise _NullError(case)

        return data

    def plot(self, data: pl.DataFrame, case: InspectCase, variables: InspectVars):
        data = self._plot_data(data, case, variables)

        if case.group is not None:
            data = data.filter(pl.col(variables.type) != 0)

        grades = [x for x in Grade if x in data['grade'].unique().to_list()]
        kwargs: dict = (
            self.KWARGS[self.kind]
            | {'data': data, 'x': variables.type, 'y': 'between'}
            | (
                {'color': 'mediumseagreen', 'order': grades}
                if case.between == 'grade'
                else {
                    'hue': 'grade',
                    'hue_order': grades,
                    'palette': self.palette.colors,
                }
            )
        )

        if case.group is None:
            fig = Figure()
            ax = fig.subplots()

            fn = sns.boxplot if self.kind == 'box' else sns.barplot
            fn(ax=ax, **kwargs)

            ax.set_xlabel(variables.value_label)
            if self.title:
                ax.set_title(variables.title, loc='left', weight=500)
                ax.set_ylabel('')
            else:
                ax.set_ylabel(variables.between_label)

            if 'hue' in kwargs:
                ax.legend(loc='center left', bbox_to_anchor=(1, 0.5), title='')
        else:
            col_wrap = utils.mpl.ColWrap(data.select(case.group).n_unique())
            grid = (
                (sns)
                .catplot(
                    col=case.group,
                    col_wrap=int(col_wrap),
                    kind=self.kind,
                    height=3,
                    aspect=4 / 3,
                    **kwargs,
                )
                .set_axis_labels(variables.value_label, variables.between_label)
            )

            if grid.legend:
                grid.legend.set_title('')

            fig = grid.figure

        for ax in fig.axes:
            if variables.type != 'delta' and ax.get_xlim()[0] > 0:
                ax.set_xlim(0)
            if variables.type == 'ratio':
                ax.xaxis.set_major_formatter(PercentFormatter(xmax=1.0))

            if self.annot and self.kind == 'bar':
                _bar_label(ax)

        return fig

    def _plot(
        self,
        case: InspectCase,
        data: pl.DataFrame,
        var: InspectVariable,
        unit: str,
    ):
        # NOTE function, source별 계산 결과 검토를 위해 전처리
        # 최종 절감량/률은 function/source 정보를 제외하고 별도 계산 필요
        fn = pl.col('function')
        match var, case.between:
            case (('소요량' | '소비량'), 'function'):
                function = ('난방', '냉방', '급탕', '조명', '환기', '합계', '신재생')
                order = {x: i for i, x in enumerate(function)}
                data = data.sort(fn.replace_strict(order))
            case '소요량', _:
                data = data.filter(fn == '합계')
            case '소비량', _:
                # 합계와 생산량(절대값)을 합한 총 소요량 계산
                data = data.filter(fn.is_in(['합계', '신재생']))

        name = f'{var} {case}{self._bipv_suffix}'

        for t in PLOT_VARIABLE_TYPES:
            variables = InspectVars(type=t, value=var, case=case, unit=unit)

            try:
                fig = self.plot(data, case=case, variables=variables)
            except _NullError:
                logger.debug('Null case: {} var={}', case, var)
                return
            else:
                fig.savefig(self.output / f'{t} {name}.png')
                plt.close(fig)

    def __call__(self):
        warnings.filterwarnings('ignore', 'The figure layout has changed to tight')
        (
            utils.mpl
            .MplTheme(fig_size=(self.width, self.height))
            .grid()
            .apply({'lines.solid_capstyle': 'butt', 'axes.xmargin': 0.1})
        )

        output = self.output
        output.mkdir(exist_ok=True)

        self.requirement.head(1000).write_excel(output / '(sample)requirement.xlsx')
        self.emission.head(1000).write_excel(output / '(sample)emission.xlsx')

        for case in InspectCase.iter():
            logger.info(repr(case))
            self._plot(case, data=self.requirement, var='소요량', unit='kWh/m²')
            self._plot(case, data=self.requirement, var='소비량', unit='kWh/m²')
            self._plot(case, data=self.emission, var='탄소배출량', unit='kg/m²')


@dc.dataclass(frozen=True)
class GroupReprParam:
    area: Area
    baseline: Literal[Grade.BASE, Grade.SUB5]
    reference: Literal['ECO2', 'reference']
    variable: Literal['소요량', '배출량'] | str  # noqa: PYI051
    clip: bool = False
    ownership: bool = True

    @property
    def name(self):
        ownership = 'ownership-' if self.ownership else ''
        return (
            f'{ownership}{self.area}-{self.baseline}-'
            f'{self.reference}-{self.variable}-'
            f'clip={self.clip}'
        )

    @classmethod
    def iter(cls):
        for a, b, r, v, c, o in itertools.product(
            ('floor',),
            (Grade.BASE,),
            ('ECO2', 'reference'),
            (
                '소요량',
                '배출량',
                '직접배출량',
                '간접배출량',
                'PV배출량',
                'PV배출량&직접배출량',
                'PV배출량&간접배출량',
            ),
            (False,),
            (True, False),
        ):
            if v == '소요량' and a == 'light':
                continue

            yield cls(area=a, baseline=b, reference=r, variable=v, clip=c, ownership=o)  # type: ignore[arg-type]


@app.command
@dc.dataclass
class GroupReprEmission:
    root: ExistingDirectory

    _: dc.KW_ONLY

    pv: bool = True
    bipv_limit: float | None = None
    """외벽면적 대비 BIPV 면적 한계."""

    fig_width: float = 25

    INDEX: ClassVar[tuple[str, ...]] = (
        'ownership',
        'scale',
        'scale1',
        'scale2',
        'use',
        'bldg',
        'region',
    )

    @functools.cached_property
    def data(self):
        lf = (
            pl
            .scan_parquet(self.root / '01.emission.parquet')
            .filter(pl.col('pv-installed') == self.pv)
            .with_columns(
                pl
                .when(pl.col('building-use') == 'residential')
                .then(pl.format('{}주택', 'use'))
                .otherwise(pl.col('use'))
                .alias('use')
            )
            .drop('building-use')
        )

        if self.bipv_limit:
            lf = lf.filter(pl.col('BIPV면적') <= self.bipv_limit * pl.col('외벽면적'))

        return lf.collect()

    @property
    def output(self):
        suffix = '' if self.pv else '-NoPV'
        return self.root / f'02.group-repr-emission{suffix}'

    def compare(self, param: GroupReprParam):
        value = (
            pl.sum_horizontal(param.variable.split('&'))
            if '&' in param.variable
            else pl.col(param.variable)
        )
        if param.variable != '소요량':
            value /= pl.col('연면적' if param.area == 'floor' else '조명면적')

        raw = (
            self.data
            .with_columns(
                (pl.col('PV배출량') + pl.col('직접배출량')).alias('신재생&직접배출량')
            )
            .with_columns(value=value)
            .drop('소요량', '소비량', '배출량', '연면적', '조명면적')
        )

        grade = pl.col('grade')
        base = (
            raw
            .filter(
                grade == param.baseline,
                pl.col('emission_reference') == param.reference,
            )
            .select([*self.INDEX, 'value'])
            .rename({'value': 'baseline'})
        )
        zeb = raw.filter(grade.str.starts_with('ZEB'))

        order = ['단독주택', '공동주택', '교육사회', '상업', '기타']
        group = ['grade', 'use', 'scale', 'row', 'col', 'col2']
        if param.ownership:
            group.append('ownership')

        data = (
            zeb
            .join(base, on=self.INDEX, how='left', validate='m:1')
            .filter(pl.col('baseline') != 0.0)  # noqa: RUF069  # XXX
            .with_columns(
                idx=pl.col('use').replace_strict({x: i for i, x in enumerate(order)})
            )
            .with_columns(
                row=pl.format('{} {}', 'ownership', 'grade')
                if param.ownership
                else pl.col('grade'),
                col=pl.format('{} {}', 'use', 'scale'),
                col2=pl.format('#{} {} {}', 'idx', 'use', 'scale'),
            )
            .group_by(group)
            .agg(
                pl.sum('value', 'baseline').name.suffix('-sum'),
                pl.mean('value', 'baseline').name.suffix('-avg'),
            )
            # NOTE 평균 산정 방법 주의
            .with_columns(
                ratio=(1 - pl.col('value-sum') / pl.col('baseline-sum'))
                .round(2)
                .replace(0.0, None)
            )
            .sort('row', 'col2')
        )

        if param.clip:
            data = data.with_columns(pl.col('ratio').clip(upper_bound=1.0))

        if data.select(pl.col('ratio').is_null().all()).item():
            raise _NullError(param)

        pivot = (
            data
            .with_columns(pl.col('ratio') * 100)
            .pivot('col2', index='row', values='ratio', sort_columns=True)
            .rename(lambda x: re.sub(r'^(#\d+ )?(.*)$', r'\2', x))
        )

        fig = Figure()
        ax = fig.add_subplot()
        sns.heatmap(
            pivot.to_pandas().set_index('row'),
            ax=ax,
            cmap='crest_r',
            vmin=0,
            annot=True,
            fmt='.0f',
            cbar_kws={'format': '{x:.0f}%'},
        )
        ax.set_yticklabels(ax.get_yticklabels(), rotation=0)
        ax.set_ylabel('')

        return data, fig

    def __call__(self):
        utils.mpl.MplTheme(fig_size=(self.fig_width, None)).grid(show=False).apply()
        self.output.mkdir(exist_ok=True)
        bipv = '' if self.bipv_limit is None else f' BIPV le {self.bipv_limit}'

        for param in GroupReprParam.iter():
            name = f'{param.name}{bipv}'
            logger.info(name)

            try:
                data, fig = self.compare(param)
            except _NullError:
                logger.debug('Null case: {}', param)
                continue

            data.write_excel(self.output / f'{name}.xlsx')
            fig.savefig(self.output / f'{name}.png')


if __name__ == '__main__':
    utils.terminal.LogHandler.set(10)

    app()
