"""2025년 ECO2 등급별 수정 코드 재현 (전전화 분석용)."""

import functools
import itertools
import math
import re
from dataclasses import KW_ONLY, dataclass, field
from pathlib import Path  # ruff: ignore[typing-only-standard-library-import]
from typing import TYPE_CHECKING, ClassVar, Literal

import eco2.report
import more_itertools as mi
import polars as pl
import structlog
from eco2.editor import Eco2Editor, EditorError, set_child_text
from lxml import etree
from tqdm.rich import tqdm

from zeb.y2026.common import Grade

if TYPE_CHECKING:
    from lxml.etree import _Element

Region = Literal['중부1', '중부2', '남부', '제주']
PVArea = Literal['zero', 'required']
Use = Literal['residential', 'non-residential']

HeatingType = Literal['EHP', 'GHP', '보일러', '지역난방']
CoolingType = Literal['EHP', 'GHP', '흡수식']
EnergySource = Literal['전기', 'LNG', 'LPG', '난방유', '지역난방']

EIR: dict[Grade, float | None] = {
    Grade.BASE: 0.13,
    Grade.ZEB5: 0.2,
    Grade.ZEB4: 0.4,
    Grade.ZEB3: 0.6,
    Grade.ZEB2: 0.8,
    Grade.ZEB1: 1.0,
    Grade.ZEBP: 1.2,
}
PV = """
<tbl_new>
    <code>0</code>
    <설명>PV</설명>
    <기기종류>태양광</기기종류>
    <가동연료>(없음)</가동연료>
    <태양열종류>(없음)</태양열종류>
    <집열기유형>(없음)</집열기유형>
    <집열판면적 />
    <집열판방위>(없음)</집열판방위>
    <솔라펌프의정격출력 />
    <태양열시스템의성능>(없음)</태양열시스템의성능>
    <무손실효율계수 />
    <열손실계수1차 />
    <열손실계수2차 />
    <축열탱크체적급탕 />
    <축열탱크체적난방 />
    <축열탱크설치장소>(없음)</축열탱크설치장소>
    <태양광모듈면적>9999</태양광모듈면적>
    <태양광모듈기울기>수평</태양광모듈기울기>
    <태양광모듈방위>(없음)</태양광모듈방위>
    <태양광모듈종류>성능치 입력</태양광모듈종류>
    <태양광모듈적용타입>후면통풍형</태양광모듈적용타입>
    <지열히트펌프용량 />
    <열성능비난방 />
    <열성능비냉방 />
    <펌프용량1차 />
    <펌프용량2차 />
    <열교환기설치여부>아니오</열교환기설치여부>
    <팽창탱크설치여부>아니오</팽창탱크설치여부>
    <팽창탱크체적 />
    <열생산능력 />
    <열생산효율 />
    <발전효율 />
    <태양광모듈효율>0.2</태양광모듈효율>
    <지열비고 />
    <열병합신재생여부>false</열병합신재생여부>
    <태양광용량>0</태양광용량>
</tbl_new>
"""
PV_GEN_PER_AREA: dict[str, dict[Region, float]] = {
    # kWh/m²
    'PV': {
        '중부1': 201.983736,
        '중부2': 197.503394,
        '남부': 224.25875,
        '제주': 209.032917,
    },
    'BIPV': {
        '중부1': 89.046987,
        '중부2': 97.839892,
        '남부': 115.092109,
        '제주': 80.210067,
    },
}

logger = structlog.stdlib.get_logger()


def _dict(obj: _Element, /) -> dict[str, str | None]:
    return {str(e.tag): e.text for e in obj.iterchildren()}


def _filter(variable: str, /, value):
    expr = pl.col(variable)
    return expr.is_null() | (expr == value)


