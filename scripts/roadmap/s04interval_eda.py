"""
2024-08-05.

- 구간별 건축물 설계특성 통계분석
    - 정책효과분석
    - 설계요소민감도분석
- 설계요소 민감도 분석 ANOVA
- 정책효과분석 대표모델 선정
"""

from __future__ import annotations

import dataclasses as dc
from functools import cached_property
from itertools import product
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, ClassVar, Literal

import more_itertools as mi
import numpy as np
import pingouin as pg
import polars as pl
import polars.selectors as cs
import rich
from loguru import logger
from scipy.spatial.distance import cdist
from xlsxwriter import Workbook

from scripts.roadmap.config import Config
from scripts.roadmap.s02certification import Dataset
from zeb import utils
from zeb.roadmap import classification as clsf
from zeb.utils.cli import App

if TYPE_CHECKING:
    from collections.abc import Collection

    from polars._typing import ColumnWidthsDefinition, FrameType


conf = Config.read()
intrv = conf.interval_eda


class FileName:
    POLICY = '01정책효과분석'
    SENSITIVITY = '02설계요소민감도분석'

    P = POLICY
    S = SENSITIVITY


H = '온열원설비'
C = '냉열원설비'


@dc.dataclass
class _Summary(utils.pl.PolarsSummary):
    variable_category: dict[str, str] = dc.field(
        default_factory=lambda: {v: c for c, v in intrv.iter_var()}
    )

    detailed_equipment: bool = True

    EQUIPMENT: ClassVar[dict[str, tuple[str, ...]]] = {
        H: (f'{H}_난방방식', f'{H}_사용연료', f'{H} 분류'),
        C: (
            f'{H}_난방방식',
            f'{H}_사용연료',
            f'{C}_냉방방식',
            f'{C}_사용연료',
            f'{C} 분류',
        ),
        '급탕설비': ('급탕설비_급탕방식', '급탕설비_사용연료'),
    }

    def _sort_columns(self, data: pl.DataFrame):
        if not self.group_prefix:
            return data
        if not (prefix := [x for x in data.columns if x.startswith(self.group_prefix)]):
            return data
        return data.select(*prefix, *(x for x in data.columns if x not in prefix))

    def describe(self, selector=None):
        return self._sort_columns(
            super()
            .describe(selector)
            .select(
                pl
                .col('variable')
                .str.extract(r'^(.*?)( \(.*\))?$')
                .replace_strict(self.variable_category, default=None)
                .alias('category'),
                pl.all(),
            )
        )

    def count_string(self):
        return self._sort_columns(
            super()
            .count_string()
            .select(
                pl
                .col('variable')
                .str.extract(r'^(.*?)( \(.*\))?$')
                .replace_strict(self.variable_category, default=None)
                .alias('category'),
                pl.all(),
            )
        )

    def write_excel(
        self,
        path: str | Path,
        column_widths: ColumnWidthsDefinition | None = 120,
        **kwargs,
    ):
        kwargs['column_widths'] = column_widths

        with Workbook(path) as wb:
            # numeric
            if (
                self.data
                .select(cs.numeric() | cs.boolean())
                .drop(self.group or [], strict=False)
                .collect_schema()
                .len()
            ):
                self.describe().write_excel(wb, worksheet='numeric', **kwargs)

            # temporal
            if (
                self.data
                .select(cs.temporal())
                .drop(self.group or [], strict=False)
                .collect_schema()
                .len()
            ):
                self.describe(selector=cs.temporal()).write_excel(
                    wb, worksheet='temporal', **kwargs
                )

            # string, categorical
            self._write_string_categorical(wb, **kwargs)

            group = self.group or ()
            assert isinstance(group, tuple)

            for equipment in self.EQUIPMENT:
                if equipment != '급탕설비':
                    g = (*group, f'{equipment} 분류')

                    (
                        utils.pl
                        .PolarsSummary(
                            self.data.select(f'{equipment}_효율', *g), group=g
                        )
                        .describe()
                        .write_excel(wb, f'{equipment}', column_widths=column_widths)
                    )

            # 냉온열원설비
            cols = tuple(
                mi.unique_everseen((
                    *self.EQUIPMENT[H],
                    *self.EQUIPMENT[C],
                    '연면적',
                    f'{H}_효율',
                    f'{C}_효율',
                ))
            )
            (
                utils.pl
                .PolarsSummary(
                    self.data.select(cols), group=[x for x in cols if '분류' in x]
                )
                .describe()
                .write_excel(wb, '냉온열원설비', column_widths=column_widths)
            )

            # 설비 분류 전 개별 변수 그룹
            if not self.detailed_equipment:
                return

            for equipment, cols in self.EQUIPMENT.items():
                g = (*group, *cols)
                (
                    utils.pl
                    .PolarsSummary(self.data.select(f'{equipment}_효율', *g), group=g)
                    .describe()
                    .write_excel(wb, f'{equipment}(전체)', column_widths=column_widths)
                )


