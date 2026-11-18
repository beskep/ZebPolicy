"""
건축허가·착공·준공 현황 통계.

https://stat.molit.go.kr/portal/cate/statView.do?hRsId=466&hFormId=7061&hDivEng=&month_yn=
https://www.index.go.kr/unity/potal/main/EachDtlPageDetail.do?idx_cd=1224
"""

import functools
import re
from dataclasses import KW_ONLY, dataclass
from typing import ClassVar, Literal
lazy from pathlib import Path

import cyclopts
import fastexcel
import polars as pl
import rich
import structlog
from tqdm.rich import tqdm

from zeb.cli import App
lazy from zeb.y2026.config import Paths

Stat = Literal['착공', '준공']
STAT: tuple[Stat, Stat] = ('착공', '준공')

app = App(
    config=cyclopts.config.Toml(
        'env.toml',
        root_keys='2026',
        allow_unknown=True,
        use_commands_as_keys=False,
    )
)
logger = structlog.stdlib.get_logger()


@dataclass
class _Command:
    _: KW_ONLY
    _paths: Paths

    @property
    def paths(self):
        return self._paths.quantity


@dataclass(frozen=True)
class MolitData:
    src: Path
    sheet: str

    REF: ClassVar[re.Pattern] = re.compile(
        r'자료기준일\s?:\s?(\d{4})\.(\d{1,2})\.(\d{1,2})'
    )
    DATE: ClassVar[re.Pattern] = re.compile(r'^(\d+)년_?(\d+)월.*')

    @functools.cached_property
    def _reader(self):
        return fastexcel.read_excel(self.src)

    def _reference_pattern(self):
        if '목차' not in self._reader.sheet_names:
            return None

        cols: tuple[str, ...] = (
            self._reader
            .load_sheet('목차', dtypes='string')
            .to_polars()
            .select(pl.all().str.join(' '))
            .row()
        )

        for col in cols:
            if m := self.REF.search(col):
                return m

        return None

    def _reference_date(self):
        if not (m := self._reference_pattern()):
            return pl.lit(None, dtype=pl.Date)

        return pl.date(*(int(m.group(x)) for x in [1, 2, 3]))

    def _load_sheet(
        self,
        header_row: int = 0,
        n_rows: int | None = None,
        dtypes: fastexcel.DType | fastexcel.DTypeMap | None = None,
    ):
        return self._reader.load_sheet(
            self.sheet, header_row=header_row, n_rows=n_rows, dtypes=dtypes
        )

    def _date(self):
        if not (m := re.match(r'^(\d+)년_?(\d+)월.*', self.src.name)):
            return pl.lit(None, dtype=pl.Date)

        return pl.date(year=int(m.group(1)), month=int(m.group(2)), day=1)

    def _data(self):
        raw = self._load_sheet(n_rows=0)
        hr = 0 if raw.selected_columns[0].name == '시도명' else 2
        h1 = self._load_sheet(header_row=hr, n_rows=0).selected_columns
        h2 = self._load_sheet(header_row=hr + 1, n_rows=0).selected_columns

        category = [
            x.column_name_from == 'looked_up' and x.name != '건축주' for x in h1
        ]
        header = [
            (x.name if c else y.name).replace('\n', '')
            for x, y, c in zip(h1, h2, category, strict=True)
        ]
        dtypes: dict = {
            h: 'float' if c else 'string' for h, c in zip(header, category, strict=True)
        }

        data = self._load_sheet(header_row=hr + 1, dtypes=dtypes).to_polars()
        data.columns = header

        return data

    def __call__(self):
        return self._data().select(
            self._date().alias('date'),
            self._reference_date().alias('reference_date'),
            pl.all(),
        )


@app.command
@dataclass
class Parse(_Command):
    def read(self, stat: Stat):
        source = list(self.paths.raw.joinpath('molit').glob(f'*{stat}*.xls'))
        sheet = f'주체별({stat})'

        return pl.concat(
            (MolitData(x, sheet)() for x in tqdm(source)),
            how='diagonal',
        )

    def __call__(self):
        index = ['date', '시도명', '용도별', '용도별(상세)', '구분']

        for stat in STAT:
            logger.info(stat)

            data = self.read(stat)
            data.write_parquet(self.paths.data / f'00.MOLIT.{stat}.raw.parquet')

            # 같은 연월의 데이터 중 자료기준일(reference_date)이 나중인 행 선택
            (
                data
                .group_by(index, maintain_order=True)
                .agg(pl.all().sort_by('reference_date', nulls_last=False).last())
                .write_parquet(self.paths.data / f'00.MOLIT.{stat}.parquet')
            )


@app.command
@dataclass
class Inspect(_Command):
    def _source(self, stat: Stat):
        return self.paths.data / f'00.MOLIT.{stat}.parquet'

    def _check_duplicate(self, stat: Stat):
        dup = (
            pl
            .scan_parquet(self._source(stat))
            .filter(
                pl.col('시도명') == '총계',
                pl.col('용도별') == '계',
                pl.col('구분') == '연면적',
            )
            .filter(pl.col('date').is_duplicated())
            .collect()
        )

        if dup.height:
            logger.warning('Duplicated dates', stat=stat)
            rich.print(dup.select('source', 'date', '계'))

    def __call__(self):
        for stat in STAT:
            self._check_duplicate(stat)


if __name__ == '__main__':
    app()
