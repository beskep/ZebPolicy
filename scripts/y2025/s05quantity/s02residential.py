import dataclasses as dc
import functools
from typing import TYPE_CHECKING, Literal

import more_itertools as mi
import polars as pl
import rich
import seaborn as sns
import xlsxwriter
from loguru import logger
from matplotlib.figure import Figure

from scripts.y2025.s05quantity.common import Dirs, app
from zeb import utils

if TYPE_CHECKING:
    from collections.abc import Sequence

    from matplotlib.axes import Axes


def _remove_m2(text: str):
    return text.removesuffix('(㎡)')


@dc.dataclass
class _Dirs(Dirs):
    def source(self, name: str):
        return mi.one(self.data.glob(f'*{name}*.parquet'))


class Var:
    class PK:
        BLOCK = '관리_동별_개요_PK'
        RESIDENTIAL = '관리_주택대장_PK'
        PERMISSION = '관리_허가대장_PK'

    class AREA:
        BUILDING = '건축_면적'
        FLOOR = '연면적'
        UNDERGROUN = '지하_면적'
        ABOVEGROUND = '용적_률_산정_연면적'

    class HOUSEHOLD:
        TOTAL = '총_세대_수(세대)'

        PUBLIC_TYPES = (
            '세대_수_국민_임대(세대)',
            '세대_수_공공_임대_계(세대)',
            '세대_수_공공_분양(세대)',
        )
        PRIVATE_TYPES = ('세대_수_민간_임대(세대)', '세대_수_민간_분양(세대)')
        OTHER_TYPES = ('세대_수_근로_복지(세대)', '세대_수_사원_임대(세대)')

        PUBLIC = '세대수_공공'
        PRIVATE = '세대수_민간'
        OTHERS = '세대수_기타'

    USE = '용도_코드_명'
    MAIN_USE = '주_용도_코드_명'
    MAIN_BUILDING_CODE = '주_부속_구분_코드_명'

    PERMISSION_DATE = '승인_일'


@app.command
def variables(dirs: Dirs):
    """건축허브 대용량 데이터 간 중복 변수, 매칭 가능한 PK 변수 확인."""

    def it():
        for path in dirs.data.glob('*.parquet'):
            variables = pl.scan_parquet(path).collect_schema().names()
            yield pl.DataFrame({'variable': variables, 'source': path.stem})

    df = pl.concat(it()).with_columns(
        pl.col('source').str.extract(r'((건축물대장|건축인허가|주택인허가)_.*)_'),
        pl.lit(1).alias('exists'),
    )

    rich.print(df)

    with xlsxwriter.Workbook(dirs.analysis / '01.01.variables.xlsx') as wb:
        (
            (df)
            .pivot('source', index='variable', values='exists', sort_columns=True)
            .write_excel(wb, worksheet='variables')
        )
        (
            df
            .filter(pl.col('variable').str.contains('PK'))
            .pivot('variable', index='source', values='exists', sort_columns=True)
            .write_excel(wb, worksheet='pk', column_widths=120)
        )


@app.command
@dc.dataclass
class JoinPK:
    """기본·동별 개요 데이터 PK join 테스트."""

    dirs: Dirs
    sources: tuple[str, ...] = (
        '건축인허가_기본개요',
        '건축인허가_동별개요',
        '주택인허가_동별개요',
        '주택인허가_기본개요',
    )
    pk: tuple[str, ...] = (Var.PK.BLOCK, Var.PK.RESIDENTIAL, Var.PK.PERMISSION)

    def read(self, source: str):
        path = mi.one(self.dirs.data.glob(f'*{source}*.parquet'))
        return pl.scan_parquet(path)

    def pk_index(self, source: str):
        lf = self.read(source)
        variables = set(lf.collect_schema().names()) & set(self.pk)

        if '용도_코드' in variables:
            lf = (
                (lf)
                .with_columns(pl.col('용도_코드').replace({'': None}))
                .drop_nulls('용도_코드')
            )

        return lf.select(variables).with_row_index(name=source).collect()

    def __call__(self):
        data = self.pk_index(self.sources[0])

        for source in self.sources[1:]:
            df = self.pk_index(source)
            if not (on := set(data.columns) & set(df.columns)):
                msg = f'`{source}`에 공통 column이 발견되지 않음'
                raise ValueError(msg)

            data = data.join(df, on=list(on), how='full', coalesce=True)

        data = data.select(*self.pk, *self.sources)

        length = (
            data
            .with_columns(
                pl
                .col(self.sources)
                .is_not_null()
                .replace_strict({False: None, True: '✓'}, return_dtype=pl.String)
            )
            .group_by(self.sources)
            .len()
            .sort(pl.all())
        )
        length.write_excel(
            self.dirs.analysis / '01.02.matched-pk-length.xlsx',
            column_widths=150,
        )