def _cast_percent(expr: pl.Expr, dtype=pl.Float64, *, strict=False):
    expr = expr.str.strip_chars()
    return (
        pl
        .when(expr.str.ends_with('%'))
        .then(expr.str.strip_suffix('%').cast(dtype, strict=strict) / 100.0)
        .otherwise(expr.cast(dtype, strict=strict))
    )


@dc.dataclass
class Preprocess:
    data: pl.DataFrame | pl.LazyFrame

    MAX_TRANSMITTANCE: float = 50

    def __post_init__(self):
        index = '신청번호'
        unique = self.data.lazy().select(index, unique=pl.col(index).is_unique())
        if not unique.select('unique').collect().to_series().all():
            raise ValueError(unique.filter(pl.col('unique').not_()).collect())

    @staticmethod
    def equipment(data: pl.DataFrame | pl.LazyFrame):
        # 냉난방설비 분류
        equipment = pl.read_excel('data/설비분류.xlsx', sheet_id=0)

        df = data.lazy().collect()

        h = ['온열원설비_난방방식', '온열원설비_사용연료']
        c = ['냉열원설비_냉방방식', '냉열원설비_사용연료', '냉난방방식_냉방']
        for var, on in zip(['온열원설비', '냉열원설비'], [h, [*h, *c]], strict=True):
            right = (
                equipment[var]
                .with_columns(pl.col(on).fill_null(''))
                .rename({'분류': f'{var} 분류'})
            )
            if not right.drop(f'{var} 분류').is_unique().all():
                msg = f'{var} is not unique'
                raise ValueError(msg)

            df = df.with_columns(pl.col(on).fill_null('')).join(
                right, on=on, how='left'
            )

        return df

    def thermal_transmittance(self, data: pl.DataFrame | pl.LazyFrame):
        k = (
            data
            .lazy()
            .select('신청번호', '지역', *intrv.variable.transmittance)
            .unpivot(index=['신청번호', '지역'])
            .with_columns(
                pl
                .when(pl.col('value') > self.MAX_TRANSMITTANCE)
                .then(None)
                .otherwise(pl.col('value'))
                .alias('value')
            )
            .collect()
        )
        kregion = k.with_columns(
            pl.format('{} ({})', 'variable', '지역').alias('variable')
        )
        kstack = pl.concat([k, kregion]).pivot(
            'variable', index='신청번호', values='value', sort_columns=True
        )

        return (
            data
            .lazy()
            .drop(intrv.variable.transmittance)
            .collect()
            .join(kstack, on='신청번호', how='full')
        )

    @staticmethod
    def etc(data: FrameType):
        numeric = (
            data
            .select(cs.string() & (~cs.contains('방식', '연료', '모듈종류', '분류')))
            .select(cs.exclude(intrv.variable.index))
            .collect_schema()
            .names()
        )
        return (
            data
            .with_columns(
                pl
                .col('연면적')
                .truediv('건축면적')
                .replace({float('inf'): None, -float('inf'): None})  # -inf?
                .alias('연면적/건축면적'),
                pl.col('열병합_열생산능력').str.extract(r'^([\d\.]+)(\([\d\.+]\))?'),
            )
            .with_columns(_cast_percent(pl.col(numeric)))
            .with_columns(
                (  # 0 -> null
                    cs.contains('_열관류율', '효율', 'COP', '용량', '면적')  # fmt
                    & cs.numeric()
                ).replace({0: None})
            )
            .with_columns(
                # 태양광 모듈면적비
                pl
                .col('태양광_모듈면적')
                .truediv('건축면적')
                .alias('태양광_모듈면적비'),
                # 전열
                pl
                .any_horizontal(
                    cs.matches('전열교환기_열회수효율_[냉난]방').fill_null(0).ne(0)
                )
                .cast(pl.Int32)
                .alias('전열교환기_설치비율'),
                # 지열
                pl
                .any_horizontal(cs.matches('지열_[냉난]방_용량').fill_null(0).ne(0))
                .cast(pl.Int32)
                .alias('지열설비_설치비율'),
                # 태양광
                pl
                .col('태양광_모듈면적')
                .fill_null(0)
                .ne(0)
                .cast(pl.Int32)
                .alias('태양광_설치비율'),
                # 열병합
                pl
                .col('열병합_열생산능력')
                .fill_null(0)
                .ne(0)
                .cast(pl.Int32)
                .alias('열병합_설치비율'),
            )
        )

    def run(self):
        data = self.equipment(self.data)
        data = self.thermal_transmittance(data)
        data = self.etc(data)

        return data.select(c for _, c in intrv.iter_var() if c in data.columns)


