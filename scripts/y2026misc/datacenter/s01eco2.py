# 2026-07-27 데이터 센터 변경, 분석
# 1. 전산실 용도프로필 냉방 설정온도 변경 : 26 -> 28, 30
# 2. 전산실 용도프로필 기기발열부하 변경 : 1800 -> 1440(80%), 900(50%)
# 3. 전산실 용도프로필 급탕요구량 변경 : 30 -> 0

import dataclasses as dc
import enum
import functools
import re
from pathlib import Path  # ruff:ignore[typing-only-standard-library-import]
from typing import TYPE_CHECKING, Literal

import cyclopts
import eco2.editor
import eco2.report
import more_itertools as mi
import polars as pl
import seaborn as sns
import structlog
import xlsxwriter
from matplotlib.figure import Figure
from tqdm.rich import tqdm

from zeb import utils
from zeb.utils.cli import App
from zeb.y2026.config import Paths  # ruff: ignore[typing-only-first-party-import]

if TYPE_CHECKING:
    from matplotlib.axes import Axes

app = App(
    config=cyclopts.config.Toml(
        'env.toml',
        root_keys='2026',
        allow_unknown=True,
        use_commands_as_keys=False,
    )
)
logger = structlog.stdlib.get_logger()

APPLICATION_NUMBER = re.compile(r'^(?P<appnum>\d+)_.*\.tpl(x)?$')


class Parameter(enum.StrEnum):
    TC = '냉방설정온도'
    TH = '난방설정온도'
    QE = '작업보조기기'
    HW = '일일급탕요구량'


@dc.dataclass
class Cases:
    tc: tuple[float, ...] = (26, 27, 28, 29, 30)
    th: tuple[float, ...] = (20, 19, 18)
    qe: tuple[float, ...] = (1800, 1440, 900)
    hw: tuple[float, ...] = (30, 0)


@dc.dataclass(frozen=True)
class _Editor(eco2.editor.Eco2Editor):
    tc: tuple[float, ...] = (26, 27, 28, 29, 30)  # FIXME
    th: tuple[float, ...] = (20, 19, 18)
    qe: tuple[float, ...] = (1800, 1440, 900)
    hw: tuple[float, ...] = (30, 0)

    @functools.cached_property
    def cases_count(self):
        return sum(len(self._values(p)) - 1 for p in Parameter)

    @functools.cached_property
    def server_room_profile(self):
        return mi.one(
            e
            for e in self.xml.ds.iterfind('tbl_profile')
            if e.findtext('설명') == '10 전산실'
        )

    def _values(self, parameter: Parameter) -> tuple[int, ...]:
        return getattr(self, parameter.name.lower())

    def edit(self, parameter: Parameter, index: int):
        for p in Parameter:
            i = index if p == parameter else 0
            value = self._values(p)[i]
            eco2.editor.set_child_text(self.server_room_profile, p, value)

        return f'{parameter.name}{self._values(parameter)[index]}'

    def __call__(self):
        for p in Parameter:
            length = len(self._values(p))
            for idx in range(1, length):
                yield self.edit(p, idx)


@app.command
@dc.dataclass
class EditEco2:
    paths: Paths
    _: dc.KW_ONLY
    xml: bool = False

    @functools.cached_property
    def src(self):
        return self.paths.datacenter.src

    @functools.cached_property
    def edit(self):
        return self.paths.datacenter.edit

    @functools.cached_property
    def cases(self):
        def it():
            for src in self.src.glob('*'):
                if (m := APPLICATION_NUMBER.match(src.name)) is None:
                    continue

                yield m.group('appnum'), src

        return tuple(it())

    def __call__(self):
        self.edit.mkdir(exist_ok=True)

        for appnum, src in tqdm(self.cases):
            editor = _Editor(src)
            editor.write(self.edit / f'{appnum} raw.tpl')

            for c in editor():
                logger.info('application number %s, case %s', appnum, c)
                output = self.edit / f'{appnum} {c}.tpl'
                editor.write(output)

                if self.xml:
                    editor.xml.write(output.with_suffix('.xml'))