@dc.dataclass
class _Base:
    dirs: _Dirs

    min_year: int | None = None
    max_year: int = 2024

    zero: Literal['drop', 'fill', 'none'] = 'drop'
    """
    세대수 기준 필터링.

    세대수가 0인 경우 신축이 아닌 허가 건으로 추정.
    """

    figsize: tuple[float | None, float | None] = (24, None)
    prefix: str = ''

    @functools.cached_property
    def _output(self):
        return self.dirs.analysis

    def output(self, name: str, suffix: str | None = None):
        return self._output / f'{self.prefix}{name}{suffix or ""}'

    def scan_base(self, *, zero: Literal['drop', 'fill', 'none']):
        lf = (
            pl
            .scan_parquet(self.dirs.source('주택인허가_기본개요'))
            .drop_nulls('시군구_코드')
            .rename(_remove_m2)
            .with_columns(
                pl.col(Var.PERMISSION_DATE).str.to_date('%Y%m%d', strict=False),
                pl.col(Var.HOUSEHOLD.TOTAL).cast(pl.UInt32),
            )
            .with_columns(pl.col(Var.PERMISSION_DATE).dt.year().alias('year'))
            .filter(pl.col('year') <= self.max_year)
        )

        match zero:
            case 'drop':
                lf = lf.filter(pl.col(Var.HOUSEHOLD.TOTAL) != 0)
            case 'fill':
                lf = lf.with_columns(pl.col(Var.HOUSEHOLD.TOTAL).replace({0: 1}))
            case None:
                pass

        return lf

    def scan_block(self):
        return (
            pl
            .scan_parquet(self.dirs.source('주택인허가_동별개요'))
            .rename(_remove_m2)
            .with_columns(
                pl
                .sum_horizontal(*Var.HOUSEHOLD.PUBLIC_TYPES)
                .cast(pl.UInt32)
                .alias(Var.HOUSEHOLD.PUBLIC),
                pl
                .sum_horizontal(*Var.HOUSEHOLD.PRIVATE_TYPES)
                .cast(pl.UInt32)
                .alias(Var.HOUSEHOLD.PRIVATE),
            )
        )


@app.command
@dc.dataclass
class BaseEda(_Base):
    """주택인허가-기본개요 EDA."""

    prefix: str = '02.01.주택인허가-기본개요.'

    def output(self, name: str, suffix: str | None = None):
        return (
            self._output / f'{self.prefix}{name} '
            f'y0={self.min_year} zero={self.zero}'
            f'{suffix or ""}'
        )

    @functools.cached_property
    def raw(self):
        lf = (
            self
            .scan_base(zero=self.zero)
            .filter(
                pl.col(Var.USE).is_in(['공동주택', '단독주택']),
            )
            .with_columns(
                pl
                .col(Var.PERMISSION_DATE)
                .dt.year()
                .cut([1990, 2010, 2020])
                .cast(pl.String)
                .fill_null('Unknown')
                .alias('permission-period'),
            )
        )

        if self.min_year:
            lf = lf.filter(pl.col('year') >= self.min_year)

        return lf.collect()

    def count(self):
        """허가 건수."""
        count = self.raw.group_by('year', Var.USE).len().sort(pl.all())
        path = self.output('인허가건수', '.xlsx')

        (
            (count)
            .pivot(Var.USE, index='year', values='len', sort_columns=True)
            .write_excel(path)
        )

        fig = Figure()
        ax = fig.subplots()
        sns.lineplot(count, x='year', y='len', hue=Var.USE, ax=ax)
        ax.set_xlabel('')
        ax.set_ylabel('주택 인허가 건수')
        ax.legend(title='')
        fig.savefig(path.with_suffix('.png'))

    def household(self):
        """세대수."""
        # 허가별 세대수 분포
        fig = Figure()
        axes: Sequence[Axes] = fig.subplots(1, 2)
        for ax, use in zip(axes, ['공동주택', '단독주택'], strict=True):
            sns.histplot(
                self.raw.filter(pl.col(Var.USE) == use).sort('permission-period'),
                x=Var.HOUSEHOLD.TOTAL,
                hue='permission-period',
                ax=ax,
                log_scale=True,
            )
            ax.set_xlabel('인허가 건별 세대수')
            ax.set_title(use, loc='left', weight=500)
            if legend := ax.get_legend():
                legend.set_title('승인 기간')

        fig.savefig(self.output('세대수-허가별', '.png'))

        # 연도별 세대수
        by_year = (
            (self.raw)
            .drop_nulls(Var.HOUSEHOLD.TOTAL)
            .group_by(['year', Var.USE])
            .agg(pl.sum(Var.HOUSEHOLD.TOTAL))
            .sort(pl.all())
        )
        fig = Figure()
        ax = fig.subplots()
        sns.lineplot(by_year, x='year', y=Var.HOUSEHOLD.TOTAL, hue=Var.USE, ax=ax)
        ax.set_xlabel('')
        ax.set_ylabel('연도별 허가 세대 수')
        fig.savefig(self.output('세대수-연도별', '.png'))

    def floor_area(self):
        data = (
            (self.raw)
            .with_columns(year=pl.col(Var.PERMISSION_DATE).dt.year())
            .rename({Var.AREA.FLOOR: 'value'})
            .group_by('year', Var.USE)
            .agg(pl.sum('value'))
            .sort(pl.all())
        )

        (
            (data)
            .pivot(Var.USE, index='year', values='value', sort_columns=True)
            .write_excel(self.output('연면적', '.xlsx'))
        )

        fig = Figure()
        ax = fig.subplots()
        sns.lineplot(data, x='year', y='value', hue=Var.USE, ax=ax)
        ax.set_xlabel('')
        ax.set_ylabel('연면적 [m²]')
        fig.savefig(self.output('연면적', '.png'))

    def __call__(self):
        utils.mpl.MplTheme(fig_size=self.figsize).grid().apply()

        logger.info('PK unique: {}', self.raw[Var.PK.RESIDENTIAL].is_unique().all())

        self.count()
        self.household()
        self.floor_area()