app = App()
app.command(app_analyse := App('analyse', help='정책효과/ZEB 설계요소 민감도 분석'))


@app_analyse.command
def analyse_prep_policy():
    """정책효과분석 데이터 전처리."""
    dataset = Dataset(
        filter_1st_cert=True,
        filter_state=True,
        filter_policy_interval=False,
        filter_cert_duration=False,
    )

    lf = dataset.lazyframe().filter(
        pl.col('인증구분') == '본인증',
        pl.col('건물용도') == '비주거',
        pl.col('예비인증신청일') >= pl.date(2014, 1, 1),
        pl.col('정책구간').is_in([f'P{i + 1}' for i in range(5)]),
    )
    assert lf.select(pl.col('신청번호').is_unique().all()).collect().item()
    df = Preprocess(lf).run()

    rich.print(df)
    df.write_parquet(intrv.root / f'{FileName.P}.parquet')
    df.write_excel(intrv.root / f'{FileName.P}.xlsx')


@app_analyse.command
def analyse_summarise_policy():
    """정책 효과 분석."""
    data = pl.scan_parquet(intrv.root / f'{FileName.P}.parquet')

    for group in [
        None,
        '정책구간',
        '주체',
        ['정책구간', '건물주용도'],
        ['정책구간', '면적구간'],
        ['정책구간', '건물주용도', '면적구간'],
        ['정책구간', '면적구간', '주체'],
        ['정책구간', '건물주용도', '면적구간', '주체'],
    ]:
        logger.info('group={}', group)
        g = None if group is None else tuple(mi.always_iterable(group))
        suffix = '' if g is None else f' by {"&".join(g)}'

        summ = _Summary(data.drop('신청번호', '건축물명'), group=group)
        summ.write_excel(intrv.root / f'{FileName.P}-요약{suffix}.xlsx')


