import dataclasses as dc
from itertools import chain
from typing import TYPE_CHECKING, ClassVar

import eco2.report
import polars as pl
import polars.selectors as cs
import structlog
from cyclopts.config import Toml
from tqdm.rich import tqdm

import zeb.emission
import zeb.y2026.common as comm
from zeb import utils
from zeb.utils.cli import App
from zeb.y2026.common import INDEX, Grade
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
    logger.debug(src.as_posix())

    s = src.as_posix()
    if 'existing' in s:
        electrification = '기존'
    elif 'all-electric' in s:
        electrification = '전환'
    else:
        raise ValueError(s)

    try:
        report = eco2.report.BatchReport(src, encoding='utf8').raw
    except UnicodeError, pl.exceptions.ComputeError:
        report = eco2.report.BatchReport(src, encoding='korean').raw

    logger.debug(
        'shape=%s, src=%s, electrification=%s', report.shape, src, electrification
    )

    return report.select(
        pl.lit(src.as_posix()).alias('src'),
        pl.lit(electrification).alias('electrification'),
        pl.all(),
    )


@app.command
def read_raw(*, paths: Paths, batchreport: str = 'batchreport.tab'):
    subdirs = [paths.eco2raw.existing, paths.eco2raw.all_electric]

    # 계산 결과가 없는 폴더 체크
    for d in chain.from_iterable(x.glob('**/') for x in subdirs):
        if not (d / batchreport).exists():
            logger.warning('%s not found: %s', batchreport, d)

    src = list(chain.from_iterable(x.rglob(batchreport) for x in subdirs))
    reports = (_read(x) for x in tqdm(src))
    data = pl.concat(reports, how='vertical_relaxed')
    data.write_parquet(paths.eco2.root / '00.raw.parquet')

    paths.eco2.root.joinpath('00.raw.glimpse.txt').write_text(
        data.glimpse(return_type='string')
    )

    return data


@app.command
@dc.dataclass
class Parse:
    _: dc.KW_ONLY
    paths: Paths

    INDEX: ClassVar[tuple[str, ...]] = INDEX.CASE
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
        root = self.paths.eco2.root
        area = ('대지면적', '연면적', '건축면적')
        data = (
            pl
            .scan_parquet(root / '00.raw.parquet')
            .with_columns(
                pl.col('file').str.extract_groups(comm.Case.PATTERN).alias('group')
            )
            .unnest('group')
            .rename({'scale_a': 'scale.a', 'scale_c': 'scale.c'})
            .with_columns(
                pl.format(
                    '{}.{}.{}.{}.{}',
                    'owner',
                    'scale.a',
                    pl.col('scale.c').fill_null('N'),
                    'purpose',
                    'index',
                ).alias('bldg'),
                pl.col('electrification').cast(pl.Enum(comm.ELECTRIFICATION)),
                pl
                .col('owner')
                .replace_strict({'공': '공공', '민': '민간'})
                .cast(pl.Enum(comm.OWNER)),
                pl
                .col('purpose')
                .replace_strict({
                    '기': '기타',
                    '상': '상업',
                    '교': '교육사회',
                    '공': '공동주택',
                    '단': '단독주택',
                })
                .cast(pl.Enum(comm.PURPOSE)),
                pl.col('region').cast(pl.Enum(comm.REGIONS)),
                pl.col('index').cast(pl.UInt8),
                pl.col(area).cast(pl.String).name.suffix('_원본'),
                pl
                .col(area)
                .str.strip_chars()
                .str.replace_all(',', '')
                .replace(self.AREA_FIX)
                .cast(pl.Float64),
            )
            .select('src', *self.INDEX, pl.all().exclude([*self.INDEX, 'src']))
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
        zeb = data.filter(grade.is_in([Grade.BASE, Grade.SUB5, Grade.NOPV]).not_())
        data = (
            pl
            .concat([baseline, sub5, zeb])
            .with_columns(pl.col('grade').cast(pl.Enum([x.value for x in Grade])))
            .with_columns()
        )

        data.write_parquet(root / '01.parsed.parquet')
        data.write_excel(root / '01.parsed.xlsx')
        root.joinpath('01.parsed.glimpse.txt').write_text(
            data.glimpse(return_type='string')
        )

        return data


@app.command
@dc.dataclass
class Emission:
    _: dc.KW_ONLY
    paths: Paths

    INDEX: ClassVar[tuple[str, ...]] = INDEX.CASE
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
        root = self.paths.eco2.root
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
                    r'\((?P<renewable>태양광|태양열|열병합|풍력|수열|지열)\)$'
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
            .rename({'value': 'delivered'})  # kWh
            .join(emission_factors.lazy(), on='source', how='left', validate='m:m')
            .with_columns(
                pl
                .col('delivered')
                .mul(pl.col('emission_factor'))
                .alias('emission'),  # kg
            )
            .collect()
        )

        data.write_parquet(root / '02.emission.parquet')
        (
            root
            .joinpath()
            .joinpath('02.emission.glimpse.txt')
            .write_text(data.glimpse(return_type='string'), encoding='utf-8')
        )
        (
            data
            .filter(pl.col('bldg') == '공.A1.N.교.1')
            .with_columns()
            .write_csv(root / '02.emission.sample.csv', include_bom=True)
        )

        (
            (utils.pl)
            .PolarsSummary(data.rename({'variable': 'var'}))
            .write_excel(root / '02.emission.summary.xlsx')
        )

        return data


if __name__ == '__main__':
    app()