@dataclass(frozen=True)
class _HeatingSystem:
    type: HeatingType
    source: EnergySource
    boiler_capacity: float

    @classmethod
    def create(cls, element: _Element):
        sources: dict[str | None, EnergySource] = {
            '전기': '전기',
            '천연가스': 'LNG',
            '액화가스': 'LPG',
            '난방유': '난방유',
        }
        data = tuple(element.findtext(x) for x in ['열생산기기방식', '히트연료'])
        args: tuple[HeatingType, EnergySource]

        match data:
            case '히트펌프', '전기':
                args = ('EHP', '전기')
            case '히트펌프', s:
                args = ('GHP', sources[s])
            case '전기보일러', _:
                args = ('보일러', '전기')
            case '보일러', _:
                args = ('보일러', sources[element.findtext('사용연료')])
            case '지역난방', _:
                args = ('지역난방', '지역난방')
            case _:
                msg = 'Unknown heating system'
                raise EditorError(msg, _dict(element))

        boiler_capacity = (
            float(element.findtext('보일러정격출력', 0)) if args[0] == '보일러' else 0
        )

        return cls(*args, boiler_capacity=boiler_capacity)


@dataclass(frozen=True)
class _CoolingSystem:
    type: CoolingType
    source: EnergySource

    @classmethod
    def create(cls, element: _Element):
        source: dict[str | None, EnergySource] = {
            '전기': '전기',
            '천연가스': 'LNG',
            '액화가스': 'LPG',
        }
        data = tuple(element.findtext(x) for x in ['냉동기방식', '열생산연결방식'])
        args: tuple[CoolingType, EnergySource]

        match data:
            case '압축식', _:
                args = ('EHP', '전기')
            case '압축식(LNG)', _:
                args = ('GHP', 'LNG')
            case '흡수식', '외부연결':
                args = ('흡수식', '지역난방')
            case '흡수식', '직화식':
                args = ('흡수식', source[element.findtext('사용연료')])
            case _:
                msg = 'Unknown cooling system'
                raise EditorError(msg, _dict(element))

        return cls(*args)


@dataclass
class _Case:
    src: Path
    scale: str
    region: Region
    grade: Grade

    def __str__(self):
        return f'{self.src.stem}-{self.region}-{self.grade}'