@app_analyse.command
def analyse_prep_sensitivity(
    source: Path | None = None,
    area_breaks: tuple[float, ...] = (3000, 10000),
):
    path = source or conf.certification.source.with_suffix('.parquet')
    if not path.exists():
        path = conf.root / '02발급목록' / path

    breaks = clsf.Breaks(period=[], area=list(area_breaks))
    lf = (
        pl
        .scan_parquet(path)
        .unique('신청번호')
        .filter(
            pl.col('인증구분') == '예비인증',
            pl.col('건물용도') == '주거용 이외',
            pl.col('인증신청일') >= pl.date(2020, 1, 1),  # XXX
        )
        .with_columns(breaks.cut(pl.col('연면적'), breaks='area').alias('면적구간'))
    )

    df = Preprocess(lf).run()
    df.write_parquet(intrv.root / f'{FileName.S}.parquet')
    df.write_excel(intrv.root / f'{FileName.S}.xlsx')


@app_analyse.command
def analyse_summarise_sensitivity():
    """ZEB 설계요소 민감도 분석."""
    data = pl.scan_parquet(intrv.root / f'{FileName.S}.parquet')

    for group in [
        None,
        '건물주용도',
        '면적구간',
        '주체',
        ['건물주용도', '면적구간'],
        ['건물주용도', '면적구간', '주체'],
    ]:
        logger.info('group={}', group)
        g = None if group is None else tuple(mi.always_iterable(group))
        suffix = '' if g is None else f' by {"&".join(g)}'

        summ = _Summary(data.drop('신청번호', '건축물명'), group=g)
        summ.write_excel(intrv.root / f'{FileName.S}-요약{suffix}.xlsx')


@app_analyse.command(name='run-all')
def analyse():
    analyse_prep_policy()
    analyse_summarise_policy()

    analyse_prep_sensitivity()
    analyse_summarise_sensitivity()


def _anova(  # noqa: PLR0913
    data: FrameType,
    *,
    dv: str,
    between: str | Collection[str],
    ss_type: Literal[1, 2, 3] = 1,
    detailed: bool = True,
    effsize: Literal['np2', 'n2'] = 'np2',
):
    between = list(mi.always_iterable(between))
    df = data.lazy().select(dv, *between).drop_nulls().collect()

    anova = pg.anova(
        df.to_pandas(),
        dv=dv,
        between=between,
        ss_type=ss_type,
        detailed=detailed,
        effsize=effsize,
    )

    return (
        pl
        .from_pandas(anova)
        .select(pl.lit(dv).alias('종속변수'), pl.all())
        .rename({'Source': '독립변수'})
    )


def _anova_equipment(
    data: pl.DataFrame,
    group: str,
    dv: str,
    between: str | Collection[str],
    ss_type: Literal[1, 2, 3] = 1,
):
    for by, df in data.group_by(group):
        try:
            anova = _anova(df, dv=dv, between=between, ss_type=ss_type)
        except (AssertionError, ValueError) as e:
            logger.warning(
                'ANOVA Error | group={} | by={} | dv={} | {}', group, by[0], dv, e
            )
            yield df.select(pl.lit(by[0]).alias(group), pl.lit(dv).alias('종속변수'))
            continue

        yield anova.select(pl.lit(by[0]).alias(group), pl.all())


