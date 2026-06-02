"""2024-07-23.

효율등급 인증 데이터 분석
(2014-2024)202407221515_발급목록(총괄).xlsx
"""

from __future__ import annotations

from collections.abc import Iterable
from itertools import product
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import cmasher as cmr
import matplotlib.pyplot as plt
import polars as pl
import polars.selectors as cs
import rich
import seaborn as sns
from cmap import Colormap
from msgspec import Struct
from xlsxwriter import Workbook

from zeb import utils
from zeb.roadmap import classification as clsf
from zeb.roadmap import config
from zeb.utils.cli import App

if TYPE_CHECKING:
    from matplotlib.axes import Axes

StrPath = str | Path
Paths = StrPath | Iterable[StrPath]


class Dirs(Struct):
    region: Path
    prep: Path
    plot: Path


class Cnst(Struct, frozen=True):
    YEAR2DAYS: int = 365

    USE: str = '건물용도'
    MAIN_USE: str = '건물주용도'
    CERT_DURATION: str = '예비인증소요일'

    POLICY_INTERVAL: str = '정책구간'
    AREA_INTERVAL: str = '면적구간'

    PERIOD: str = POLICY_INTERVAL
    SCALE: str = AREA_INTERVAL

    REGIONS: tuple[str, str, str, str] = ('중부1', '중부2', '남부', '제주')


class Config(config.Config, Struct):
    root: Path
    source: Path
    dirs: Dirs
    cnst: Cnst = Cnst()

    @classmethod
    def _read(
        cls,
        path: Paths = ('data/config.toml', '.config.toml'),
    ) -> dict:
        d = super()._read(path)
        return {
            k: (Path(d['root']) / v if k == 'root' else v)
            for k, v in d['certification'].items()
        }

    def __post_init__(self):
        if not self.source.exists():
            self.source = self.root / self.source

        for f in self.dirs.__struct_fields__:
            v: Path = self.root / getattr(self.dirs, f)
            setattr(self.dirs, f, v)
            v.mkdir(exist_ok=True)


class Dataset(Struct):
    data: pl.DataFrame | pl.LazyFrame | str | Path | None = None

    # ('주거용', '주거용 이외') 또는 ('주거', '비주거')
    usage: Literal['formal', 'concise'] = 'concise'

    filter_1st_cert: bool = True  # 예비인증 유무
    filter_state: bool = True  # 인증상태
    filter_policy_interval: bool = True  # 정책기간 P1~P5
    filter_cert_duration: bool = False  # 예비인증소요일 1년 이하

    @classmethod
    def read(cls, path: str | Path | None = None, conf: Config | None = None):
        conf = conf or Config.read()
        path = path or conf.dirs.prep / f'{conf.source.stem}.parquet'
        return pl.scan_parquet(path)

    def lazyframe(self):
        if self.data is None or isinstance(self.data, str | Path):
            data = self.read(self.data)
        else:
            data = self.data.lazy()

        return self.prep(data).lazy()

    def dataframe(self):
        return self.lazyframe().collect()

    def prep(self, df: pl.DataFrame | pl.LazyFrame):
        conf = Config.read()

        if self.usage == 'concise':
            df = df.with_columns(
                pl.col('건물용도').replace({'주거용': '주거', '주거용 이외': '비주거'})
            )

        if self.filter_1st_cert:
            df = df.filter(pl.col('예비인증번호').is_not_null())

        if self.filter_state:
            df = df.filter(pl.col('진행상태') != '인증취소')

        if self.filter_policy_interval:
            intervals = ['P1', 'P2', 'P3', 'P4', 'P5']
            df = df.filter(pl.col(conf.cnst.POLICY_INTERVAL).is_in(intervals))

        if self.filter_cert_duration:
            df = df.filter(pl.col('예비인증소요일') <= conf.cnst.YEAR2DAYS)

        return df


def _cast_percent(expr: pl.Expr, dtype=pl.Float64, *, strict=False):
    expr = expr.str.strip_chars()
    return (
        pl
        .when(expr.str.ends_with('%'))
        .then(expr.str.strip_suffix('%').cast(dtype, strict=strict) / 100.0)
        .otherwise(expr.cast(dtype, strict=strict))
    )


