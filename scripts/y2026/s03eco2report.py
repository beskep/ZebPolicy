import dataclasses as dc
from typing import TYPE_CHECKING, ClassVar

import eco2.report
import more_itertools as mi
import polars as pl
import polars.selectors as cs
import structlog
from cyclopts.config import Toml

import zeb.emission
import zeb.y2026.common as comm
from zeb import utils
from zeb.utils.cli import App
from zeb.y2026.common import Grade
from zeb.y2026.config import Paths  # ruff:ignore[typing-only-first-party-import]

if TYPE_CHECKING:
    from pathlib import Path

app = App(
    config=Toml(
        'env.toml', root_keys='2026', allow_unknown=True, use_commands_as_keys=False
    )
)
logger = structlog.stdlib.get_logger()


def _read(src: Path):
    try:
        report = eco2.report.BatchReport(src, kwargs={'encoding': 'UTF-8'}).raw
    except UnicodeError, pl.exceptions.ComputeError:
        report = eco2.report.BatchReport(src, kwargs={'encoding': 'korean'}).raw

    use = mi.one(x for x in src.parts if x in comm.USES)
    logger.debug('shape=%s, use=%s, src=%s', report.shape, use, src)

    return report.insert_column(0, pl.lit(use).alias('use'))


@app.command
def read(*, paths: Paths, batchreport: str = 'batchreport.tab'):
    # 계산 결과가 없는 폴더 체크
    for d in paths.eco2raw.glob('**/*/'):
        if not (d / batchreport).exists():
            logger.warning('%s not found: %s', batchreport, d)

    reports = (_read(x) for x in paths.eco2raw.rglob(batchreport))
    data = pl.concat(reports, how='vertical_relaxed')

    paths.eco2.analysis.mkdir(exist_ok=True)
    data.write_parquet(paths.eco2.analysis / '00.raw.parquet')

    return data


@app.command
@dc.dataclass
class Parse:
    _: dc.KW_ONLY
    paths: Paths

    INDEX: ClassVar[tuple[str, ...]] = (
        'bldg',
        'use',
        'owner',
        'scale.c',
        'scale.a',
        'purpose',
        'index',
        'region',
        'grade',
        '대지면적',
        '연면적',
        '건축면적',
    )
    AREA_FIX: ClassVar[dict[str, str]] = {
        '1878.42 // 1549.68': '1549.68',
        '31031.96 // 19726.98': '19726.98',
        '19017.02 (전:9337.64 326세대)': '19017.02',
        '32928.31 (용:16505.68': '16505.68',
        '35542.73 (용22774.09)': '22774.09',
        '142.841.7416': '142841.7416',
        '135415.55(820세대)': '135415.55',
    }

    def __call__(self):
        root = self.paths.eco2.analysis
        area = ('대지면적', '연면적', '건축면적')
        data = (
            pl
            .scan_parquet(self.paths.eco2.analysis / '00.raw.parquet')
            .with_columns(
                pl.col('use').replace_strict({'res': '주거', 'non-res': '비주거'}),
                pl.col('file').str.extract_groups(comm.Case.PATTERN),
            )
            .unnest('file')
            .rename({'scale_a': 'scale.a', 'scale_c': 'scale.c'})
            .with_columns(pl.col('scale.c').fill_null('null'))
            .with_columns(
                pl.format(
                    '{}.{}.{}.{}.{}.{}',
                    pl.col('use').str.slice(0, 1),
                    'owner',
                    'scale.a',
                    'scale.c',
                    'purpose',
                    'index',
                ).alias('bldg'),
                pl.col('owner').replace_strict({'공': '공공', '민': '민간'}),
                pl.col('purpose').replace_strict({
                    '기': '기타',
                    '상': '상업',
                    '교': '교육사회',
                    '공': '공동주택',
                    '단': '단독주택',
                }),
                pl.col('index').cast(pl.UInt8),
                pl.col(area).cast(pl.String).name.suffix('_원본'),
                pl
                .col(area)
                .str.strip_chars()
                .str.replace_all(',', '')
                .replace(self.AREA_FIX)
                .cast(pl.Float64),
            )
            .select(*self.INDEX, pl.all().exclude(self.INDEX))
            .sort(pl.all())
            .collect()
        )

        owner = pl.col('owner')
        grade = pl.col('grade')

        baseline = (
            (data)
            .filter(
                (owner.eq('민간') & grade.eq(Grade.NOPV))
                | (owner.eq('공공') & grade.eq(Grade.BASE))
            )
            .with_columns(pl.lit(Grade.BASE).alias('grade'))
        )
        sub5 = (
            (data)
            .filter(grade == Grade.BASE)
            .with_columns(pl.lit(Grade.SUB5).alias('grade'))
        )
        data = pl.concat([
            baseline,
            sub5,
            data.filter(grade.is_in([Grade.BASE, Grade.SUB5, Grade.NOPV]).not_()),
        ])

        data.write_parquet(root / '01.parsed.parquet')
        data.write_excel(root / '01.parsed.xlsx')
        root.joinpath('01.glimpse.txt').write_text(data.glimpse(return_type='string'))

        return data