@app.command
@dc.dataclass
class BlockEda(_Base):
    """주택인허가-동별개요 EDA."""

    annex: bool = False
    """부속 건물 포함 여부"""

    prefix: str = '02.02.주택인허가-동별개요.'

    def output(self, name: str, suffix: str | None = None):
        return (
            self._output / f'{self.prefix}{name} '
            f'y0={self.min_year} '
            f'zero={self.zero} annex={self.annex}'
            f'{suffix or ""}'
        )

    @functools.cached_property
    def raw(self):
        block = self.scan_block()
        if not self.annex:
            block = block.filter(pl.col('주_부속_구분_코드_명') == '주건축물')

        base = self.scan_base(zero=self.zero).select(
            'year', Var.PK.RESIDENTIAL, Var.USE, Var.HOUSEHOLD.TOTAL
        )
        lf = (
            (block)
            .join(base, on=Var.PK.RESIDENTIAL, how='left', validate='m:1')
            .filter(pl.col(Var.USE).is_in(['공동주택', '단독주택']))
        )

        if self.zero != 'none':
            lf = lf.drop_nulls(Var.HOUSEHOLD.TOTAL)

        if self.min_year:
            lf = lf.filter(pl.col('year') >= self.min_year)

        return lf.collect()

    def area(self):
        data = (
            self.raw
            .drop_nulls('year')
            .unpivot([Var.AREA.FLOOR, Var.AREA.ABOVEGROUND], index=['year', Var.USE])
            .with_columns(
                pl.col('variable').replace(Var.AREA.ABOVEGROUND, '용적률 산정 연면적')
            )
            .group_by('year', Var.USE, 'variable')
            .agg(pl.sum('value'))
            .sort(pl.all())
        )

        data.write_excel(self.output('면적', '.xlsx'))

        fig = Figure()
        axes = fig.subplots(1, 2)

        ax: Axes
        for use, ax in zip(['공동주택', '단독주택'], axes, strict=True):
            sns.lineplot(
                data.filter(pl.col(Var.USE) == use),
                x='year',
                y='value',
                hue='variable',
                ax=ax,
                alpha=0.8,
            )
            ax.set_xlabel('')
            ax.set_ylabel('연도별 면적 합산 [m²]')
            ax.set_title(use, loc='left', weight=500)

            if legend := ax.get_legend():
                legend.set_title('')

        fig.savefig(self.output('면적', '.png'))

    def __call__(self):
        utils.mpl.MplTheme(fig_size=self.figsize).grid().apply()
        return self.area()