app = App()
app.command(app_prep := App('prep'))


@app_prep.command
def prep_to_polars():
    conf = Config.read()
    if (pq := conf.source.with_suffix('.parquet')).exists():
        df = pl.read_parquet(pq)
    else:
        df = pl.read_excel(conf.source, infer_schema_length=None).with_columns(
            pl
            .col('인증신청일', '접수일', '인증서 발행일', '예비인증일')
            .str.strip_chars()
            .str.to_date(strict=False),
            pl.col('적용고시일').str.to_date('%Y%m%d'),
            pl.col('인증년도').cast(pl.UInt16),
            pl.col('인증서번호').replace('', None),
            # numeric
            (cs.matches('면적') & cs.string())
            .str.strip_chars()
            .str.replace(r'(\d+)\.(\d*\.\d*)', f'${1}${2}')  # XXX
            .cast(pl.Float64, strict=False),
            _cast_percent(
                cs.contains(
                    '에너지요구량',
                    '에너지소요량',
                    '에너지절감량',
                    '열관류율',
                    '_비중',
                    '_용량',
                    '절감량',
                )
                & cs.string()
            ),
        )

        region = clsf.Region()
        breaks = clsf.Breaks.read()
        df = region.guess(df, address='건물소재지', gov='신청지역').with_columns(
            breaks
            .cut(pl.col('인증신청일'), breaks='period')
            .cast(pl.String)
            .alias(conf.cnst.POLICY_INTERVAL),
            breaks
            .cut(pl.col('연면적'), breaks='area')
            .cast(pl.String)
            .alias(conf.cnst.AREA_INTERVAL),
        )

        df.write_parquet(pq)

    utils.pl.PolarsSummary(df).write_excel(pq.parent / f'{pq.stem}_desc.xlsx')

    df.sample(1000).write_excel(pq.parent / f'{pq.stem}_sample.xlsx')
    rich.print(df.glimpse(max_items_per_column=5, return_as_string=True))


@app_prep.command
def prep_try_classify_region():
    conf = Config.read()
    d = conf.dirs.region
    d.mkdir(exist_ok=True)

    df = pl.scan_parquet(conf.source.with_suffix('.parquet'))

    region = (
        clsf
        .Region()
        .guess(
            df.select('신청번호', '건물소재지', '신청지역'),
            address='건물소재지',
            gov='신청지역',
        )
        .sort('신청지역', '건물소재지')
        .collect()
    )

    rich.print(region)

    region.write_excel(d / '발급목록_지역목록.xlsx')


@app_prep.command
def prep(*, xlsx: bool = False, filter_cert_duration: bool = False):
    conf = Config.read()
    cnst = conf.cnst
    lf = pl.scan_parquet(conf.source.with_suffix('.parquet'))

    # 예비인증
    cert1 = lf.filter(pl.col('인증구분') == '예비인증').select(
        pl.col('인증서번호').alias('예비인증번호'),
        pl.col('인증신청일').alias('예비인증신청일'),
        pl.col('인증서 발행일').alias('예비인증서발행일'),
    )

    # 본인증 데이터에 예비인증 날짜 join
    cert2 = (
        lf
        .filter(pl.col('인증구분') == '본인증')
        .rename({'인증서번호': '본인증번호', '인증신청일': '본인증신청일'})
        .join(cert1, on='예비인증번호', how='left')
    )

    breaks = clsf.Breaks.read()
    cert = cert2.with_columns(
        breaks
        .cut(pl.col('예비인증신청일'), breaks='period')
        .cast(pl.String)
        .alias(cnst.POLICY_INTERVAL),
        (pl.col('예비인증서발행일') - pl.col('예비인증신청일'))
        .dt.total_days()
        .alias(cnst.CERT_DURATION),
    ).collect()

    rich.print(cert)

    root = conf.dirs.prep
    stem = conf.source.stem
    root.mkdir(exist_ok=True)

    cert.write_parquet(root / f'{stem}.parquet')
    if xlsx:
        cert.write_excel(root / f'{stem}.xlsx')

    cert.sample(100).write_excel(root / f'{stem}_전처리 샘플.xlsx')
    utils.pl.PolarsSummary(
        Dataset(cert, filter_cert_duration=filter_cert_duration)
        .lazyframe()
        .drop(cs.starts_with('_'))
    ).write_excel(root / f'{stem}_전처리 요약.xlsx')