@app.command
def anova(
    *,
    between: tuple[str, ...] = ('건물주용도', '정책구간', '면적구간', '주체'),
    k_between: tuple[str, ...] = ('지역', '건물주용도', '정책구간', '면적구간', '주체'),
    column_widths: int = 120,
    ss_type: Literal[1, 2, 3] = 1,
):
    """민감도 분석 대상 ANOVA."""
    src = intrv.root / f'{FileName.S}.parquet'
    data = (
        pl
        .scan_parquet(src)
        .with_columns(cs.categorical().cast(pl.String))
        .rename(lambda x: x.replace('(-)', ''))
        .collect()
    )
    anova: dict[str, pl.DataFrame] = {}

    # 열관류율
    k = (
        data
        .select(cs.contains('열관류율') & cs.numeric())
        .drop(cs.ends_with(')'))
        .columns
    )
    anova['열관류율'] = pl.concat(
        _anova(data, dv=x, between=k_between, ss_type=ss_type) for x in k
    )

    # 기타
    etc = data.select(cs.numeric()).drop(cs.contains('열관류율')).columns
    anova['기타'] = pl.concat(
        mi.map_except(
            lambda x: _anova(data, dv=x, between=between, ss_type=ss_type),
            etc,
            AssertionError,
        )
    )

    # 설비
    data = data.with_columns(
        pl.concat_str('급탕설비_급탕방식', '급탕설비_사용연료', separator='/').alias(
            '급탕설비 분류'
        )
    )
    equipment = ['온열원설비', '냉열원설비', '급탕설비']
    anova['설비효율'] = pl.concat(
        _anova(
            data,
            dv=f'{eq}_효율',
            between=[*between, f'{eq} 분류'],
            ss_type=ss_type,
        )
        for eq in equipment
    )
    anova['설비효율(그룹별)'] = pl.concat(
        mi.flatten(
            _anova_equipment(
                data.rename({f'{eq} 분류': '설비분류'}),
                group='설비분류',
                dv=f'{eq}_효율',
                between=between,
                ss_type=ss_type,
            )
            for eq in equipment
        ),
        how='diagonal',
    )

    dst = intrv.root / f'{FileName.S}-ANOVA-SSType={ss_type}.xlsx'
    with Workbook(dst) as wb:
        for key, df in anova.items():
            df.write_excel(wb, worksheet=key, column_widths=column_widths)


def _scale_dataframe(
    data: pl.DataFrame,
    method: Literal['standard', 'robust'] = 'robust',
):
    if data.select(~cs.numeric()).width:
        raise ValueError

    a = pl.all()
    if method == 'standard':
        array = data.with_columns(a.fill_null(strategy='mean')).to_numpy()
        scaled = (array - np.nanmean(array, axis=0)) / np.nanstd(array, axis=0)
    elif method == 'robust':
        array = data.with_columns(a.fill_null(a.median())).to_numpy()
        iqr = np.nanquantile(array, 0.75, axis=0) - np.nanquantile(array, 0.25, axis=0)
        iqr[iqr == 0] = 1.0
        scaled = (array - np.nanmedian(array, axis=0)) / iqr
    else:
        raise ValueError(method)

    return pl.from_numpy(scaled, schema=data.columns)


def _center_distance(
    data: pl.DataFrame,
    center: Literal['mean', 'median'] = 'median',
    metric='euclidean',
    **kwargs,
) -> np.ndarray:
    array = data.to_numpy()
    c: np.ndarray = (
        np.mean(array, axis=0) if center == 'mean' else np.median(array, axis=0)
    )

    return cdist(array, c.reshape([1, -1]), metric=metric, **kwargs)  # pyright: ignore[reportArgumentType, reportCallIssue]


def _center_distance_dataframe(
    data: pl.DataFrame,
    group: str | Collection[str],
    variables: str | Collection[str],
    center: Literal['mean', 'median'] = 'median',
    metric='euclidean',
    **kwargs,
):
    for _, df in data.group_by(group, maintain_order=True):
        dist = _center_distance(
            df.select(variables),
            center=center,
            metric=metric,
            **kwargs,
        )
        yield df.with_columns(pl.Series('distance', dist.ravel()))