@app.command
@dc.dataclass
class Prep(_Base):
    """데이터 전처리."""

    annex: bool = False
    """부속 건물 포함 여부"""

    scale_threshold: tuple[int, ...] = (300, 500, 1000)

    @functools.cached_property
    def _output(self):
        return self.dirs.processed

    def public_vs_private(self, data: pl.DataFrame):
        data = data.drop_nulls([
            Var.HOUSEHOLD.PUBLIC,
            Var.HOUSEHOLD.PRIVATE,
        ])

        fig = Figure()
        ax = fig.subplots()
        sns.scatterplot(
            data,
            x=Var.HOUSEHOLD.PUBLIC,
            y=Var.HOUSEHOLD.PRIVATE,
            ax=ax,
            alpha=0.8,
        )
        ax.set_xlabel('공공 세대수')
        ax.set_ylabel('민간 세대수')
        fig.savefig(self.output('공공vs민간 세대수.png'))

        fig = Figure()
        ax = fig.subplots()
        sns.scatterplot(
            data.with_columns(
                sum=pl.sum_horizontal(Var.HOUSEHOLD.PUBLIC, Var.HOUSEHOLD.PRIVATE)
            ),
            x='sum',
            y=Var.HOUSEHOLD.TOTAL,
            ax=ax,
            alpha=0.8,
        )
        ax.set_xlabel('공공+민간 세대수')
        ax.set_ylabel('총 세대수')
        utils.mpl.equal_scale(ax)
        fig.savefig(self.output('공공+민간 vs 총 세대수.png'))

    def check_annex(self, block: pl.LazyFrame):
        """부속건축물, null 세대수 체크."""
        annex = (
            (block)
            .filter(
                pl.col(Var.MAIN_BUILDING_CODE) != '주건축물',
                pl.any_horizontal(
                    pl.col(Var.HOUSEHOLD.PUBLIC, Var.HOUSEHOLD.PRIVATE) != 0
                ),
            )
            .collect()
        )
        if annex.height:
            logger.warning('99.부속건물 가구 수 존재 (shape={})', annex.shape)

        annex.write_excel(self.output('주택인허가-부속건축물-세대수존재.xlsx'))

    def __call__(self):
        basic = self.scan_base(zero=self.zero)
        block = self.scan_block()

        self.check_annex(block)

        # 부속건물 여부, (동별개요) 용도 코드로 세대수 존재 여부 구분 불가능
        # (`동명`은 102(동)이지만 `부속건축물`로 표기된 데이터,
        # `경비실`이지만 4세대로 표시된 데이터 등 존재)
        # => `동별개요`에서 PK 번호 기준으로 세대 수, 면적 합산 후 분석,
        #    나머지 변수는 `기본개요` 파일 활용
        area = [Var.AREA.BUILDING, Var.AREA.FLOOR, Var.AREA.ABOVEGROUND]
        public = pl.col(Var.HOUSEHOLD.PUBLIC)
        private = pl.col(Var.HOUSEHOLD.PRIVATE)

        block_agg = (
            block
            .unpivot(
                [
                    *area,
                    *Var.HOUSEHOLD.PUBLIC_TYPES,
                    *Var.HOUSEHOLD.PRIVATE_TYPES,
                    *Var.HOUSEHOLD.OTHER_TYPES,
                ],
                index=Var.PK.RESIDENTIAL,
            )
            .with_columns(
                pl.col('variable').replace({
                    **{x: f'{x}(동별합계)' for x in area},
                    **dict.fromkeys(Var.HOUSEHOLD.PUBLIC_TYPES, Var.HOUSEHOLD.PUBLIC),
                    **dict.fromkeys(Var.HOUSEHOLD.PRIVATE_TYPES, Var.HOUSEHOLD.PRIVATE),
                    **dict.fromkeys(Var.HOUSEHOLD.OTHER_TYPES, Var.HOUSEHOLD.OTHERS),
                })
            )
            .drop_nulls('value')
            .group_by([Var.PK.RESIDENTIAL, 'variable'])
            .agg(pl.sum('value'))
            .collect()
            .pivot('variable', index=Var.PK.RESIDENTIAL, sort_columns=True)
            .with_columns(
                pl
                .when(public == private)
                .then(None)
                .when(public > private)
                .then(pl.lit('public'))
                .otherwise(pl.lit('private'))
                .alias('유형(추정)')
            )
        )

        data = (
            (basic)
            .join(block_agg.lazy(), on=Var.PK.RESIDENTIAL, how='left', validate='1:1')
            .with_columns(
                pl
                .col(Var.HOUSEHOLD.TOTAL)
                .cut(
                    self.scale_threshold,
                    labels=[f'C{x + 1}' for x in range(len(self.scale_threshold) + 1)],
                )
                .alias('규모')
            )
            .collect()
        )

        data.write_parquet(
            self._output / '00.전체데이터(공동&단독주택 외 포함).parquet'
        )

        data = data.filter(pl.col(Var.USE).is_in(['공동주택', '단독주택']))
        data.write_parquet(self._output / '01.data.parquet')
        data.write_excel(self._output / '01.data.xlsx')

        (
            (self._output)
            .joinpath('02.glimpse.txt')
            .write_text(data.glimpse(return_type='string'))
        )
        utils.pl.PolarsSummary(data).write_excel(self._output / '03.summary.xlsx')

        return data


if __name__ == '__main__':
    utils.mpl.MplTheme().grid().apply()
    app()