@app.command
def join_srr():
    """2024-07-29 ZEB등급, 자립률 join."""
    # XXX
    conf = Config.read()
    root = conf.dirs.prep
    src = root / f'{conf.source.stem}.parquet'

    df1 = pl.read_parquet(src)
    df2 = (
        pl
        .read_excel(
            conf.root / '240729_202407291658_발급목록(총괄).xlsx',
            columns=['효율등급 인증번호', '등급{ZEB}', '신재생에너지-자립률'],
        )
        .with_columns(
            pl.col('등급{ZEB}').str.strip_chars().cast(pl.Int8),
            pl.col('신재생에너지-자립률').str.strip_suffix('%'),
            # .cast(pl.Float32, strict=False),  # XXX
        )
        .with_columns()
    )

    df = df1.with_columns(pl.col('본인증번호').str.strip_chars()).join(
        df2.rename({'효율등급 인증번호': '본인증번호'}).with_columns(
            pl.col('본인증번호').str.strip_chars()
        ),
        on='본인증번호',
        how='left',
    )
    df.write_excel(root / f'{src.stem}_ZEB-Join.xlsx')

    rich.print(df.shape)
    rich.print(df.select('등급{ZEB}', '신재생에너지-자립률').describe())


app.command(app_eda := App('eda'))


@app_eda.command
def eda_count():
    """구간별 개수."""
    conf = Config.read()
    cnst = conf.cnst
    data = Dataset(filter_policy_interval=False).lazyframe()

    with Workbook(conf.dirs.prep / f'{conf.source.stem}_구간별개수.xlsx') as wb:
        (
            data
            .filter(pl.col(cnst.POLICY_INTERVAL).is_in(['P1', 'P2', 'P3', 'P4', 'P5']))
            .group_by(cnst.USE, cnst.AREA_INTERVAL)
            .len('count')
            .sort(pl.all())
            .collect()
            .write_excel(wb, worksheet='면적구간별')
        )
        (
            data
            .group_by(cnst.USE, cnst.POLICY_INTERVAL)
            .len('count')
            .sort(pl.all())
            .collect()
            .pivot('건물용도', index='정책구간', values='count')
            .write_excel(wb, worksheet='정책구간별')
        )
        (
            data
            .group_by(cnst.USE, cnst.POLICY_INTERVAL, cnst.AREA_INTERVAL)
            .len('count')
            .collect()
            .pivot(
                cnst.POLICY_INTERVAL,
                index=[cnst.USE, cnst.AREA_INTERVAL],
                values='count',
                sort_columns=True,
            )
            .sort(pl.all())
            .fill_null(0)
            .write_excel(wb, worksheet='인증&면적구간별')
        )


@app_eda.command
def eda_describe_area(*, filter_cert_duration: bool = False):
    # XXX Dataset 사용 수정
    conf = Config.read()
    use = '건물용도'
    df = (
        pl
        .scan_parquet(conf.dirs.prep / f'{conf.source.stem}.parquet')
        .with_columns(pl.col(use).replace({'주거용': '주거', '주거용 이외': '비주거'}))
        .sort(use)
        .select(use, cs.ends_with('면적'), '예비인증소요일')
        .drop(cs.ends_with('_면적'))
    )

    if filter_cert_duration:
        df = df.filter(pl.col('예비인증소요일') <= conf.cnst.YEAR2DAYS)

    desc = pl.concat([
        d.drop(use).describe().select(pl.lit(by[0]).alias(use), pl.all())
        for by, d in df.collect().group_by(use, maintain_order=True)
    ])

    rich.print(desc)
    desc.write_excel(conf.dirs.prep / f'{conf.source.stem}_건물용도별 description.xlsx')


