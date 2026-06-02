"""ECO2 케이스 수정 및 분석 관련 스크립트."""

import dataclasses as dc
import functools
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

import cyclopts
import matplotlib.pyplot as plt
import more_itertools as mi
import polars as pl
import polars.selectors as cs
import rich
import seaborn as sns
from eco2 import report
from eco2.editor import Area, Eco2Xml
from loguru import logger
from matplotlib.figure import Figure
from tqdm.rich import tqdm

from zeb import utils
from zeb.utils.cli import App

if TYPE_CHECKING:
    from lxml.etree import _Element
    from matplotlib.axes import Axes


def _dict(obj: _Element, /):
    return {str(e.tag): e.text for e in obj.iterchildren()}


app = App(
    config=cyclopts.config.Toml('config.toml', root_keys='eco2'),
    result_action=['call_if_callable', 'print_non_int_sys_exit'],
)


@app.command
def pv_coefficient(path: Path, *, show: bool = False):
    """지역별 면적당 PV 발전량 계산."""
    data = pl.concat(
        report.CalculationsReport(x).data.select(pl.lit(x.stem).alias('case'), pl.all())
        for x in path.glob('*계산결과.xls')
    )
    rich.print(data)

    data = (
        data
        .filter(pl.col('변수') == '전기에너지 생산량(태양광)')
        .with_columns(
            pl
            .col('case')
            .str.strip_suffix(' 계산결과')
            .str.extract_groups(
                r'^(?<case>.*)\-(?<region>중부1|중부2|남부|제주).*'
                r'\-(?<pv>PV|BIPV)\-(?<area>\d+)$'
            )
        )
        .unnest('case')
        .with_columns(pl.format('{}-{}', 'pv', 'region').alias('case'))
        .with_columns(pl.col('area').cast(pl.Float64))
    )

    rich.print(data)

    if show:
        _, ax = plt.subplots()
        sns.lineplot(data, x='area', y='합계', ax=ax, hue='case')
        ax.set_xlabel('PV 면적 [m²]')
        ax.set_ylabel('연간 발전량 [kWh]')
        plt.show()

    data = (
        data
        .filter(pl.col('area').is_in([0, 100]))
        .rename({'합계': 'value'})
        .select('case', 'area', 'value')
        .unique()
        .with_columns(pl.format('a{}', pl.col('area').cast(pl.Int32)).alias('area'))
        .pivot('area', index='case', values='value')
        .sort('case')
        .with_columns((pl.col('a100') - pl.col('a0')).truediv(100).alias('per area'))
    )

    rich.print(data)


@app.command
def pv_equation(path: Path):
    """목표 자립률로부터 필요 PV 면적 계산식 검토."""

    @utils.pl.frame_cache(path / 'cache.parquet')
    def read():
        return pl.concat(
            (report)
            .CalculationsReport(x)
            .data.select(pl.lit(x.stem).alias('case'), pl.all())
            for x in path.glob('*.xls')
            if '계산결과' in x.name
        )

    data = read().lazy().collect()

    var = pl.col('변수')
    data = (
        data
        .filter(
            var.str.contains('생산량')
            | var.str.starts_with('사용면적')
            | (var.is_in(['전력 소요량', '단위면적당 1차에너지 소요량']))
        )
        .drop(cs.ends_with('월'))
        .pivot(on='case', index=['index', '구분', '변수'], values='합계')
    )

    data.write_excel(path / 'PV 검토.xlsx')


@app.command
def pv_eir(path: Path):
    """목표 자립률 계산 결과/안전률 검토."""
    pq = path.with_suffix('.parquet')
    if pq.exists():
        data = pl.read_parquet(pq)
    else:

        def it():
            for p in path.glob('*결과그래프.xls'):
                yield (
                    report
                    .GraphReport(p)
                    .data.filter(pl.col('variable') == '에너지자립률(전체)')
                    .select(pl.lit(p.stem).alias('case'), pl.all())
                )

        data = pl.concat(it()).with_columns(
            pl.col('case').str.extract(r'\-(Base|ZEB.)\-').alias('grade')
        )
        data.write_parquet(pq)

    pl.Config.set_tbl_cols(20)
    rich.print(
        utils.pl
        .PolarsSummary(data.select('grade', 'value'), group='grade')
        .describe()
        .drop('variable')
    )


