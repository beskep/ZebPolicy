"""2026-06-02 기존 건물 ZEB 전환 분석."""

import dataclasses as dc
import enum
import functools

import cyclopts
import polars as pl
import polars.selectors as cs
import seaborn as sns

from zeb import utils
from zeb.utils.cli import App
from zeb.y2026.config import Paths  # noqa: TC001

app = App(
    config=cyclopts.config.Toml(
        'env.toml',
        root_keys='2026',
        allow_unknown=True,
        use_commands_as_keys=False,
    ),
    result_action=['call_if_callable', 'print_non_none_return_zero'],
)


class V(enum.StrEnum):
    GFA = '연면적'
    HT = '온열원설비_난방방식'
    HF = '온열원설비_사용연료'
    HE = '온열원설비_효율'
    CT = '냉열원설비_냉방방식'
    CF = '냉열원설비_사용연료'
    CE = '냉열원설비_효율'
    L = '조명설비_주거실조명전력(W/㎡)'


@app.command
def prep(paths: Paths):
    data = (
        pl
        .read_excel(paths.raw / paths.certificates)
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
            # 온열원 효율 (% 표기 포함)
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
    paths.existing.mkdir(exist_ok=True)

    glimpse = data.glimpse(return_type='string')
    paths.existing.joinpath('00.glimpse.txt').write_text(glimpse)

    data.write_parquet(paths.existing / '01.raw.parquet')

    return data


@app.command
@dc.dataclass
class Eda:
    paths: Paths

    @functools.cached_property
    def raw(self):
        return pl.scan_parquet(self.paths.existing / '01.raw.parquet')

    @functools.cached_property
    def data(self):
        return (
            self.raw
            .filter(
                pl.col('인증구분') == '예비인증',
                pl.col('건물용도') == '주거용 이외',
                pl.col('인증년도').is_in([2010, 2011]),
            )
            .with_columns(type=pl.format('{}&{}', V.HT, V.CT))
            .with_columns(
                pl
                .when(pl.col(V.HF).is_in(['액화가스', '천연가스']))
                .then(pl.col(V.HE))
                .otherwise(pl.lit(None))
                .alias(V.HE),
                pl
                .when(pl.col(V.CF).is_in(['액화가스', '천연가스']))
                .then(pl.col(V.CE))
                .otherwise(pl.lit(None))
                .alias(V.CE),
            )
            .collect()
        )

    def describe(self):
        for df, suffix in (
            (self.raw, '-raw'),
            (self.data, '-filtered'),
            (self.data.select(list(V)), ''),
        ):
            (
                df
                .describe(interpolation='linear')
                .with_columns()
                .write_excel(
                    self.paths.existing / f'02.describe{suffix}.xlsx',
                    column_widths=150,
                )
            )

    def plot(self):
        data = self.data.select('연면적', V.HE, V.CE, V.L, 'type')
        grid = sns.pairplot(data.to_pandas(), hue='type', diag_kind='hist')
        grid.savefig(self.paths.existing / '03.grid.png')

    def __call__(self):
        self.describe()

        utils.mpl.MplTheme().grid().apply()
        self.plot()

        return self.data


if __name__ == '__main__':
    app()
