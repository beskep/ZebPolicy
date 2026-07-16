import dataclasses as dc
from operator import mul
from typing import TYPE_CHECKING, ClassVar

import eco2.report
import more_itertools as mi
import polars as pl
import polars.selectors as cs
import structlog
from cyclopts.config import Toml

import zeb.emission
import zeb.y2026.common as comm
from zeb.utils.cli import App
from zeb.y2026.config import Paths  # noqa: TC001

if TYPE_CHECKING:
    from pathlib import Path

app = App(
    config=Toml('env.toml', root_keys=['2026', 'paths'], use_commands_as_keys=False)
)
logger = structlog.stdlib.get_logger()


@app.command
@dc.dataclass
class Prep:
    _: dc.KW_ONLY
    paths: Paths

    BATCHREPORT: ClassVar[str] = 'batchreport.tab'
    AREA_FIX: ClassVar[dict[str, str]] = {
        '1878.42 // 1549.68': '1549.68',
        '31031.96 // 19726.98': '19726.98',
        '19017.02 (전:9337.64 326세대)': '19017.02',
        '32928.31 (용:16505.68': '16505.68',
        '35542.73 (용22774.09)': '22774.09',
        '142.841.7416': '142841.7416',
        '135415.55(820세대)': '135415.55',
    }

    def check_batch_reports(self):
        # 계산 결과가 없는 폴더 체크
        for d in self.paths.eco2.glob('**/*/'):
            if not (d / self.BATCHREPORT).exists():
                logger.warning('%s not found: %s', self.BATCHREPORT, d)

    @staticmethod
    def read(src: Path):
        try:
            report = eco2.report.BatchReport(src, kwargs={'encoding': 'UTF-8'}).raw
        except UnicodeError, pl.exceptions.ComputeError:
            report = eco2.report.BatchReport(src, kwargs={'encoding': 'korean'}).raw

        use = mi.one(x for x in src.parts if x in comm.USES)
        logger.info('shape=%s, use=%s, src=%s', report.shape, use, src)

        return report.insert_column(0, pl.lit(use).alias('use'))

    def prep(self):
        reports = (self.read(x) for x in self.paths.eco2.rglob(self.BATCHREPORT))
        area = ('대지면적', '연면적', '건축면적')
        data = (
            pl
            .concat(reports, how='vertical_relaxed')
            .with_columns(pl.col('file').str.extract_groups(comm.Case.PATTERN))
            .unnest('file')
            .rename({'scale_a': 'scale.a', 'scale_c': 'scale.c'})
            .with_columns(
                pl.col('index').cast(pl.UInt8),
                pl.col(area).cast(pl.String).name.suffix('_원본'),
                pl
                .col(area)
                .str.strip_chars()
                .str.replace_all(',', '')
                .replace(self.AREA_FIX)
                .cast(pl.Float64),
            )
            .sort(pl.all())
        )

        data.write_parquet(self.paths.eco2 / '00.raw.parquet')
        data.write_excel(self.paths.eco2 / '00.raw.xlsx')
        self.paths.eco2.joinpath('00.glimpse.txt').write_text(
            data.glimpse(return_type='string')
        )

        return data

    def __call__(self):
        self.check_batch_reports()
        self.prep()


@app.command
@dc.dataclass
class Emission:
    _: dc.KW_ONLY
    paths: Paths

    INDEX: ClassVar[tuple[str, ...]] = (
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
        '주체',
    )
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
        emission_factors = zeb.emission.EmissionFactors.read().dataframe()

        data = (
            pl
            .scan_parquet(self.paths.eco2 / '00.raw.parquet')
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
                .then(pl.col('function'))
                .otherwise(pl.lit('생산')),
                source=pl
                .when(pl.col('src1').is_null())
                .then(pl.col('src2'))
                .otherwise(pl.col('src1')),
            )
            .filter(pl.col('source') != '총량')
            .drop('src1', 'src2')
            .with_columns(
                (
                    pl.col('value')
                    * pl.col('function').replace_strict(
                        {'생산': -1}, default=1, return_dtype=pl.Float64
                    )
                ).alias('value'),
                pl.col('source').replace_strict(self.SOURCE),
            )
            .collect()
            .join(emission_factors, on='source', how='left', validate='m:m')
        )

        data.write_parquet(self.paths.eco2 / '01.emission.parqauet')
        data.head(1000).write_csv(
            self.paths.eco2 / '01.emission.sample.csv', include_bom=True
        )

        return data


if __name__ == '__main__':
    app()
