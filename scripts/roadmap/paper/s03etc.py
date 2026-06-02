import enum
from pathlib import Path
from typing import Literal

import cyclopts
import polars as pl
import polars.selectors as cs

from scripts.roadmap.s04interval_eda import PolicyTarget2
from zeb import utils

app = utils.cli.App(
    help_on_error=True,
    config=cyclopts.config.Toml(
        path=Path(__file__).parent / 'config.toml', root_keys='etc'
    ),
    result_action=['call_if_callable', 'print_non_int_sys_exit'],
)


@app.command
def requirement(src: Path, dst: Path):
    class V(enum.StrEnum):
        period = '정책구간'
        grade = '등급'
        requirement = '에너지소요량_합계'

    data = pl.scan_parquet(src).select(V.period, V.grade, V.requirement).collect()
    for group in [[V.grade], [V.period, V.grade]]:
        name = '+'.join(group)
        utils.pl.PolarsSummary(data, group=group).write_excel(dst / f'요약-{name}.xlsx')


@app.command
def use(src: Path, dst: Path, year: int = 2023):
    class V(enum.StrEnum):
        bldg_index = '신청번호'
        grade = '등급'
        area = '연면적'
        use = '건물주용도'

        consumption = '사용량'
        year = 'year'
        outlier = 'outlier'

    bldg = (
        pl
        .scan_parquet(src / '0001building.parquet')
        .select(V.bldg_index, V.grade, V.area, V.use)
        .collect()
    )
    energy = (
        pl
        .scan_parquet(src / '0101outlier.parquet')
        .filter(pl.col(V.year) == year)
        .rename({'합계': V.consumption})
        .select(V.bldg_index, V.year, V.consumption, V.outlier)
        .collect()
    )
    data = (
        (energy)
        .join(bldg, on=V.bldg_index, how='left')
        .with_columns(pl.col(V.consumption).truediv(V.area).alias(V.consumption))
    )

    for f in ['keep', 'drop']:
        d = data if f == 'keep' else data.filter(pl.col(V.outlier).is_null())
        (
            (utils.pl)
            .PolarsSummary(d.drop(V.bldg_index, V.area), group=[V.year, V.use, V.grade])
            .write_excel(dst / f'등급별 사용량_outlier={f}.xlsx')
        )

    return data


@app.command
def use2(
    src: Path,
    dst: Path,
    year: int = 2023,
    target: Literal['EUI', 'EUIadj'] = 'EUIadj',
):
    class V(enum.StrEnum):
        bldg_index = '신청번호'
        grade = '등급'
        area = '연면적'
        use = '건물주용도'
        owenership = '주체'

        year = '연도'

    data = (
        pl
        .scan_parquet(src / '0201EUIanalysis.parquet')
        .filter(pl.col(V.year) == year, pl.col('variable') == target)
        .collect()
    )

    group = [V.use, V.grade, V.year, V.owenership]
    (
        (utils.pl)
        .PolarsSummary(data.select(*group, cs.starts_with('EUI')), group=group)
        .write_excel(dst / f'지역+등급+주체별 {target}.xlsx')
    )


@app.command
def count_profile(src: Path, dst: Path, *, classify_use: bool = False):
    class V(enum.StrEnum):
        use = '건물주용도'
        period = '정책구간'
        ownership = '주체'

    data = pl.scan_parquet(src)
    if classify_use:
        data = data.with_columns(pl.col(V.use).replace_strict(PolicyTarget2.USE))

    count = (
        data
        .group_by(V.period, V.use, V.ownership)
        .len()
        .sort(pl.all())
        .collect()
        .pivot(V.period, index=[V.use, V.ownership], values='len', sort_columns=True)
    )

    count.write_excel(dst / '정책구간+용도+주체별 데이터 개수.xlsx', column_widths=100)

    return count


if __name__ == '__main__':
    app()
