"""2026-06-02 기존 건물 ZEB 전환 분석."""

import dataclasses as dc
import enum
import functools
from typing import Literal

import cyclopts
import polars as pl
import polars.selectors as cs
import seaborn as sns

from zeb import utils
from zeb.utils.cli import App
from zeb.y2026.config import Paths  # ruff:ignore[typing-only-first-party-import]

app = App(
    config=cyclopts.config.Toml(
        'env.toml',
        root_keys='2026',
        allow_unknown=True,
        use_commands_as_keys=False,
    )
)


class V(enum.StrEnum):
    GFA = '연면적'
    HM = '온열원설비_난방방식'
    HT = '온열원설비_적용기기'
    HF = '온열원설비_사용연료'
    HE = '온열원설비_효율'
    CM = '냉열원설비_냉방방식'
    CT = '냉열원설비_적용기기'
    CF = '냉열원설비_사용연료'
    CE = '냉열원설비_효율'
    L = '조명설비_주거실조명전력(W/㎡)'


@app.command
def prep(paths: Paths):
    data = (
        pl
        .read_excel(paths.eco2.raw / paths.certificates)
        .with_columns(
            pl
            .col('인증신청일', '접수일', '인증서 발행일')
            .str.strip_chars()
            .replace('', None)
            .str.to_date(),
            pl.col('인증년도').replace('', None).cast(pl.UInt16),
        )
        .with_columns(
            pl
            .col('대지면적', '연면적', '건축면적', V.CE, V.L)
            .str.strip_chars()
            .str.replace_all(',', '')
            .replace({'': None, '0': None})
            .cast(pl.Float64, strict=False),
            cs
            .contains('에너지요구량', '에너지소요량', 'CO₂발생량')
            .str.strip_chars()
            .str.replace_all(',', '')
            .replace('', None)
            .cast(pl.Float64),
            # 온열원 효율 (% 표기 처리)
            (
                pl
                .col(V.HE)
                .str.strip_chars(' %')
                .str.replace_all(',', '')
                .replace('', None)
                .cast(pl.Float64)
                * pl
                .col(V.HE)
                .str.ends_with('%')
                .replace_strict({True: 0.01, False: 1}, return_dtype=pl.Float64)
            ).alias(V.HE),
        )
    )
    paths.eco2.existing.mkdir(exist_ok=True)

    glimpse = data.glimpse(return_type='string')
    paths.eco2.existing.joinpath('00.glimpse.txt').write_text(glimpse)

    data.write_parquet(paths.eco2.existing / '01.raw.parquet')

    return data


@app.command
@dc.dataclass
class Eda:
    paths: Paths

    @functools.cached_property
    def raw(self):
        return pl.scan_parquet(self.paths.eco2.existing / '01.raw.parquet')

    @functools.cached_property
    def data(self):
        return self.raw.filter(
            pl.col('인증구분') == '예비인증',
            pl.col('건물용도') == '주거용 이외',
            pl.col('인증년도').is_in([2010, 2011]),
        ).collect()

    def describe(self):
        for df, suffix in (
            (self.raw, '-raw'),
            (self.data, '-filtered'),
        ):
            (
                df
                .describe(interpolation='linear')
                .with_columns()
                .write_excel(
                    self.paths.eco2.existing / f'02.describe{suffix}.xlsx',
                    column_widths=150,
                )
            )

        for name, v in (('난방설비', V.HT), ('냉방설비', V.CT)):
            (
                (utils.pl)
                .PolarsSummary(
                    self.data.select(list(V)), group=v, interpolation='linear'
                )
                .write_excel(
                    self.paths.eco2.existing / f'02.describe-group-{name}.xlsx',
                    column_widths=150,
                )
            )

    def plot(self, v: Literal['heating', 'cooling']):
        match v:
            case 'heating':
                t = V.HT
                e = V.HE
            case 'cooling':
                t = V.CT
                e = V.CE

        data = self.data.select('연면적', t, e, V.L).sort(t)
        grid = sns.pairplot(
            data.to_pandas(), hue=t, diag_kind='hist', plot_kws={'alpha': 0.8}
        )
        grid.savefig(self.paths.eco2.existing / f'03.grid-{v}.png')

    def __call__(self):
        self.describe()

        utils.mpl.MplTheme().grid().apply()
        self.plot('heating')
        self.plot('cooling')

        return self.data


if __name__ == '__main__':
    app()