@app.command
@dc.dataclass
class Emission:
    _: dc.KW_ONLY
    paths: Paths

    INDEX: ClassVar[tuple[str, ...]] = Parse.INDEX
    SOURCE: ClassVar[dict[str, str]] = {
        '난방유(등유)': '등유',
        '액화가스(LPG)': 'LPG',
        '열': '열',
        '전기': '전력',
        '전력': '전력',
        '지역난방연료': '지역난방',
        '지역난방열': '지역난방',
        '지역냉방열': '지역냉방',
        '천연가스(LNG)': 'LNG',
    }

    def __call__(self):
        root = self.paths.eco2.analysis
        emission_factors = zeb.emission.EmissionFactors.read().dataframe()

        fn = pl.col('function')
        src = pl.col('source')

        data = (
            pl
            .scan_parquet(root / '01.parsed.parquet')
            .select(
                *self.INDEX,
                cs.starts_with(
                    # unit kWh
                    '1차에너지소요량/',
                    '전기에너지 생산량',
                    '열에너지 생산량',
                ),
            )
            .unpivot(index=self.INDEX)
            .sort([*self.INDEX, 'variable'])
            .with_columns(
                pl
                .col('variable')
                .str.extract_groups(
                    r'^1차에너지소요량/'
                    r'(?P<function>냉방|난방|급탕|조명|환기)'
                    r'(?:/(?P<src1>[\w()]+))?'
                    r'|(?P<src2>전기|열)에너지 생산량'
                    r'\((?<renewable>태양광|태양열|열병합|풍력|수열|지열)\)$'
                )
                .alias('groups')
            )
            .unnest('groups')
            .with_columns(
                function=pl
                .when(pl.col('src2').is_null())
                .then(fn)
                .otherwise(pl.lit('생산')),
                source=pl
                .when(pl.col('src1').is_null())
                .then(pl.col('src2'))
                .otherwise(pl.col('src1')),
            )
            .filter(src != '총량')
            .drop('src1', 'src2')
            .with_columns(
                (
                    pl.col('value')
                    * fn.replace_strict('생산', -1, default=1, return_dtype=pl.Float64)
                ).alias('value'),
                src.replace_strict(self.SOURCE),
            )
            .with_columns(
                pl
                .format('{}{}', fn.replace_strict('생산', '생산.', default=''), src)
                .replace_strict({
                    'LPG': 'direct',
                    'LNG': 'direct',
                    '등유': 'direct',
                    '전력': 'indirect',
                    '지역난방': 'indirect',
                    '지역냉방': 'indirect',
                    '생산.전력': 'generation.elec',
                    '생산.열': 'generation.heat',
                })
                .alias('scope')
            )
            .collect()
            .join(emission_factors, on='source', how='left', validate='m:m')
        )

        data.write_parquet(root / '02.emission.parquet')
        (
            root
            .joinpath()
            .joinpath('02.emission.glimpse.txt')
            .write_text(data.glimpse(return_type='string'), encoding='utf-8')
        )
        data.head(1000).write_csv(root / '02.emission.sample.csv', include_bom=True)

        utils.pl.PolarsSummary(data.rename({'variable': 'var'})).write_excel(
            root / '02.emission.summary.xlsx'
        )

        return data


if __name__ == '__main__':
    app()
