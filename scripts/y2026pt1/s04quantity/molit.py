"""
건축허가·착공·준공 현황 통계.

https://stat.molit.go.kr/portal/cate/statView.do?hRsId=466&hFormId=7061&hDivEng=&month_yn=
https://www.index.go.kr/unity/potal/main/EachDtlPageDetail.do?idx_cd=1224
"""

import functools
import re
import shutil
from dataclasses import KW_ONLY, dataclass
from typing import ClassVar, Literal
lazy from pathlib import Path

import cyclopts
import fastexcel
import polars as pl
import polars.selectors as cs
import seaborn as sns
import structlog
from matplotlib.figure import Figure
from tqdm.rich import tqdm
lazy import rich
lazy from matplotlib.axes import Axes

from zeb.cli import App
lazy from zeb.y2026.common import Owner, Purpose
lazy from zeb.y2026.config import Paths

Stat = Literal['착공', '준공']
STAT: tuple[Stat, Stat] = ('착공', '준공')

OWNER_MAP: dict[str, Owner | Literal['기타']] = {
    '개인': '민간',
    '일반법인': '민간',
    '주택조합': '민간',
    '주택업체': '민간',
    'LH': '공공',
    '지방공사': '공공',
    '정부투자기관': '공공',
    '지방자치단체': '공공',
    '국가기관': '공공',
    '기타': '기타',
}
PURPOSE_MAP: dict[str, Purpose] = {
    '단독주택': '단독주택',
    '공동주택': '공동주택',
    '제1종근린생활시설': '상업',
    '제2종근린생활시설': '상업',
    '문화및집회시설': '교육사회',
    '종교시설': '교육사회',
    '판매시설': '상업',
    '운수시설': '상업',
    '의료시설': '교육사회',
    '교육연구시설': '교육사회',
    '노유자시설': '교육사회',
    '수련시설': '교육사회',
    '운동시설': '교육사회',
    '업무시설': '상업',
    '숙박시설': '상업',
    '위락시설': '상업',
    '공장': '기타',
    '창고시설': '기타',
    '위험물저장및처리시설': '상업',
    '자동차관련시설': '상업',
    '동.식물 관련시설': '기타',
    '분뇨.쓰레기처리시설': '기타',  # -> 자원순환관련시설
    '교정및군사시설': '기타',
    '방송통신시설': '기타',
    '발전시설': '기타',
    '묘지관련시설': '교육사회',
    '관광휴게시설': '교육사회',
    '장례식장': '교육사회',
    '자원순환관련시설': '기타',
    '장례시설': '교육사회',
    '야영장시설': '상업',
}

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
    DATE: ClassVar[re.Pattern] = re.compile(
        r'^(?P<year>\d{4})(년_?|\.)?(?P<month>\d{1,2})[월\.\+].*'
    )

    @functools.cached_property
    def _reader(self):
        return fastexcel.read_excel(self.src)

    def _reference_date(self):
        try:
            index = ' '.join(
                self._reader
                .load_sheet('목차', dtypes='string')
                .to_polars()
                .select(pl.all().str.join(' '))
                .row()
            )
        except fastexcel.SheetNotFoundError:
            index = None

        if index and (m := self.REF.search(index)):
            return pl.date(*(int(m.group(x)) for x in [1, 2, 3]))

        return pl.lit(None, dtype=pl.Date)

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
        if not (m := self.DATE.match(self.src.name)):
            return pl.lit(None, dtype=pl.Date)

        return pl.date(year=int(m.group('year')), month=int(m.group('month')), day=1)

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
            h: ('string' if c else 'float')
            for h, c in zip(header, category, strict=True)
        }

        data = self._load_sheet(header_row=hr + 1, dtypes=dtypes).to_polars()
        data.columns = header

        return data

    def __call__(self):
        return self._data().select(
            pl.lit(self.src.name).alias('source'),
            self._date().alias('date'),
            self._reference_date().alias('reference_date'),
            pl.all(),
        )


@app.command
@dataclass
class Parse(_Command):
    source: Literal['molit', 'index'] = 'molit'
    # stat.molit.go.kr 또는 www.index.go.kr 자료 폴더 -- 결과 비슷함

    def read(self, stat: Stat):
        ref = {'molit': 'molit', 'index': 'index.go.kr'}[self.source]
        source = list(self.paths.raw.joinpath(ref).glob(f'*{stat}*.xls'))
        sheet = f'주체별({stat})'

        return pl.concat(
            (MolitData(x, sheet)() for x in tqdm(source)),
            how='diagonal',
        )

    def __call__(self):
        for stat in STAT:
            logger.info(stat)

            data = self.read(stat)
            data.write_parquet(self.paths.data / f'MOLIT.00.{stat}.parquet')


