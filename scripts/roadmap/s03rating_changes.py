"""
2024-08-01.

등급변경검토
"""

from __future__ import annotations

from pathlib import Path  # ruff:ignore[typing-only-standard-library-import]

import polars as pl
from cyclopts import App
from eco2.report import GraphReport
from xlsxwriter import Workbook

SSR = '에너지자립률(전체)'


def report(path: Path):
    report = GraphReport(path)

    return (
        report.yearly
        .select(
            pl.lit(path.as_posix()).str.extract(r'.*(CASE \w).*').alias('case'),
            pl
            .lit(path.stem)
            .str.extract_groups(r'(?<building>\d+)-(?<building_case>\d+)')
            .alias('building'),
            pl.lit(report.data[SSR]).alias(SSR),
            pl.all(),
        )
        .unnest('building')
        .with_columns(pl.col('building', 'building_case').cast(pl.Int16))
    )


app = App()


@app.default
def main(path: Path):
    index = ['case', 'building', 'building_case', SSR]
    data = pl.concat(report(p) for p in path.rglob('*.xls'))

    wide = (
        data
        .with_columns(pl.format('{}\n{}', 'variable', 'energy').alias('column'))
        .pivot('column', index=index, values='value')
        .with_columns()
    )

    with Workbook(path / '등급변경검토.xlsx') as wb:
        wide.write_excel(
            wb, worksheet='등급변경검토', header_format={'text_wrap': True}
        )
        data.write_excel(wb, worksheet='등급변경검토tidy')


if __name__ == '__main__':
    app()