@app.command
def pv_insufficient(path: Path):
    """BIPV 적용 전 PV 요구면적 대비 최대면적 분석."""
    data = (
        pl
        .scan_parquet(path)
        .with_columns(
            pl.col('case').str.extract(r'\-(A\d)\-').alias('scale'),
            (pl.col('요구PV면적') <= pl.col('최대면적')).alias('충족'),
        )
        .with_columns(
            (pl.col('요구PV면적') / pl.col('최대면적')).alias('최대면적 대비 요구면적')
        )
        .collect()
    )

    utils.pl.PolarsSummary(data, group=['region', 'scale']).write_excel(
        path.parent / '요구PV-지역-규모.xlsx'
    )
    utils.pl.PolarsSummary(data, group='scale').write_excel(
        path.parent / '요구PV-규모.xlsx'
    )

    fig = Figure()
    ax = fig.subplots()
    sns.violinplot(
        data, x='최대면적 대비 요구면적', y='scale', hue='충족', split=True, ax=ax
    )
    fig.savefig(path.parent / '요구PV.png')


@dc.dataclass
class _Eco2Xml(Eco2Xml):
    source: Path | None = None

    MANUAL_CORRECTION: ClassVar[dict[str, str]] = {'142.841.7416': '142841.7416'}

    @classmethod
    def read(cls, src: str | Path, encoding: str = 'UTF-8'):
        instance = super().read(src=src, encoding=encoding)
        instance.source = Path(src)
        return instance

    @functools.cached_property
    def area(self):
        """
        불규칙한 연면적 수정.

        e.g.
          - "42 // 24" -> 42
          - "42(용24)" -> 42
        """
        area = super().area

        if area.floor is not None:
            return area

        raw = area.raw['floor']
        assert raw is not None
        head = (
            raw
            .replace('(', ' ')
            .replace('/', ' ')
            .replace(',', '')
            .split(' ', maxsplit=1)[0]
        )
        value = float(self.MANUAL_CORRECTION.get(head, head))
        case = None if self.source is None else self.source.stem
        logger.info(f'연면적 수정 {case=} | {raw=} | {value=}')

        return Area(site=area.site, building=area.building, floor=value, raw=area.raw)

    def pv_area(self, name: str):
        pv = mi.only(
            x for x in self.ds.iterfind('tbl_new') if x.findtext('설명') == name
        )
        if pv is None:
            return 0.0

        area = pv.findtext('태양광모듈면적')
        assert area, pv
        return float(area)

    @functools.cached_property
    def zone_count(self):
        data = {}
        for zone in self.ds.iterfind('tbl_zone'):
            if (code := zone.findtext('code', 'NOT FOUND')) in data:
                logger.warning('Duplicated zone code: {}', code)

            if not (raw := zone.findtext('입력존의수')):
                logger.warning(f'{code=} | count not found | element={_dict(zone)}')
                count = 0
            else:
                count = int(raw)

            data[code] = count

        return data

    def _envelope_area(self, kind: str):
        d = {'남', '남동', '남서', '동', '서', '북동', '북서', '북'}
        for face in self.ds.iterfind('tbl_myoun'):
            if face.findtext('건축부위방식') != kind:
                continue
            if face.findtext('방위') not in d:
                continue

            if not (area := face.findtext('건축부위면적')):
                logger.warning(f'{area=} | element={_dict(face)}')
                continue

            zone = face.findtext('존분류', 'NOT FOUND')
            if not (count := self.zone_count.get(zone, 0)):  # 참조되지 않는 면은 0 처리
                logger.warning(f'{count=} not found | element={_dict(face)}')
                continue

            yield float(area or 0) * count

    def envelope_area(self, kind: Literal['외벽', '외부창']):
        return sum(self._envelope_area(kind))