@dc.dataclass
class PolicyTarget:
    path: dc.InitVar[str | Path | None] = None

    dist_group: tuple[str, ...] = (
        '정책구간',
        '면적구간',
        '지역',
        '주체',
        '온열원설비 분류',
        '냉열원설비 분류',
    )
    method: Literal['param', 'non-param'] = 'non-param'
    metric: str = 'euclidean'
    max_na: float = 0

    sample_count: int = 100  # 정책구간별 샘플링 개수
    sample_group: tuple[str, ...] = ('지역', '건물주용도')

    data: pl.DataFrame = dc.field(init=False)
    points: pl.DataFrame = dc.field(init=False)
    variables: list[str] = dc.field(init=False)
    drop: list[str] = dc.field(init=False)

    def __post_init__(self, path: str | Path | None):
        path = path or intrv.root / f'{FileName.POLICY}.parquet'

        equipment = pl.from_dicts(intrv.iter_equipment())
        data = (
            pl
            .read_parquet(path)
            .join(equipment, on=equipment.columns, how='inner')
            .with_row_index()
        )

        # 거리 계산에 사용할 변수들
        variables = [
            *(
                v
                for f, v in intrv.iter_var()
                if f in {'area', 'transmittance', 'electrical'}
                and v != '연면적/건축면적'
            ),
            '창면적비_평균',
            '온열원설비_효율',
            '냉열원설비_효율',
            '전열교환기_열회수효율_난방',
            '전열교환기_열회수효율_냉방',
        ]

        points = data.select(variables).with_columns(cs.categorical().cast(pl.String))
        drop = [s.name for s in points if s.is_null().mean() > self.max_na]  # type: ignore[operator]

        if self.max_na and drop:
            logger.info('drop {}', drop)
            variables = [x for x in variables if x not in drop]
            points = points.drop(drop)

        assert points.select(cs.numeric()).width == points.width

        self.data = data
        self.points = points
        self.variables = variables
        self.drop = drop

    def name(self):
        na = f'NA {self.max_na:0.0%}이하, ' if self.max_na else ''
        method = '평균' if self.method == 'param' else '중위수'
        g = '&'.join(self.sample_group)

        return f'{na}{method}, n={self.sample_count}, {g} 그룹'

    def head(self, *, sample=False):
        if sample:
            h = (
                'index',
                'group_index',
                'sample_index',
                *self.dist_group,
                *self.sample_group,
                'distance',
                'dist_rank',
                '샘플개수',
            )
        else:
            h = (
                'index',
                'group_index',
                *self.dist_group,
                'distance',
            )

        return tuple(mi.unique_everseen(h))

    def columns(self, *, sample=False):
        head = self.head(sample=sample)
        return (pl.col(head), cs.exclude(head))

    @cached_property
    def normalized_points(self):
        group_index = (
            self.data
            .select(self.dist_group)
            .unique()
            .sort(pl.all())
            .with_row_index('group_index')
        )
        groups = mi.unique_everseen([*self.dist_group, *self.sample_group])
        left = self.data.select('index', *groups).join(
            group_index, on=self.dist_group, how='left'
        )
        right = _scale_dataframe(
            self.points,
            method='standard' if self.method == 'param' else 'robust',
        )
        return pl.concat([left, right], how='horizontal')

    @cached_property
    def dist_norm(self):
        return (
            pl
            .concat(
                _center_distance_dataframe(
                    data=self.normalized_points,
                    group=self.dist_group,
                    variables=self.variables,
                    center='mean' if self.method == 'param' else 'median',
                    metric=self.metric,
                )
            )
            .select(self.columns())
            .sort('group_index', 'distance')
        )

    @cached_property
    def dist(self):
        return (
            self.data
            .join(
                self.dist_norm.select('index', 'group_index', 'distance'),
                on='index',
                how='full',
            )
            .drop('index_right')
            .sort('group_index', 'distance')
            .select(self.columns())
        )

    def group_sample_count(self):
        if self.sample_count <= 0:
            msg = f'{self.sample_count=}'
            raise ValueError(msg)

        index = ['정책구간', *self.sample_group]
        sample_index = (
            self.data
            .select(index)
            .unique()
            .sort(pl.all())
            .with_row_index('sample_index')
        )
        count = (
            self.data
            .join(sample_index, on=index, how='left')
            .group_by('sample_index', *index)
            .len('원본데이터개수')
            .sort('sample_index')
            .with_columns(
                pl
                .col('원본데이터개수')
                .truediv(pl.sum('원본데이터개수').over('정책구간'))
                .alias('샘플비율')
            )
            .with_columns(
                pl
                .col('샘플비율')
                .mul(self.sample_count)
                .round()
                .cast(pl.UInt32)
                .alias('샘플개수')
            )
        )

        return index, count

    def sample(self):
        index, count = self.group_sample_count()

        sample = (
            self.dist
            .join(
                count.select('sample_index', '샘플개수', *index),
                on=index,
                how='left',
            )
            .with_columns(dist_rank=pl.col('distance').rank().over('sample_index'))
            .sort('sample_index', 'dist_rank')
            .select(self.columns(sample=True))
        )

        return index, count, sample

    def write_excel(self, path: str | Path):
        fmt = {
            'distance': {
                'type': '2_color_scale',
                'min_color': '#FFFFFF',
                'max_color': '#FF0000',
            }
        }

        with Workbook(path) as wb:
            gray = wb.add_format({'bg_color': '#E0E0E0'})
            fmt_dist: dict = fmt | {
                self.dist_group: {
                    'type': 'formula',
                    'criteria': '=ISODD($B2)',
                    'format': gray,
                }
            }

            def write(df: pl.DataFrame, worksheet: str, *, cond: dict | None = None):
                df.write_excel(
                    wb,
                    worksheet=worksheet,
                    conditional_formats=cond,
                    column_widths=min(120, max(60, int(1600 / df.width))),
                )

            write(self.dist, '원본 데이터', cond=fmt_dist)
            write(self.dist_norm, '정규화 데이터', cond=fmt_dist)

            _, count, sample = self.sample()
            write(count, worksheet='샘플배분')

            group = tuple(mi.unique_everseen((*self.dist_group, *self.sample_group)))
            fmt_sample: dict = fmt | {
                group: {
                    'type': 'formula',
                    'criteria': '=ISODD($D2)',
                    'format': gray,
                }
            }

            sample = sample.filter(pl.col('dist_rank') <= pl.col('샘플개수'))
            sample = sample.insert_column(
                3,
                sample
                .select('sample_index')
                .to_series()
                .rank('dense')
                .rename('_sample_rank'),
            )
            write(sample, worksheet='샘플', cond=fmt_sample)