@app.command
@dc.dataclass
class ParseReport:
    paths: Paths

    @staticmethod
    def read(src: Path):
        try:
            report = eco2.report.BatchReport(src)
            report.raw  # ruff: ignore[useless-expression]
        except pl.exceptions.ComputeError:
            report = eco2.report.BatchReport(src, encoding='korean')

        return report

    @staticmethod
    def parse_file(data: pl.DataFrame):
        return (
            data
            .with_columns(
                pl
                .col('file')
                .str.extract_groups(
                    r'^(?P<appnum>\d+) (?P<cvar>[a-zA-Z]+)(?P<cval>\d+)?\.tplx?'
                )
                .alias('file')
            )
            .unnest('file')
            .rename({'cvar': 'case.variable', 'cval': 'case.value'})
            .with_columns(
                pl.col('appnum').cast(pl.UInt32),
                pl.col('case.value').cast(pl.Float64),
            )
            .insert_column(
                2,
                pl
                .col('case.variable')
                .replace({x.name: x.value for x in Parameter} | {'raw': '원본'})
                .alias('case.variable.kor'),
            )
        )

    def __call__(self):
        paths = self.paths.datacenter

        reports = [self.read(x) for x in paths.edit.glob('batchreport*.tab')]

        raw = pl.concat(x.raw for x in reports)
        raw = self.parse_file(raw)
        raw.write_parquet(paths.root / '03.report.raw.parquet')
        raw.write_excel(paths.root / '03.report.raw.xlsx')

        data = pl.concat(x.data for x in reports)
        data = self.parse_file(data)
        data.write_parquet(paths.root / '03.report.tidy.parquet')
        data.write_excel(paths.root / '03.report.tidy.xlsx')


@app.command
@dc.dataclass
class EnergyTrend:
    paths: Paths

    variables: tuple[str, ...] = ('1차에너지소요량', '등급산출용 1차에너지소요량')

    @functools.cached_property
    def data(self):
        variables = [f'{x}/합계' for x in self.variables]
        data = (
            pl
            .scan_parquet(self.paths.datacenter.root / '03.report.tidy.parquet')
            .filter(pl.col('variable').is_in(variables))
            .with_columns(
                pl
                .format(
                    '{} {}', 'case.variable.kor', pl.col('case.value').cast(pl.Int32)
                )
                .str.strip_chars()
                .alias('case'),
                pl.col('value').str.replace_all(',', '').cast(pl.Float64),
            )
            .with_columns(
                pl
                .col('value')
                .filter(pl.col('case.variable') == 'raw')
                .sum()
                .over(['appnum', 'variable'])
                .alias('raw'),
            )
            .with_columns(
                pl.col('value').truediv(pl.col('raw')).alias('ratio'),
            )
            .sort(
                pl.col('case.variable').replace_strict(
                    {'raw': 0}, default=1, return_dtype=pl.UInt8
                ),
                'case',
                'appnum',
                'index',
            )
            .collect()
        )

        assert (
            data
            .filter(pl.col('case') == 'raw')
            .select(pl.col('value').is_close('raw').all())
            .item()
        )

        return data

    def plot(self, value: Literal['value', 'ratio']):
        data = (
            self.data
            if value == 'value'
            else self.data.filter(pl.col('case') != '원본')
        )

        fig = Figure((16 * 1.2, 9 * 1.2, 'cm'))
        axes: list[Axes] = fig.subplots(1, len(self.variables), sharey=True)
        for v, ax in zip(self.variables, axes, strict=True):
            df = data.filter(pl.col('variable') == f'{v}/합계')
            unit = df['unit'][0]
            sns.boxplot(
                df,
                x=value,
                y='case',
                ax=ax,
                fill=False,
                fliersize=0,
                color='slategray',
                linewidth=1,
            )
            sns.stripplot(df, x=value, y='case', ax=ax, jitter=0.4, size=3, alpha=0.4)

            ax.set_xlabel(f'{v} [{unit}]' if value == 'value' else '원본 대비 비율')
            ax.set_ylabel('')

        fig.savefig(self.paths.datacenter.root / f'04.{value}.png')

    def __call__(self):
        utils.mpl.MplTheme().grid().apply()
        self.plot('value')
        self.plot('ratio')


