import dataclasses as dc
import re
from typing import TYPE_CHECKING

import more_itertools as mi
import polars as pl
import polars.selectors as cs
from loguru import logger

from scripts.y2025.s05quantity.common import Dirs, app
from zeb import utils

if TYPE_CHECKING:
    from pathlib import Path


@app.default
@dc.dataclass(frozen=True)
class Preprocess:
    """
    건축Hub 대용량 데이터 전처리.

    https://www.hub.go.kr/portal/opn/lps/idx-lgcpt-pvsn-srvc-list.do
    """

    dirs: Dirs
    summary: bool = False

    @staticmethod
    def _dtype(s: str):
        if s.startswith('NUMERIC'):
            return pl.Float64
        return pl.String

    @staticmethod
    def _name(text: str):
        name = re.sub(
            r'^(.*?) \((\d+)년 (\d+)월\)',
            r'\1_\2-\3',
            text.replace('+', ' '),
        )
        return name.removeprefix('국토교통부_')

    @classmethod
    def columns(cls, path: Path):
        data = pl.read_csv(path / 'meta.tsv', separator='\t').rename(str.strip)
        cols = data['컬럼한글명'].to_list()
        schema = dict(data.select('컬럼한글명', '데이터타입').iter_rows())
        schema = {k: cls._dtype(v) for k, v in schema.items()}
        return cols, schema

    @classmethod
    def read(cls, path: Path):
        # 폴더 하나당 파일 data, metadata 하나씩 존재:
        # root
        # ├── 국토교통부_건축물대장_기본개요+(2025년+07월)
        # │   ├── mart_djy_01.txt
        # │   └── meta.tsv
        # ├── 국토교통부_건축물대장_총괄표제부+(2025년+07월)
        # │   ├── mart_djy_02.txt
        # │   └── meta.tsv
        # ├── 국토교통부_건축물대장_표제부+(2025년+07월)
        # │   ├── mart_djy_03.txt
        # │   └── meta.tsv
        # ├── 국토교통부_건축인허가_기본개요+(2025년+07월)
        # │   ├── mart_kcy_01.txt
        # │   └── meta.tsv
        # ├── 국토교통부_건축인허가_동별개요+(2025년+07월)
        # │   ├── mart_kcy_02.txt
        # │   └── meta.tsv
        # └── memo.md
        _, schema = cls.columns(path)
        p = mi.one(path.glob('*.txt'))
        return pl.read_csv(p, separator='|', quote_char=None, schema=schema)

    def sample(
        self,
        src: Path | pl.DataFrame,
        name: str | None = None,
        rows: int = 10000,
    ):
        if isinstance(src, pl.DataFrame):
            if name is None:
                msg = 'name is required'
                raise ValueError(msg)

            lf = src.lazy()
        else:
            lf = pl.scan_parquet(src)
            name = src.stem

        d = self.dirs.sample

        head = lf.head(rows).collect()
        tail = lf.tail(rows).collect()
        head.write_excel(d / f'0000.head.{name}.xlsx')
        tail.write_excel(d / f'0001.tail.{name}.xlsx')

        glimpse = d / f'0002.glimpse.{name}.txt'
        glimpse.write_text(head.glimpse(return_type='string'), encoding='utf-8')

        if not self.summary:
            return

        (
            (utils.pl)
            .PolarsSummary(lf.select(cs.numeric(), cs.temporal()))
            .write_excel(d / f'0003.sample-summary.{name}.xlsx')
        )

    def __call__(self):
        self.dirs.data.mkdir(exist_ok=True)
        self.dirs.sample.mkdir(exist_ok=True)

        for src in self.dirs.raw.glob('*'):
            if not src.is_dir():
                continue

            name = self._name(src.name)
            parquet = self.dirs.data / f'HUB_{name}.parquet'
            exists = parquet.exists()
            logger.info(f'{src.stem=} | {exists=}')

            if not exists:
                data = self.read(src)
                data.write_parquet(parquet)

                self.sample(data, name=parquet.stem)
            else:
                self.sample(parquet)


if __name__ == '__main__':
    app()