@app_eda.command
def eda_count_by_period(*, non_residential_only=True):
    conf = Config.read()
    cnst = conf.cnst
    data = Dataset().lazyframe()

    name = '정책구간별 개수'
    if non_residential_only:
        data = data.filter(pl.col(cnst.USE) == '비주거')
        name = f'{name} (비주거)'

    variables = [
        cnst.MAIN_USE,
        cnst.AREA_INTERVAL,
        '지역',
        cnst.USE,
    ]
    with Workbook(conf.dirs.prep / f'{conf.source.stem}_{name}.xlsx') as wb:
        for var in variables:
            (
                data
                .group_by(cnst.POLICY_INTERVAL, var)
                .len()
                .collect()
                .pivot(cnst.POLICY_INTERVAL, index=var, values='len', sort_columns=True)
                .sort(var)
                .fill_null(0)
                .write_excel(wb, worksheet=var)
            )


app.command(app_plot := App('plot'))


@app_plot.command
def plot_pair():
    """전처리 된 데이터 numerical 변수 pair plot."""
    conf = Config.read()
    root = conf.dirs.plot

    df = (
        Dataset()
        .lazyframe()
        .with_columns(pl.col('1차에너지소요량_합계').cast(pl.Float64, strict=False))
        .select(
            '건물용도',
            '지역',
            pl.col(conf.cnst.POLICY_INTERVAL).str.strip_prefix('P').cast(pl.Int8),
            pl.col('1차에너지소요량_합계').log().alias('log(1차소요합계)'),
            pl.col('예비인증소요일'),
        )
    )

    for use in ['주거', '비주거']:
        dff = df.filter(pl.col('건물용도') == use).collect()

        grid = sns.PairGrid(
            dff.to_pandas(),
            hue='지역',
            hue_order=['중부1', '중부2', '남부', '제주'],
            height=2,
            despine=False,
        )
        grid.map_diag(sns.histplot, hue=None)
        grid.map_upper(sns.kdeplot, hue=None)
        grid.map_lower(sns.scatterplot, alpha=0.5)
        grid.add_legend()

        grid.figure.savefig(root / f'pair_{use}.png')
        plt.close(grid.figure)


@app_plot.command
def plot_single_dist():
    conf = Config.read()
    root = conf.dirs.plot
    data = (
        Dataset(
            filter_policy_interval=False,
            filter_cert_duration=False,
        )
        .lazyframe()
        .with_columns(pl.col('1차에너지소요량_합계').cast(pl.Float64, strict=False))
    )
    variables = [
        '연면적',
        '건물주용도',
        '지역',
        conf.cnst.POLICY_INTERVAL,
        conf.cnst.AREA_INTERVAL,
        '예비인증소요일',
        '1차에너지소요량_합계',
    ]

    for var in variables:
        df = data.select(var, '건물용도').drop_nulls().collect()
        xy = {'y': var} if var == '건물주용도' else {'x': var}

        if var in {conf.cnst.POLICY_INTERVAL, conf.cnst.AREA_INTERVAL}:
            df = df.sort(var)
        elif var == '지역':
            df = df.sort(
                pl.col('지역').replace({'중부1': 0, '중부2': 1, '남부': 2, '제주': 3})
            )

        grid = sns.displot(
            df,
            **xy,  # pyright: ignore[reportArgumentType]
            col='건물용도',
            col_order=['주거', '비주거'],
            facet_kws={'despine': False},
            log_scale=var in {'연면적', '예비인증소요일'},
        )

        if var == '예비인증소요일':
            ax: Axes
            for ax in grid.axes.flat:
                ax.axvline(365, c='coral', ls='--')

        grid.figure.set_layout_engine('constrained')
        grid.figure.suptitle(f'{var} 분포 (n={df.height})')
        grid.figure.savefig(root / f'분포_단일변수_{var} (FilterX).png')

        plt.close(grid.figure)