@app.command
def policy_target(  # noqa: PLR0913
    *,
    dist_group: tuple[str, ...] = (
        '정책구간',
        '면적구간',
        '지역',
        '주체',
        '온열원설비 분류',
        '냉열원설비 분류',
    ),
    sample_count: int = 100,
    sample_group: tuple[str, ...] = ('지역', '건물주용도'),
    method: Literal['param', 'non-param'] = 'non-param',
    metric='euclidean',
    max_na: float = 0,
):
    """정책효과 대표모델 - 그룹별 데이터 중앙과 가까운 대표모델 후보군 선정."""
    target = PolicyTarget(
        path=None,
        dist_group=dist_group,
        method=method,
        metric=metric,
        max_na=max_na,
        sample_count=sample_count,
        sample_group=sample_group,
    )

    name = f'03정책효과대표모델 ({target.name()}).xlsx'
    target.write_excel(intrv.root / name)


@app.command
def policy_target_batch():
    include = ['지역', '건물주용도']
    group_vars = ['면적구간', '냉열원설비 분류', '온열원설비 분류']

    for count, group in product([100, 150], mi.powerset(group_vars)):
        logger.info(f'{count=} | {group=}')
        policy_target(sample_count=count, sample_group=(*include, *group))


@dc.dataclass
class PolicyTarget2(PolicyTarget):
    profile_ratio: dc.InitVar[bool] = False

    dist_group: tuple[str, ...] = (
        '정책구간',
        '면적구간',
        '주체',
        '등급',
        '건물주용도',
    )

    USE: ClassVar[dict[str, str]] = {
        '공장': '기타',
        '관광 휴게시설': '교육&사회',
        '교육연구시설': '교육&사회',
        '교정 및 군사 시설': '기타',
        '교정시설': '기타',
        '국방·군사시설': '기타',
        '노유자시설': '교육&사회',
        '동물 및 식물 관련 시설': '기타',
        '묘지 관련 시설': '교육&사회',
        '문화 및 집회시설': '교육&사회',
        '발전시설': '기타',
        '방송통신시설': '기타',
        '분뇨 및 쓰레기 처리 시설': '기타',
        '수련시설': '교육&사회',
        '숙박시설': '상업용',
        '업무시설': '상업용',
        '운동시설': '교육&사회',
        '운수시설': '상업용',
        '위험물 저장 및 처리 시설': '상업용',
        '의료시설': '교육&사회',
        '자동차 관련 시설': '상업용',
        '자원순환 관련 시설': '기타',
        '장례시설': '교육&사회',
        '제1종 근린생활시설': '상업용',
        '제2종 근린생활시설': '상업용',
        '종교시설': '교육&사회',
        '창고시설': '기타',
        '판매시설': '상업용',
    }
    POLICY: ClassVar[dict[str, str]] = {
        'P1': 'A',
        'P2': 'A',
        'P3': 'B',
        'P4': 'B',
        'P5': 'B',
    }

    def __post_init__(self, path: str | Path | None, profile_ratio: bool):
        path = path or intrv.root / f'{FileName.POLICY}.parquet'

        data = (
            pl
            .read_parquet(path)
            .with_columns(
                pl.col('건물주용도').replace_strict(self.USE),
                pl.col('정책구간').replace_strict(self.POLICY),
            )
            .with_row_index()
        )

        # 거리 계산에 사용할 변수들
        variables = ['연면적', '건축면적', '연면적/건축면적', '창면적비_평균']
        if profile_ratio:
            variables.extend(intrv.variable.profile)

        points = data.select(variables).with_columns(cs.categorical().cast(pl.String))
        drop = [s.name for s in points if s.is_null().mean() > self.max_na]  # type: ignore[operator]

        if self.max_na and drop:
            logger.info('drop {}', drop)
            variables = [x for x in variables if x not in drop]
            points = points.drop(drop)

        assert points.select(cs.numeric()).width == points.width

        self.data = data
        self.points = points
        self.variables = variables
        self.drop = drop


