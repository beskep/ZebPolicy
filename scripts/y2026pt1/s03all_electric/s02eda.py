import tomllib
from pathlib import Path

import cyclopts
import matplotlib.pyplot as plt
import polars as pl
import seaborn as sns
import structlog
from matplotlib.figure import Figure

from zeb.utils.cli import App
from zeb.y2026.common import Grade

_ENV = 'env.toml'

app = App(
    config=cyclopts.config.Toml(
        _ENV,
        root_keys=['2026', 'all-electric'],
        allow_unknown=True,
        use_commands_as_keys=False,
    ),
    result_action=['call_if_callable', 'print_non_int_sys_exit'],
)
logger = structlog.stdlib.get_logger()


@app.command
def parse(root: Path):
    """ECO2 연산 폴더의 batchreport 파일 복사, 열 추가."""
    env = tomllib.loads(Path(_ENV).read_text('utf-8'))
    working_dir = Path(env['2026']['all-electric']['edit']['root'])
    head = (
        'index',
        'file',
        'owner',
        'scale_c',
        'scale_a',
        'use',
        'bldg',
        'region',
        'grade',
    )

    data = (
        pl
        .scan_parquet(working_dir / '03.pv.required/batchreport.parquet')
        .with_columns(
            pl
            .col('file')
            .str.extract_groups(
                r'^(?<owner>공|민)'
                r'(?:-(?:C(?<scale_c>\d)))?-(?:A(?<scale_a>\d))-'
                r'(?<use>\w)-(?<bldg>\d+)'
                r'(?:_전환)?'
                r'-(?<region>\w+)-(?<grade>Base|ZEB[1-5\+])-PV.*'
            )
            .alias('group')
        )
        .unnest('group')
        .with_columns(pl.col('scale_c', 'scale_a', 'bldg').cast(pl.UInt8))
        .select(*head, pl.all().exclude(head))
        .with_columns(
            pl.col('value').alias('value.raw'),
            pl
            .col('value')
            .str.replace_all(',', '')
            .cast(pl.Float64, strict=False)
            .alias('value'),
        )
        .collect()
    )

    data.write_parquet(root / '01.raw.parquet')
    data.filter(pl.col('file') == pl.col('file').first()).write_csv(
        root / '01.raw.sample.csv', include_bom=True
    )

    return data.glimpse(return_type='string')


@app.command
def check_eir(root: Path):
    data = (
        pl
        .scan_parquet(root / '01.raw.parquet')
        .filter(pl.col('variable') == '에너지자립률')
        .collect()
    )

    plt.style.use('custom.mplstyle')
    fig = Figure()
    ax = fig.subplots()

    grade = data['grade'].unique()
    order = [x for x in Grade if x in grade]
    sns.boxplot(data, x='value', y='grade', order=order, ax=ax)
    ax.set_xlabel('자립률 [%]')
    ax.set_ylabel('')

    fig.savefig(root / '02.EIR.png')


if __name__ == '__main__':
    app()
