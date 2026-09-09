import functools
import itertools
import re
from dataclasses import KW_ONLY, asdict, dataclass, field
from typing import TYPE_CHECKING, Literal

import cyclopts
import matplotlib.pyplot as plt
import more_itertools as mi
import polars as pl
import seaborn as sns
import structlog
from cmap import Colormap
from matplotlib.figure import Figure

from zeb.utils.cli import App
from zeb.y2026.config import Paths  # ruff: ignore[typing-only-first-party-import]

if TYPE_CHECKING:
    from pathlib import Path

app = App(
    config=cyclopts.config.Toml(
        'env.toml',
        root_keys='2026',
        allow_unknown=True,
        use_commands_as_keys=False,
    )
)
logger = structlog.stdlib.get_logger()


class _V:
    PERMISSION_DATE = '사용승인_일'
    USE_CODE = '주_용도_코드'
    USE = '주_용도_코드_명'
    GFA = '연면적(㎡)'


@dataclass
class Variables:
    pk: str = '관리_허가대장_PK'
    date: str = '사용승인_일'
    use_code: str = '주_용도_코드'
    use: str = '주_용도_코드_명'
    gfa: str = '연면적(㎡)'
    agfa: str = '용적_률_산정_연면적(㎡)'
    households: str = '세대_수(세대)'


@dataclass
class _Command:
    _: KW_ONLY
    _paths: Paths

    @property
    def paths(self):
        return self._paths.quantity


@app.command
@dataclass
class Parse(_Command):
    """
    건축Hub 대용량 데이터 해석.

    https://www.hub.go.kr/portal/opn/lps/idx-lgcpt-pvsn-srvc-list.do
    """

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

        head = lf.head(rows).collect()
        tail = lf.tail(rows).collect()

        head.write_csv(self.paths.data / f'{name}.head.csv', include_bom=True)
        tail.write_csv(self.paths.data / f'{name}.tail.csv', include_bom=True)

        (
            self.paths.data
            .joinpath(f'{name}.glimpse.txt')
            .joinpath()
            .write_text(tail.glimpse(return_type='string'))
        )

        if not self.summary:
            return

    def __call__(self):
        self.paths.data.mkdir(exist_ok=True)

        for src in self.paths.raw.glob('*'):
            if not src.is_dir():
                continue

            name = self._name(src.name)
            parquet = self.paths.data / f'00.{name}.parquet'
            logger.info('parsing', data=name)

            if parquet.exists():
                self.sample(parquet)
            else:
                data = self.read(src)
                data.write_parquet(parquet)

                self.sample(data, name=parquet.stem)


@app.command
@dataclass
class Prep(_Command):
    """데이터 전처리 - 날짜 해석 및 변수 추출."""

    # XXX: 민간/공공 구분 불가, 전용면적 불명

    years: tuple[int, int] = (2021, 2025)
    variables: Variables = field(default_factory=Variables)

    def read(self):
        src = mi.one(self.paths.data.glob('00.*.parquet'))
        variables = asdict(self.variables)
        return (
            pl
            .scan_parquet(src)
            .with_columns(
                pl
                .col(self.variables.date)
                .str.strip_chars()
                .str.to_date('%Y%m%d', strict=False)
            )
            .filter(pl.col(self.variables.date).dt.year().is_between(*self.years))
            .select(list(variables.values()))
            .rename({v: k for k, v in variables.items()})
            .rename({'use_code': 'use.code'})
            .collect()
        )

    def write(self, data: pl.DataFrame):
        data.write_parquet(self.paths.data / '01.data.parquet')

        sample = data.sample(10000, seed=42).sort('pk')
        sample.write_csv(self.paths.data / '01.data.sample.csv', include_bom=True)
        (
            self.paths.data
            .joinpath('01.data.glimpse.txt')
            .joinpath()
            .write_text(sample.glimpse(return_type='string'))
        )

        (
            data
            .select('use.code', 'use.raw', 'use')
            .unique()
            .drop_nulls('use.code')
            .sort('use.code')
            .write_csv(self.paths.data / '01.data.use.csv', include_bom=True)
        )

    def __call__(self):
        self.paths.data.mkdir(exist_ok=True)

        use = pl.read_csv('data/2026/use.csv')
        columns = [
            'pk',
            'date',
            'use.code',
            'use.raw',
            'use',
            'gfa',
            'agfa',
            'households',
            'scale.a',
            'scale.c',
        ]
        data = (
            self
            .read()
            .rename({'use': 'use.raw'})
            .join(use, on=['use.code', 'use.raw'], how='left', validate='m:1')
            .with_columns(
                pl
                .col('gfa')
                .cut(
                    [1e-8, 500, 1000, 3000, 10000],
                    labels=[f'A{x}' for x in range(6)],
                    left_closed=True,
                )
                .alias('scale.a'),
                pl
                .col('households')
                .cut(
                    [1e-8, 300, 500, 1000],
                    labels=[f'C{x}' for x in range(5)],
                    left_closed=True,
                )
                .alias('scale.c'),
            )
            .select(*columns, pl.all().exclude(columns))
        )

        self.write(data)

        return data


@app.command
@dataclass
class EDA(_Command):
    @functools.cached_property
    def data(self):
        return (
            pl
            .read_parquet(self.paths.data / '01.data.parquet')
            .with_columns(pl.col('gfa') / 1000000)
            .with_columns()
        )

    @functools.cached_property
    def cmap(self):
        return Colormap('cmasher:ocean').to_mpl()

    def _heatmap(self, v: Literal['count', 'gfa'], *, raw: bool = False):
        with plt.rc_context({
            'axes.grid': False,
            'xtick.bottom': False,
            'xtick.top': False,
            'ytick.left': False,
            'ytick.right': False,
        }):
            fig = Figure(figsize=(32, 24, 'cm') if raw else (16 * 1.2, 9 * 1.2, 'cm'))
            ax = fig.subplots()

        use = pl.format('{}.{}', 'use.code', 'use.raw') if raw else pl.col('use')
        groupby = (
            self.data
            .with_columns(
                pl.col('date').dt.year().alias('year'),
                use.alias('use'),
            )
            .with_columns()
            .group_by('year', 'use')
        )

        match v:
            case 'count':
                data = groupby.len('value')
                title = '허가 건수'
                fmt = '.0f'
            case 'gfa':
                data = groupby.agg(pl.sum('gfa').alias('value'))
                title = '연면적 [km²]'
                fmt = '.2f'

        data = (
            data
            .pivot('year', index='use', values='value', sort_columns=True)
            .sort('use')
            .with_columns()
        )
        sns.heatmap(
            data.to_pandas().set_index('use'),
            ax=ax,
            annot=True,
            fmt=fmt,
            cmap=self.cmap,
        )
        ax.set_title(title, loc='left', weight=500)

        ax.set_ylabel('')
        fig.savefig(self.paths.eda / f'01.{v}{".raw" if raw else ""}.png')

    def __call__(self):
        self.paths.eda.mkdir(exist_ok=True)

        for v, r in itertools.product(('count', 'gfa'), (False, True)):
            self._heatmap(v, raw=r)


if __name__ == '__main__':
    plt.style.use('custom.mplstyle')
    app()