@app.command
def describe(paths: Paths):
    root = paths.datacenter.root
    data = (
        pl
        .scan_parquet(root / '03.report.tidy.parquet')
        .with_columns(
            pl.col('variable').str.extract_groups(
                r'^(?<variable>(?:등급산출용 )?1차에너지소요량)/'
                r'(?<function>\w+)(?:/(?<source>.*))?$'
            )
        )
        .unnest('variable')
        .drop_nulls('variable')
        .filter(pl.col('source').is_null())
        .with_columns(
            pl.col('value').str.replace_all(',', '').cast(pl.Float64),
        )
        .drop('source')
        .collect()
    )

    cases = dc.asdict(Cases())
    raw_cases = pl.DataFrame([
        {
            'case.variable': p.name,
            'case.variable.kor': p.value,
            'case.value': cases[p.name.lower()][0],
        }
        for p in Parameter
    ])
    raw_cases = (
        data
        .filter(pl.col('case.variable') == 'raw')
        .drop('case.variable', 'case.variable.kor', 'case.value')
        .join(raw_cases, how='cross')
        .with_columns(pl.col('case.value').cast(pl.Float64))
    )

    data = (
        pl
        .concat([data, raw_cases], how='diagonal')
        .with_columns(
            pl
            .col('value')
            .filter(pl.col('case.variable') == 'raw')
            .sum()
            .over(['appnum', 'variable', 'function'])
            .alias('raw')
        )
        .with_columns(pl.col('value').truediv('raw').alias('ratio'))
    )
    data.write_parquet(root / '05.consumption.parquet')
    data.write_excel(root / '05.consumption.xlsx')

    group = [
        'case.variable',
        'case.variable.kor',
        'case.value',
        'consumption',
        'function',
    ]
    (
        utils.pl
        .PolarsSummary(
            data
            .rename({'variable': 'consumption'})
            .select([*group, 'value', 'raw', 'ratio'])
            .with_columns(),
            group=group,
        )
        .describe()
        .sort('variable', pl.all())
        .write_excel(root / '05.consumption.describe.xlsx')
    )

    return data


@app.command
def grade(paths: Paths):
    root = paths.datacenter.root
    data = (
        pl
        .scan_parquet(root / '03.report.tidy.parquet')
        .filter(
            pl.col('variable').is_in([
                '에너지자립률',
                '등급산출용 1차에너지소요량/합계',
            ])
        )
        .with_columns(
            pl.col('value').str.replace_all(',', '').cast(pl.Float64),
            pl.col('variable').replace({
                '등급산출용 1차에너지소요량/합계': '등급1차소요량'
            }),
        )
        .collect()
        .pivot(
            'variable',
            index=['appnum', 'case.variable', 'case.value'],
            values='value',
        )
        .with_columns(
            pl
            .col('에너지자립률')
            .bin_intervals(
                [20, 40, 60, 80, 100, 120],
                labels=['6', '5', '4', '3', '2', '1', '0'],
            )
            .alias('grade.eir'),
            pl
            .col('등급1차소요량')
            .bin_intervals(
                [-70, -30, 10, 50, 90, 130],
                labels=['0', '1', '2', '3', '4', '5', '6'],
            )
            .alias('grade.consumption'),
        )
        .with_columns(
            pl.col('grade.eir', 'grade.consumption').cast(pl.String).cast(pl.UInt8)
        )
        .with_columns(
            pl.min_horizontal('grade.eir', 'grade.consumption').alias('grade')
        )
        .sort(pl.all())
    )

    wide = (
        data
        .with_columns(
            pl.format(
                '{}{}',
                pl.col('case.variable'),
                pl.col('case.value').cast(pl.Int32).cast(pl.String).fill_null(''),
            ).alias('case')
        )
        .pivot('case', index='appnum', values='grade')
        .with_columns()
    )

    with xlsxwriter.Workbook(root / '05.grade.xlsx') as wb:
        data.write_excel(wb, 'raw')
        wide.write_excel(wb, 'wide')

    return data


if __name__ == '__main__':
    app()
