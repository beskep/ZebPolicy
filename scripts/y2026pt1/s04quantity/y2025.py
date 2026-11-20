"""2025년 분석 결과 변환."""

lazy from pathlib import Path

import cyclopts
import polars as pl
import structlog

from zeb.cli import App
lazy from zeb.y2026.config import Paths

app = App(
    config=cyclopts.config.Toml(
        'env.toml',
        root_keys='2026',
        allow_unknown=True,
        use_commands_as_keys=False,
    )
)
logger = structlog.stdlib.get_logger()


def _read(src: str | Path):
    data = pl.read_csv(src)

    if 'use' not in data.columns:
        data = data.with_columns(pl.lit('공동주택').alias('use'))  # TODO: 확인

    return data.unpivot(index=['variable', 'use', 'owner'], variable_name='scale')


@app.default
def main(paths: Paths):
    sources = [
        'data/2026/quantity.non-residential.csv',
        'data/2026/quantity.residential.csv',
    ]
    data = pl.concat(_read(x) for x in sources).sort(pl.all())

    data.write_parquet(paths.quantity.data / '2025.parquet')
    (
        data
        .filter(pl.col('variable') == '인증면적__m2')
        .select(
            pl.concat_str('use', 'scale', 'owner', separator='-').alias('case'), 'value'
        )
        .rename({'value': '인증면적__m2'})
        .write_csv(paths.quantity.data / '2025_case.csv', include_bom=True)
    )

    return data


if __name__ == '__main__':
    app()