@app.command
def policy_target2(  # noqa: PLR0913
    *,
    dist_group: tuple[str, ...] = (
        '정책구간',
        '면적구간',
        '주체',
        '등급',
        '건물주용도',
    ),
    profile_ratio: bool = False,
    sample_count: int = 100,
    sample_group: tuple[str, ...] = ('지역', '건물주용도'),
    method: Literal['param', 'non-param'] = 'non-param',
    metric='euclidean',
    max_na: float = 0,
):
    """
    2025-04-16 제안서용 대표모델 재선정.

    - 정책 구간 2개로 단순화
    - 용도 3개로 단순화
    - 그룹 효율등급 추가
    - 거리 계산 변수 열관류율, 설비 정보 제외
      (연면적, 건축면적, 연면적/건축면적, 창면적비만 고려)
    """
    target = PolicyTarget2(
        path=None,
        profile_ratio=profile_ratio,
        dist_group=dist_group,
        method=method,
        metric=metric,
        max_na=max_na,
        sample_count=sample_count,
        sample_group=sample_group,
    )

    name = f'03정책효과대표모델 2025 ({target.name()}).xlsx'
    target.write_excel(intrv.root / name)


if __name__ == '__main__':
    from zeb.utils.terminal import LogHandler

    LogHandler.set()
    app()

    # TODO 바닥_열관류율 이상치 처리?
