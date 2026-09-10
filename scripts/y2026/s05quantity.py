import functools
import itertools
import re
from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING, Literal

import cyclopts
import matplotlib.pyplot as plt
import more_itertools as mi
import polars as pl
import polars.selectors as cs
import rich
import seaborn as sns
import structlog
from cmap import Colormap
from matplotlib.figure import Figure

from zeb import utils
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


VARIABLES = {
    '관리_허가대장_PK': 'pk',
    '건물_명': 'bldg',
    '사용승인_일': 'date',
    '주_용도_코드': 'use.code',
    '주_용도_코드_명': 'use',
    '연면적(㎡)': 'gfa',
    '용적_률_산정_연면적(㎡)': 'gfa.ground',
    '세대_수(세대)': 'unit.strata',
    '호_수(호)': 'unit.room',
    '가구_수(가구)': 'unit.household',
}


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

            data = src
        else:
            data = pl.read_parquet(src)
            name = src.stem

        head = data.head(rows)
        tail = data.tail(rows)

        head.write_csv(self.paths.data / f'{name}.head.csv', include_bom=True)
        tail.write_csv(self.paths.data / f'{name}.tail.csv', include_bom=True)

        (
            self.paths.data
            .joinpath(f'{name}.glimpse.txt')
            .joinpath()
            .write_text(data.glimpse(return_type='string', max_items_per_column=4))
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

    def read(self):
        src = mi.one(self.paths.data.glob('00.*.parquet'))
        return (
            pl
            .scan_parquet(src)
            .filter(pl.col('건축_구분_코드_명') == '신축')
            .rename(VARIABLES)
            .select(list(VARIABLES.values()))
            .with_columns(
                pl.col('date').str.strip_chars().str.to_date('%Y%m%d', strict=False)
            )
            .collect()
        )

    def write(self, data: pl.DataFrame):
        data.write_parquet(self.paths.data / '01.data.parquet')

        data = data.drop_nulls('date')
        sample = data.sample(10000, seed=42).sort('pk')
        sample.write_csv(self.paths.data / '01.data.sample.csv', include_bom=True)
        (
            self.paths.data
            .joinpath('01.data.glimpse.txt')
            .joinpath()
            .write_text(data.glimpse(return_type='string'))
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

        data = self.read()
        logger.info('허가일 인식률')
        rich.print(
            data.select(
                pl.col('date').null_count().alias('null'),
                pl.col('date').len().alias('total'),
            ).with_columns(
                (pl.col('null') / pl.col('total')).alias('r_null'),
                (1 - pl.col('null') / pl.col('total')).alias('r_normal'),
            )
        )

        use = pl.read_csv('data/2026/use.csv')
        columns = [*VARIABLES.values(), 'scale.a', 'scale.c']
        data = (
            data
            .filter(
                pl.col('date').dt.year().is_between(*self.years)
                | pl.col('date').is_null()
            )
            .rename({'use': 'use.raw'})
            .drop_nulls(['use.code', 'use.raw'])
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
                .col('unit.strata')
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
    fmt: Literal['svg', 'png'] = 'svg'

    @property
    def lf(self):
        return pl.scan_parquet(self.paths.data / '01.data.parquet')

    @functools.cached_property
    def data(self):
        return (
            self.lf.drop_nulls('date').with_columns(pl.col('gfa') / 1000000).collect()
        )

    @functools.cached_property
    def cmap(self):
        return Colormap('cmasher:ocean').to_mpl()

    def _units(self, xscale: str | None = 'symlog'):
        data = self.data.unpivot(cs.starts_with('unit.'), index='use').with_columns(
            pl.col('variable').replace_strict({
                'unit.strata': '세대',
                'unit.room': '호',
                'unit.household': '가구',
            })
        )

        if xscale == 'log':
            data = data.filter(pl.col('value') != 0)

        grid = sns.FacetGrid(
            data,
            col='use',
            col_wrap=3,
            col_order=['교육사회용', '상업용', '기타', '단독주택', '공동주택'],
            hue='variable',
            height=2.5,
            aspect=4 / 3,
            sharex=False,
            sharey=False,
            despine=False,
        )

        if xscale:
            for ax in grid.axes_dict.values():
                ax.set_xscale(xscale)

        (
            grid
            .map_dataframe(sns.histplot, x='value', bins='doane', element='step')
            .set_axis_labels('유닛 수')
            .set_titles('')
            .set_titles('{col_name}', loc='left', weight=500)
            .add_legend(title='')
        )
        utils.mpl.move_grid_legend(grid)
        grid.figure.savefig(self.paths.eda / f'01.units.{xscale}.{self.fmt}')

    def _heatmap(
        self,
        x: Literal['year', 'area', 'strata'],
        v: Literal['count', 'gfa'],
        *,
        raw: bool = False,
    ):
        xvar = {'year': 'year', 'area': 'scale.a', 'strata': 'scale.c'}[x]

        with plt.rc_context({
            'axes.grid': False,
            'xtick.bottom': False,
            'xtick.top': False,
            'ytick.left': False,
            'ytick.right': False,
        }):
            fig = Figure(figsize=(32, 24, 'cm') if raw else (16, 12, 'cm'))
            ax = fig.subplots()

        use = pl.format('{}.{}', 'use.code', 'use.raw') if raw else pl.col('use')
        groupby = (
            self.data
            .with_columns(
                pl.col('date').dt.year().alias('year'),
                use.alias('use'),
            )
            .with_columns()
            .group_by(xvar, 'use')
        )

        match v:
            case 'count':
                data = groupby.len('value')
                title = '신규 허가 건수'
                fmt = '.0f'
            case 'gfa':
                data = groupby.agg(pl.sum('gfa').alias('value'))
                title = '신규 연면적 [km²]'
                fmt = '.2f'

        data = (
            data
            .pivot(xvar, index='use', values='value', sort_columns=True)
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

        u = 'raw' if raw else 'use'
        path = self.paths.eda / f'02.{x}.{v}.{u}.{self.fmt}'
        fig.savefig(path)

    def _apartment(self):
        return (
            self.lf.filter(pl.col('use') == '공동주택').sort('pk').tail(10000).collect()
        )

    def __call__(self):
        self.paths.eda.mkdir(exist_ok=True)

        self._units('symlog')
        self._units('log')

        for x, v, r in itertools.product(
            ('year', 'area', 'strata'), ('count', 'gfa'), (False, True)
        ):
            logger.info('heatmap', x=x, v=v, r=r)
            self._heatmap(x, v, raw=r)

        self._apartment().write_csv(
            self.paths.eda / '03.apartment.csv', include_bom=True
        )


@app.command
def use():
    return (
        pl
        .read_csv('data/2026/use.csv')
        .group_by('use')
        .agg(pl.col('use.raw'))
        .with_columns(pl.col('use.raw').list.join(', '))
        .to_dicts()
    )


if __name__ == '__main__':
    plt.style.use('custom.mplstyle')
    app()