class _Editor(Eco2Editor):
    REGION: ClassVar[dict[Region, str]] = {
        '중부1': '춘천',
        '중부2': '서울',
        '남부': '부산',
        '제주': '제주',
    }
    REGION_CODE: ClassVar[dict[str, str]] = {
        '춘천': '101300',
        '서울': '010100',
        '부산': '020100',
        '제주': '170100',
    }

    def __init__(
        self,
        case: _Case,
        setting: pl.DataFrame,
        use: Use,
        pv: float = 0,
        bipv: float = 0,
    ):
        if pv == 'required':
            msg = f'{pv=}'
            raise EditorError(msg)

        super().__init__(case.src)
        self.case = case
        self.setting = setting
        self.use: Use = use
        self.pv: float = pv
        self.bipv: float = bipv

        self.category = pl.col('category')
        self.part = pl.col('part')
        self.source = pl.col('source')

    @functools.cached_property
    def weather_group(self):
        """{name: code}."""
        return {
            e.findtext('name'): e.findtext('code')
            for e in self.xml.iterfind('weather_group')
        }

    def _value(self, expr: pl.Expr):
        try:
            return self.setting.row(by_predicate=expr)[-1]
        except pl.exceptions.TooManyRowsReturnedError as e:
            raise EditorError(self.setting) from e

    def connected_renewable(self, equipment: _Element):
        code = equipment.findtext('연결된시스템')
        assert code is not None

        if code == '0':
            return None

        return mi.one(
            e for e in self.xml.ds.iterfind('tbl_new') if e.findtext('code') == code
        )

    def edit(self):
        self.edit_region()
        self.edit_wall_and_window()
        self.edit_heating_equipment()
        self.edit_cooling_equipment()
        self.edit_heat_recovery_rate()
        self.edit_lighting_load()
        self.edit_pv()

        return self

    def edit_region(self):
        region = self.REGION[self.case.region]
        if (code := self.weather_group[region]) is None:
            raise EditorError(region)

        if code != self.REGION_CODE[region]:
            logger.warning(
                '%s code 불일치 %s != %s', region, code, self.REGION_CODE[region]
            )

        desc = mi.one(self.xml.iterfind('tbl_Desc'))
        set_child_text(desc, 'buildarea', code)

    def edit_wall_and_window(self):
        uvalue = self.category == '열관류율'

        # 벽
        for surface in [
            '외벽(벽체)',
            '외벽(지붕)',
            '외벽(바닥)',
            '내벽(벽체)',
            '내벽(지붕)',
            '내벽(바닥)',
        ]:
            value = float(self._value(uvalue & (self.part == surface)))
            self.xml.set_walls(uvalue=value, surface_type=surface)

        # 창
        window_uvalue = float(self._value(uvalue & (self.part == '외부창')))
        shgc = float(self._value((self.category == 'SHGC') & (self.part == '외부창')))
        self.xml.set_windows(uvalue=window_uvalue, shgc=shgc)

    def _edit_heating_equipment(
        self,
        element: _Element,
        boiler_control_threshold: float | None = None,
    ):
        heating = _HeatingSystem.create(element)
        boiler_control_threshold = (
            boiler_control_threshold  # fmt
            or (100 if self.use == 'non-res' else math.inf)
        )

        # 효율
        part = (
            '전기보일러'
            if (heating.type == '보일러' and heating.source == '전기')
            else heating.type
        )

        try:
            value = float(
                self._value(
                    (self.category == '난방효율')
                    & (self.part == part)
                    & (self.source.is_null() | (self.source == heating.source))
                )
            )
        except pl.exceptions.RowsError as e:
            raise EditorError(heating) from e

        match heating.type:
            case 'EHP' | 'GHP':
                set_child_text(element, '히트난방정격7', str(value))
                set_child_text(element, '히트난방정격10', str(round(value * 0.42, 3)))
            case '보일러' | '지역난방':
                set_child_text(element, '정격보일러효율', str(value * 100))
            case _:
                raise EditorError(heating)

        # 펌프제어
        if (
            heating.type == '지역난방'  # fmt
            or (heating.boiler_capacity >= boiler_control_threshold)
        ):
            control = self._value(
                (self.category == '난방제어') & (self.part == '펌프제어')
            )
            set_child_text(element, '펌프제어유형', control)

    def edit_heating_equipment(self):
        for element in self.xml.iterfind('tbl_nanbangkiki'):
            if element.findtext('code') == '0' and element.findtext('설명') == '(없음)':
                continue

            renewable = self.connected_renewable(element)
            if renewable is not None and renewable.findtext('기기종류') == '지열':
                continue

            self._edit_heating_equipment(element)

    def _edit_cooling_equipment(self, element: _Element):
        cooling = _CoolingSystem.create(element)

        # 효율
        try:
            value = self._value(
                (self.category == '냉방효율')
                & (self.part == cooling.type)
                & (self.source.is_null() | (self.source == cooling.source))
            )
        except pl.exceptions.RowsError as e:
            raise EditorError(cooling) from e

        set_child_text(element, '열성능비', value)

        # 제어
        match cooling.type:
            case 'EHP' | 'GHP':
                if (kind := element.findtext('냉동기종류')) != '실내공조시스템':
                    logger.warning('냉동기종류="%s"', kind)
                    return

                control = self._value(
                    (self.category == '냉방제어') & (self.part == 'HP')
                )
                set_child_text(element, '제어방식', control)
            case '흡수식':
                control = self._value(
                    (self.category == '냉방제어') & (self.part == '흡수식')
                )

                code = element.findtext('code')
                assert code is not None

                for dist in self.xml.ds.iterfind('tbl_bunbae'):
                    if dist.findtext('냉동기') == code:
                        set_child_text(dist, '펌프운전제어유무', control)

    def edit_cooling_equipment(self):
        for element in self.xml.iterfind('tbl_nangbangkiki'):
            if element.findtext('code') == '0' and element.findtext('설명') == '(없음)':
                continue

            renewable = self.connected_renewable(element)
            if renewable is not None and renewable.findtext('기기종류') == '지열':
                continue

            self._edit_cooling_equipment(element)

    def edit_heat_recovery_rate(self):
        recovery = self.category == '열회수율'
        heating = self._value(recovery & (self.part == '난방'))
        cooling = self._value(recovery & (self.part == '냉방'))

        for element in self.xml.iterfind('tbl_kongjo'):
            if element.findtext('code') == '0' and element.findtext('설명') == '(없음)':
                continue
            if element.findtext('열교환기유형') != '전열교환':
                continue

            set_child_text(element, '열회수율', heating)
            set_child_text(element, '열회수율냉', cooling)

    def edit_lighting_load(self):
        value = self._value(
            (self.category == '전기') & (self.part == '평균조명에너지부하율')
        )
        self.xml.set_elements('tbl_zone/조명에너지부하율입력치', str(value))

    @functools.cached_property
    def max_pv_area(self):
        if self.xml.area.building is None:
            msg = '건축면적 해석 불가'
            raise EditorError(msg)

        ratio = float(self._value(self.part == '최대 태양광 모듈면적비'))
        return round(self.xml.area.building * ratio, 2)

    def _sort_renewable(self):
        for idx, element in enumerate(self.xml.ds.iterfind('tbl_new')):
            prev = element.findtext('code')
            assert prev is not None

            next_ = '0' if idx == 0 else f'{idx:04d}'
            set_child_text(element, 'code', next_)

            yield prev, next_

    def edit_pv(self):
        # 기존 PV 삭제
        for element in self.xml.iterfind('tbl_new'):
            if element.findtext('기기종류') == '태양광':
                self.xml.ds.remove(element)

        if not (self.pv or self.bipv):
            return

        # PV
        if self.pv:
            pv = etree.fromstring(PV)
            set_child_text(pv, '태양광모듈면적', f'{self.pv:.3f}')
            set_child_text(pv, '태양광모듈효율', '0.2')

            last_renewable = mi.last(self.xml.ds.iterfind('tbl_new'))
            index = self.xml.ds.index(last_renewable) + 1

            self.xml.ds.insert(index, pv)

        # BIPV
        if self.bipv:
            bipv = etree.fromstring(PV)
            set_child_text(bipv, '설명', 'BIPV')
            set_child_text(bipv, '태양광모듈면적', f'{self.bipv:.3f}')
            set_child_text(bipv, '태양광모듈기울기', '수직')
            set_child_text(bipv, '태양광모듈방위', '남')
            set_child_text(bipv, '태양광모듈적용타입', '밀착형')
            set_child_text(bipv, '태양광모듈효율', '0.16')

            last_renewable = mi.last(self.xml.ds.iterfind('tbl_new'))
            index = self.xml.ds.index(last_renewable) + 1

            self.xml.ds.insert(index, bipv)

        # code 순서에 따라 정렬
        codes = dict(self._sort_renewable())

        # 냉난방기기 '연결된시스템' 새 code로 변경
        for equipment in mi.flatten([
            self.xml.iterfind('tbl_nanbangkiki'),
            self.xml.iterfind('tbl_nangbangkiki'),
        ]):
            prev = equipment.findtext('연결된시스템')
            assert prev is not None

            if prev == '0':
                continue

            set_child_text(equipment, '연결된시스템', codes[prev])