@app.command
class Filter(_Command):
    # 같은 연월의 데이터 중 자료기준일(reference_date)이 나중인 행 선택

    @staticmethod
    def _filter(path: Path):
        raw = (
            pl
            .scan_parquet(path)
            .filter(pl.col('시도명') == '총계', pl.col('용도별') != '계')
            .collect()
        )

        dates = raw.select('source', 'date', 'reference_date').unique()
        if not dates.filter(pl.col('date').is_duplicated()).height:
            return raw, None

        recent = dates.group_by('date').agg(
            pl.all().sort_by('reference_date', nulls_last=False).last()
        )
        filtered = recent.select('source').join(raw, on='source', how='inner')

        return raw, filtered

    def _main(self, stat: Stat):
        logger.info(stat)
        src = self.paths.data / f'MOLIT.00.{stat}.parquet'
        dst = self.paths.data / f'MOLIT.01.{stat}.parquet'
        raw, filtered = self._filter(src)

        if filtered is None:
            logger.info('Not filtered')
            shutil.copy2(src, dst)
            data = raw
        else:
            logger.info('height', raw=raw.height, filtered=filtered.height)
            filtered.write_parquet(dst)
            data = filtered

        (
            data
            .describe(interpolation='linear')
            .unpivot(index='statistic')
            .pivot('statistic', index='variable', values='value')
            .write_csv(self.paths.eda / f'MOLIT.{stat}.describe.csv', include_bom=True)
        )

        if (negative := data.filter(pl.any_horizontal(cs.float() < 0))).height:
            logger.warning('Negative value')
            negative.write_csv(
                self.paths.eda / f'MOLIT.{stat}.negative.csv', include_bom=True
            )
            rich.print(negative)

    def __call__(self):
        for stat in STAT:
            self._main(stat)


@app.command
@dataclass
class Summary(_Command):
    years: tuple[int, int] = (2015, 2025)
    check_duplicate: bool = False

    def _process(self, stat: Stat):
        return (
            pl
            .scan_parquet(self.paths.data / f'MOLIT.01.{stat}.parquet')
            .filter(pl.col('시도명') == '총계', pl.col('용도별') != '계')
            .with_columns(pl.col('용도별').replace_strict(PURPOSE_MAP).alias('purpose'))
            .rename({'구분': 'variable'})
            .unpivot(
                list(OWNER_MAP),
                index=['date', 'purpose', 'variable'],
                variable_name='owner',
            )
            .with_columns(pl.col('owner').replace_strict(OWNER_MAP))
            .group_by('date', 'purpose', 'owner', 'variable')
            .agg(pl.sum('value'))
            .sort(pl.all())
            .collect()
        )

    @staticmethod
    def plot(data: pl.DataFrame, stat: str):
        fig = Figure(figsize=(32, 18, 'cm'))
        axes = fig.subplots(2, 1, squeeze=False, sharey=False)

        ax: Axes
        for ax, v in zip(axes.ravel(), ('동수', '연면적'), strict=True):
            sns.lineplot(
                data.filter(pl.col('variable') == v).to_dict(),
                x='date',
                y='value',
                hue='purpose',
                style='owner',
                ax=ax,
            )

            unit = '[m²]' if v == '연면적' else ''
            ax.set_xlabel('')
            ax.set_ylabel(f'{stat} {v}{unit}')

        return fig

    def _summarize(self, data: pl.DataFrame):
        index = ['purpose', 'owner', 'variable']
        data = (
            data
            .with_columns(pl.col('date').dt.year().alias('year'))
            .group_by('year', *index, maintain_order=True)
            .agg(pl.sum('value'))
        )

        filtered = data.filter(pl.col('year').is_between(*self.years, closed='left'))
        avg = filtered.group_by(index, maintain_order=True).agg(pl.mean('value'))
        years = filtered.select(min=pl.min('year'), max=pl.max('year')).row()
        avg = avg.select(pl.lit('{}~{}'.format(*years)).alias('year'), pl.all())

        return (
            pl
            .concat(
                [avg, data.with_columns(pl.col('year').cast(pl.String))],
            )
            .pivot(
                'variable',
                index=['year', 'purpose', 'owner'],
                values='value',
                sort_columns=True,
            )
            .rename({'purpose': '용도', 'owner': '건축주', '연면적': '연면적 [m²]'})
        )

    def __call__(self):
        for stat in STAT:
            data = self._process(stat)
            self.plot(data, stat).savefig(self.paths.eda / f'MOLIT.{stat}.summary.png')
            self._summarize(data).write_csv(
                self.paths.eda / f'MOLIT.{stat}.summary.csv', include_bom=True
            )


if __name__ == '__main__':
    app()