@app.command
@dc.dataclass
class Gather:
    """연산 완료 케이스 자립률/등급용 소요량 검토, 오류 케이스 정리."""

    src: Path
    dst: Path | None = None

    _: dc.KW_ONLY

    drop_monthly: bool = True
    plot: bool = True

    copy_eir_error: bool = False
    """자립률 오류 케이스 복사."""

    eir_threshold: float = 1.0
    """자립률 초과 오류 임계치."""

    EIR: ClassVar[dict[str, float]] = {
        'Base': 13,
        'ZEB5': 20,
        'ZEB4': 40,
        'ZEB3': 60,
        'ZEB2': 80,
        'ZEB1': 100,
        'ZEB+': 120,
    }
    CALC_RENAME: ClassVar[dict[str, str]] = {
        '구분': 'group',
        '변수': 'variable',
        '합계': 'value',
        '단위': 'unit',
    }

    @property
    def output(self):
        return self.dst or self.src.parent

    def tpls(self):
        return tuple(x for x in self.src.glob('*') if x.suffix in {'.tpl', '.tplx'})

    @staticmethod
    def _xml_data(path: Path):
        xml = _Eco2Xml.read(path)

        assert xml.area.floor is not None
        yield '대지면적', xml.area.site
        yield '건축면적', xml.area.building
        yield '연면적', xml.area.floor

        yield 'PV면적', xml.pv_area('PV')
        yield 'BIPV면적', xml.pv_area('BIPV')

        yield '외벽면적', xml.envelope_area('외벽')
        yield '외부창면적', xml.envelope_area('외부창')

    def __iter__(self):
        graph_group = (
            pl
            .format(
                '그래프-{}-{}',
                pl.col('category').fill_null(''),
                pl.col('energy').fill_null(''),
            )
            .str.strip_chars('-')
            .alias('group')
        )

        for tpl in tqdm(self.tpls()):
            stem = tpl.stem

            try:
                xml_data = tuple(self._xml_data(tpl))
            except ValueError as e:
                e.add_note(str(tpl))
                raise

            xml = pl.DataFrame(xml_data, schema=['variable', 'value'], orient='row')
            calc = (
                (report)
                .CalculationsReport(self.src / f'{stem} 계산결과.xls')
                .data.rename(self.CALC_RENAME)
            )
            graph = (
                (report)
                .GraphReport(self.src / f'{stem} 결과그래프.xls')
                .data.select(graph_group, 'variable', 'value', 'unit')
            )

            if self.drop_monthly:
                calc = calc.drop(cs.ends_with('월'))

            yield (
                pl
                .concat([xml, calc, graph], how='diagonal')
                .select(pl.lit(tpl.name).alias('case'), pl.all())
                .with_columns(pl.col('unit').str.strip_chars('[]'))
            )

    def _read(self):
        return (
            pl
            .concat(self)
            .select(
                'case',
                pl
                .col('case')
                .str.extract_groups(
                    r'^(?<ownership>공|민)'
                    r'(?<drop>-(?<scale1>C\d))?-(?<scale2>A\d)-'
                    r'(?<use>\w)-(?<bldg>\d+)-'
                    r'(?<region>\w+)-(?<grade>[\w+]+)-(?<pv>\w+)-'
                )
                .alias('struct'),
                pl.all().exclude('case'),
            )
            .unnest('struct')
            .drop('drop')
            .insert_column(
                2,
                pl
                .when(pl.col('scale1').is_null())
                .then(pl.col('scale2'))
                .otherwise(pl.col('scale1'))
                .alias('scale'),
            )
            .with_columns(pl.col('bldg').cast(pl.UInt8))
        )

    @staticmethod
    def _plot(data: pl.DataFrame, xlabel: str | None = None):
        fig = Figure(figsize=(32 / 2.54, 18 / 2.54))
        axes = fig.subplots(2, 4)

        ax: Axes
        for ax, grade in zip(
            axes.ravel(),
            ['ZEB+', 'ZEB1', 'ZEB2', 'ZEB3', 'ZEB4', 'ZEB5', 'Base', None],
            strict=True,
        ):
            if grade is None:
                ax.set_axis_off()
                continue

            values = data.filter(pl.col('grade') == grade)['value'].to_numpy()
            sns.violinplot(x=values, ax=ax, fill=False, split=True)
            sns.stripplot(x=values, ax=ax, alpha=0.25, color='slategray')
            ax.set_title(grade, loc='left', weight=500)
            if xlabel:
                ax.set_xlabel(xlabel)

        return fig

    def _copy_eir_error(self, data: pl.DataFrame):
        cases = (
            data
            .filter(pl.col('variable') == '에너지자립률(전체)')
            .with_columns(
                error=pl.col('value') - pl.col('grade').replace_strict(self.EIR)
            )
            .filter((pl.col('error') < 0) | (pl.col('error') > self.eir_threshold))
        )

        if not cases.height:
            return

        output = self.output / 'error'
        output.mkdir(exist_ok=True)

        for row in tqdm(
            cases.select('case', 'error').sort('case').iter_rows(), total=cases.height
        ):
            src: Path = self.src / row[0]
            dst = output / f'{src.stem}-error={row[1]:+.3f}{src.suffix}'

            try:
                shutil.copy2(src, dst)
            except FileNotFoundError as e:
                e.add_note(str(src))
                raise

    def __call__(self):
        name = self.src.name
        cache = self.output / f'{name}.parquet'

        if cache.exists():
            data = pl.read_parquet(cache)
        else:
            data = self._read()

            data.write_parquet(cache)
            data.head(1000).write_excel(cache.with_suffix('.xlsx'), column_widths=100)

        if self.plot:
            group = pl.col('group')
            for var, xlabel in [
                ['에너지자립률(전체)', '에너지 자립률 [%]'],
                ['등급용1차소요량', '등급용 1차 소요량 [kWh/m²yr]'],
            ]:
                d = data.filter(
                    pl.col('variable') == var,
                    group.str.starts_with('그래프'),
                    (group == '그래프-기타') | group.str.ends_with('합계'),
                )
                self._plot(d, xlabel).savefig(self.output / f'{name}-{var}.png')

        if self.copy_eir_error:
            self._copy_eir_error(data)

        return data


if __name__ == '__main__':
    utils.terminal.LogHandler.set()
    utils.mpl.MplTheme().grid().apply({'lines.solid_capstyle': 'butt'})
    app()