@app_plot.command
def plot_period_cat():
    conf = Config.read()
    root = conf.dirs.plot
    cnst = conf.cnst
    data = Dataset().lazyframe()

    periods = (
        data
        .select(pl.col(cnst.POLICY_INTERVAL).unique().sort())
        .collect()
        .to_series()
        .to_list()
    )
    regions = {x: i for i, x in enumerate(cnst.REGIONS)}

    utils.mpl.MplTheme(context='paper', fig_size=(16, 12)).apply()

    for use, var in product(
        ['주거', '비주거'], ['지역', cnst.MAIN_USE, cnst.AREA_INTERVAL]
    ):
        count = (
            data
            .filter(pl.col(cnst.USE) == use)
            .select(var, cnst.POLICY_INTERVAL)
            .group_by(var, cnst.POLICY_INTERVAL)
            .len()
            .collect()
        ).sort(pl.col(var).replace(regions) if var == '지역' else pl.col(var))

        # bar
        grid = sns.catplot(
            count,
            x='len',
            y=var,
            col=cnst.POLICY_INTERVAL,
            col_order=periods,
            col_wrap=3,
            kind='bar',
            height=3.75,
        ).set_axis_labels('건수', '')
        grid.savefig(root / f'분포_정책구간별_{use}_bar_{var}.png')
        plt.close(grid.figure)

        # heatmap
        wide = (
            count
            .pivot(cnst.POLICY_INTERVAL, index=var, values='len')
            .fill_null(0)
            .select(var, *periods)
        )

        if var == '지역':
            wide = wide.sort(pl.col('지역').replace(regions))
        else:
            wide = wide.sort(var)

        fig, ax = plt.subplots()
        sns.heatmap(
            wide.to_pandas().set_index(var),
            ax=ax,
            annot=True,
            linewidths=0.1,
            cmap=cmr.ocean,
            fmt='d',
        )
        fig.savefig(root / f'분포_정책구간별_{use}_heatmap_{var}.png')
        plt.close(fig)


@app_plot.command
def plot_period_area(threshold: int = 30):
    conf = Config.read()
    root = conf.dirs.plot
    cnst = conf.cnst

    data = (
        Dataset()
        .lazyframe()
        .with_columns(pl.col(cnst.POLICY_INTERVAL).str.strip_prefix('P').cast(pl.Int8))
    )
    cm = Colormap('crameri:batlow').to_matplotlib()

    for use in ['주거', '비주거']:
        df = (
            data
            .filter(pl.col(cnst.USE) == use, pl.len().over(cnst.MAIN_USE) >= threshold)
            .select('연면적', cnst.MAIN_USE, cnst.POLICY_INTERVAL)
            .collect()
        )
        col_wrap = utils.mpl.ColWrap(df.select(cnst.MAIN_USE).n_unique()).ncols

        grid = sns.displot(
            df,
            x='연면적',
            hue=cnst.POLICY_INTERVAL,
            col=cnst.MAIN_USE,
            col_wrap=col_wrap,
            log_scale=True,
            palette=cm,
            height=2.5,
            facet_kws={'sharey': False},
            alpha=0.5,
            common_norm=False,
            kde=True,
        ).set_titles('{col_name}')
        grid.figure.suptitle(f'{use} 용도별 연면적 분포 (n≥{threshold})')
        grid.figure.set_layout_engine('constrained')
        grid.savefig(root / f'분포_정책구간별_{use}_연면적_용도별.png')
        plt.close(grid.figure)

        fig, ax = plt.subplots()
        sns.histplot(
            df,
            x='연면적',
            hue=cnst.POLICY_INTERVAL,
            log_scale=True,
            kde=True,
            alpha=0.5,
            palette=cm,
            ax=ax,
        )
        fig.savefig(root / f'분포_정책구간별_{use}_연면적.png')
        plt.close(fig)


if __name__ == '__main__':
    import warnings

    warnings.simplefilter('ignore', UserWarning)

    utils.terminal.LogHandler.set()
    utils.mpl.MplTheme().grid().apply()

    app()

    # TODO 인증번호 unique 체크