# interface ====================================================================


@dataclass
class _Paths:
    src: str = '01.src'
    pv_zero: str = '02.pv.zero'
    pv_required: str = '03.pv.required'

    required_pv: str = 'required-pv.parquet'


@dataclass
class _Arguments:
    root: Path
    paths: _Paths = field(default_factory=_Paths)
    use: Use = 'non-residential'

    @functools.cached_property
    def config(self):
        return (
            pl
            .scan_csv(f'data/2025/{self.use}.csv')
            .unpivot(
                index=['category', 'part', 'region', 'source', 'scale'],
                variable_name='grade',
            )
            .collect()
        )


@dataclass
class EditZeb(_Arguments):
    _: KW_ONLY

    pv: PVArea
    xml: bool = False
    precision: int = 2

    grades: tuple[Grade, ...] = (
        Grade.BASE,
        Grade.ZEB5,
        Grade.ZEB4,
        Grade.ZEB3,
        Grade.ZEB2,
        Grade.ZEB1,
        Grade.ZEBP,
    )

    _BASE_INDEX: ClassVar[int] = -99

    @functools.cached_property
    def output(self):
        subdir = self.paths.pv_zero if self.pv == 'zero' else self.paths.pv_required
        return self.root / subdir

    def filter_setting(self, scale: str, region: Region, grade: Grade):
        return self.config.filter(
            _filter('region', region),
            _filter('scale', scale),
            _filter('grade', grade),
        )

    @functools.cached_property
    def _required_pv(self):
        src = self.root / self.paths.required_pv
        if not src.exists():
            return None

        k = 10**self.precision
        return (
            pl
            .scan_parquet(src)
            .select(
                'file',
                'grade',
                (pl.col('요구PV면적') * k).ceil().truediv(k).alias('PV'),
            )
            .collect()
        )

    def _pv_area(self, case: _Case) -> float:
        if isinstance(self.pv, float | int):
            return self.pv

        match self.pv:
            case 'zero':
                return 0.0
            case 'required':
                if self._required_pv is None:
                    msg = '요구 PV 면적이 입력되지 않음.'
                    raise EditorError(msg)

                p = (
                    (pl.col('file').str.starts_with(str(case)))  # fmt
                    & (pl.col('grade') == case.grade)
                )
                row = self._required_pv.row(by_predicate=p, named=True)
                return float(row['PV'])
            case _:
                raise EditorError(self.pv)

    def cases(self):
        sources = list(self.root.joinpath(self.paths.src).glob('*'))
        sources = [x for x in sources if x.suffix.lower() in {'.tpl', '.tplx'}]

        p = re.compile(r'^\w(-(?P<s1>C\d))?\-(?P<s2>A\d)\-\w\-\d+')

        for src, region, grade in itertools.product(
            sources,
            ('중부1', '중부2', '남부', '제주'),
            self.grades,
        ):
            if (m := p.search(src.stem)) is None:
                raise EditorError(src.name)

            if (scale := m.group('s1') or m.group('s2')) is None:
                raise EditorError(src.name)

            yield _Case(src=src, scale=scale, region=region, grade=grade)

    def edit(self, case: _Case):
        setting = self.filter_setting(
            scale=case.scale, region=case.region, grade=case.grade
        )
        if not setting.height:
            raise EditorError(case)

        pv = self._pv_area(case)

        suffix = 'PvNotRequired' if pv < 0 else 'PV'
        output = self.output / case.grade / f'{case}-PV.{self.pv}-{suffix}.tpl'
        if output.exists():
            return

        editor = _Editor(case=case, setting=setting, use=self.use, pv=max(0, pv))

        try:
            editor.edit()
        except EditorError:
            logger.exception('Editor Error', case=case)
            return

        editor.write(output)

        if self.xml:
            editor.xml.write(output.with_suffix('.xml'))

    def __call__(self):
        self.output.mkdir(exist_ok=True)
        for g in self.grades:
            self.output.joinpath(g).mkdir(exist_ok=True)

        cases = tuple(self.cases())

        for c in tqdm(cases):
            logger.info(c)
            self.edit(c)


