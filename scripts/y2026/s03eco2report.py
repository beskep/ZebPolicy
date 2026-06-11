import dataclasses as dc
from typing import TYPE_CHECKING, ClassVar

import eco2.report
import polars as pl
import structlog
from cyclopts.config import Toml

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
    paths: Paths

    BATCHREPORT: ClassVar[str] = 'batchreport.tab'

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

        logger.info('shape=%s, src=%s', report.shape, src)

        return report

    def prep(self):
        reports = (self.read(x) for x in self.paths.eco2.rglob(self.BATCHREPORT))
        data = (
            pl
            .concat(reports, how='vertical_relaxed')
            .with_columns(pl.col('file').str.extract_groups(comm.Case.PATTERN))
            .unnest('file')
        )

        data.write_parquet(self.paths.eco2 / '00.raw.parquet')
        self.paths.eco2.joinpath('00.raw-glimpse.txt').write_text(
            data.glimpse(return_type='string')
        )

        return data

    def __call__(self):
        self.check_batch_reports()
        self.prep()


if __name__ == '__main__':
    app()