class ParseReport(_Arguments):
    @staticmethod
    def parse(d: Path):
        if not (src := list(d.glob('**/batchreport.tab'))):
            return None
        return pl.concat([eco2.report.BatchReport(s).data for s in src])

    def __call__(self):
        for it in (self.paths.pv_required, self.paths.pv_zero):
            path = self.root / it

            if (data := self.parse(path)) is None:
                continue

            data.write_parquet(path / 'batchreport.parquet')
            data.filter(pl.col('file') == pl.col('file').first()).write_csv(
                path / 'batchreport.sample.csv', include_bom=True
            )


class RequiredPV(_Arguments):
    safety: float = 0.001  # 안전률 (요구 자립률에 더함)

    @functools.cached_property
    def _barchreport(self):
        return (
            pl
            .scan_parquet(self.root / self.paths.pv_zero / 'batchreport.parquet')
            .with_columns(
                pl.col('value').str.replace_all(',', '').cast(pl.Float64, strict=False)
            )
            .collect()
        )

    def _max_pv_ratio(self):
        # 미사용
        d = self.config.filter(pl.col('part') == '최대 태양광 모듈면적비').to_dicts()
        return {x['grade']: float(x['value']) for x in d}

    def generation(self):
        # 총 면적당 1차 소요량, 생산량
        return (
            self._barchreport
            .with_columns(
                pl.col('variable').replace_strict(
                    {
                        '1차에너지소요량/합계': '면적당1차소요량',
                        '전기에너지 생산량(태양광)': 'PV전력생산량',
                        '전기에너지 생산량(풍력)': '기타전력생산량',
                        '전기에너지 생산량(열병합)': '기타전력생산량',
                        '단위면적당 생산량(태양열)': '면적당기타생산량',
                        '단위면적당 생산량(지열)': '면적당기타생산량',
                        '단위면적당 생산량(수열)': '면적당기타생산량',
                        '단위면적당 생산량(열병합)': '면적당기타생산량',
                    },
                    default=None,
                )
            )
            .drop_nulls('variable')
            .group_by('file', 'variable')
            .agg(pl.sum('value'))
            .pivot('variable', index='file', values='value', sort_columns=True)
        )

    def elec_area(self):
        # 전력 생산량 보정 면적
        # a' = (sum_i a_i) / (sum_i (E_i / a_i)), i: 용도
        return (
            self._barchreport
            .with_columns(
                pl
                .col('variable')
                .str.extract(r'^1차에너지소요량/(\w+)/전력$')
                .alias('delivered'),  # 용도별 전력 소요량 [kWh]
                pl
                .col('variable')
                .str.extract(r'^사용면적\((\w+)\)$')
                .alias('area'),  # 용도별 면적 [m²]
            )
            .drop('variable')
            .unpivot(
                ['delivered', 'area'],
                index=['file', 'value'],
                variable_name='variable',
                value_name='function',
            )
            .filter(pl.col('function') != 'unknown')
            .pivot('variable', index=['file', 'function'], values='value')
            .with_columns(
                (pl.col('delivered') / pl.col('area'))
                .clip(lower_bound=0)
                .alias('delivered_per_area')
            )
            .group_by('file')
            .agg(pl.sum('delivered', 'delivered_per_area'))
            .select(
                'file',
                pl
                .col('delivered')
                .truediv(pl.col('delivered_per_area'))
                .alias('보정면적'),
            )
        )

    def __call__(self):
        generation = self.generation()
        elec_area = self.elec_area()

        cases = (
            self._barchreport
            .unique('file')
            .select(
                'file',
                pl
                .col('file')
                .str.extract_groups(
                    r'(?<region>중부[12]|남부|제주)-(?<grade>Base|ZEB[1-5\+])'
                )
                .alias('group'),
            )
            .unnest('group')
        )

        data = (
            cases
            .join(generation, on='file', how='left', validate='1:1')
            .join(elec_area, on='file', how='left', validate='1:1')
            .with_columns(
                pl.col('grade').replace_strict(EIR).add(self.safety).alias('EIR')
            )
            .with_columns(
                (
                    pl.col('면적당1차소요량')
                    + 2.75
                    * (pl.col('PV전력생산량') + pl.col('기타전력생산량'))
                    / pl.col('보정면적')
                    + pl.col('면적당기타생산량')
                ).alias('면적당1차소요량')
            )
            .with_columns(
                (
                    pl.col('보정면적')
                    * (
                        pl.col('EIR') * pl.col('면적당1차소요량')
                        - pl.col('면적당기타생산량')
                    )
                    / 2.75
                    - pl.col('기타전력생산량')
                ).alias('요구PV생산량'),
                pl
                .col('region')
                .replace_strict(PV_GEN_PER_AREA['PV'], return_dtype=pl.Float64)
                .alias('면적당PV발전량'),
            )
            .with_columns(
                pl
                .col('요구PV생산량')
                .truediv(pl.col('면적당PV발전량'))
                .alias('요구PV면적')
            )
            .sort('file')
        )

        output = self.root / self.paths.required_pv
        data.write_parquet(output)
        data.write_csv(output.with_suffix('.csv'), include_bom=True)

        pl.Config.set_tbl_cols(20)
        return data
