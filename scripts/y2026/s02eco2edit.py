import dataclasses as dc
import functools
import itertools
import math
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import cyclopts
import eco2
import polars as pl
import structlog
from cyclopts.config import Toml
from eco2 import editor
from eco2.editor import set_child_text

from zeb.utils import tqdm
from zeb.utils.cli import App
from zeb.y2026 import equipment as eq
from zeb.y2026.common import REGIONS, USES, Case, Grade, Use
from zeb.y2026.config import Paths  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Iterable

    from lxml.etree import _Element

app = App(
    config=Toml(
        'env.toml',
        root_keys=['2026', 'paths'],
        use_commands_as_keys=False,
    )
)
logger = structlog.stdlib.get_logger()


def _remove_renewable(src: str | Path | editor.Eco2Xml, *, pv_only: bool = False):
    eco = editor.Eco2Xml.read(src) if isinstance(src, str | Path) else src

    for element in tuple(eco.iterfind('tbl_new')):
        if element.findtext('code') == '0':
            continue

        if pv_only and element.findtext('기기종류') != '태양광':
            continue

        eco.ds.remove(element)

    return eco


@app.command
@dc.dataclass
class Copy:
    """
    2025년 계산 ECO2 파일 tpl 형식으로 복사.

    2026-06-05
    """

    paths: Paths

    @functools.cached_property
    def dst(self):
        return self.paths.eco2

    def copy(self, src: Path, use: Use):
        c = Case.search(src.name)
        dst = self.dst / use / c.subdir / f'{src.stem}.tpl'

        if dst.exists():
            return

        match src.suffix:
            case '.tpl':
                shutil.copy2(src, dst)
            case '.tplx':
                eco2.Eco2.read(src).write(dst)
            case _:
                raise ValueError(src)

    def nopv(self, src: Iterable[Path], use: Use):
        for s in tqdm(src, desc='no pv'):
            if Grade.BASE not in s.name:
                continue

            stem = (
                s.stem
                .removesuffix('-PV-required-PV')
                .removesuffix('-PV-required-BIPV')
                .removesuffix('-PV-required-PvNotRequired')
                .replace('Base', 'NOPV')
            )
            c = Case.search(stem)
            dst = self.dst / use / c.subdir / f'{stem}.tpl'

            eco = editor.Eco2Editor(s)
            _remove_renewable(eco.xml, pv_only=True)
            eco.write(dst, dsr=False)

    def copy_batch(self, use: Use, /):
        logger.info(use)
        root = self.paths.use(use)
        src = sorted(set(root.glob('*.tpl')) | set(root.glob('*.tplx')))

        for s in tqdm(src, desc='copy'):
            self.copy(s, use)

        self.nopv(src, use)

    def __call__(self):
        self.dst.mkdir(exist_ok=True)
        for use, grade, region in itertools.product(
            USES, Grade, ('중부1', '중부2', '남부', '제주')
        ):
            if grade in {Grade.SUB5, Grade.EXST}:
                continue

            dst = self.dst.joinpath(use, f'{grade}_{region}')
            dst.mkdir(parents=True, exist_ok=True)

        self.copy_batch('non-res')
        self.copy_batch('res')


@cyclopts.Parameter(name='*')
@dc.dataclass
class _Design:
    use: Use
    grade: str = 'existing(08)'

    @functools.cached_property
    def design(self):
        src = f'data/2026/{self.use.replace("res", "residential")}.csv'
        return pl.read_csv(src).select(
            'category', 'part', 'region', 'source', 'scale', self.grade
        )

    def value(self, expr: pl.Expr):
        return self.design.row(by_predicate=expr)[-1]


class _Expr:
    category = pl.col('category')
    part = pl.col('part')
    region = pl.col('region')
    source = pl.col('source')
    scale = pl.col('scale')


@dc.dataclass(frozen=True)
class _ExistingBldg(editor.Eco2Editor):
    use: Use
    case: Case
    design: _Design

    def edit(self):
        _remove_renewable(self.xml)
        self.edit_wall_and_window()
        self.edit_heating_equipment()
        self.edit_cooling_equipment()
        self.edit_heat_recovery_rate()
        self.edit_lighting_load()

        return self

    def edit_wall_and_window(self):
        region = _Expr.region == self.case.region

        # 벽
        for surface in (
            '외벽(벽체)',
            '외벽(지붕)',
            '외벽(바닥)',
            '내벽(벽체)',
            '내벽(지붕)',
            '내벽(바닥)',
        ):
            if next(self.xml.surfaces_by_type(surface), None) is None:
                continue

            uvalue = float(
                self.design.value(
                    region & (_Expr.category == '열관류율') & (_Expr.part == surface)
                )
            )
            self.xml.set_walls(uvalue=uvalue, surface_type=surface)

        # 창
        window = _Expr.part == '외부창'
        uvalue = float(
            self.design.value(region & window & (_Expr.category == '열관류율'))
        )
        shgc = float(self.design.value(region & window & (_Expr.category == 'SHGC')))
        self.xml.set_windows(uvalue=uvalue, shgc=shgc)

    def _edit_heating_equipment(
        self,
        element: _Element,
        boiler_control_threshold: float | None = None,
    ):
        heating = eq.HeatingSystem.create(element)
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
            eff = float(
                self.design.value(
                    (_Expr.category == '난방효율')
                    & (_Expr.part == part)
                    & (_Expr.source.is_null() | (_Expr.source == heating.source))
                    & (_Expr.scale.is_null() | (_Expr.scale == self.case.scale_a))
                )
            )
        except pl.exceptions.RowsError as e:
            raise editor.EditorError(heating) from e

        match heating.type:
            case 'EHP' | 'GHP':
                set_child_text(element, '히트난방정격7', f'{eff:.3f}')
                set_child_text(element, '히트난방정격10', f'{eff * 0.42:.3f}')
            case '보일러' | '지역난방':
                set_child_text(element, '정격보일러효율', f'{eff * 100:.3f}')
            case _:
                raise editor.EditorError(heating)

        # 펌프제어
        if (
            heating.type == '지역난방'  # fmt
            or (heating.boiler_capacity >= boiler_control_threshold)
        ):
            control = self.design.value(
                (_Expr.category == '난방제어') & (_Expr.part == '지역난방:펌프제어')
            )
            set_child_text(element, '펌프제어유형', control)

    def edit_heating_equipment(self):
        for element in self.xml.iterfind('tbl_nanbangkiki'):
            if element.findtext('code') == '0' and element.findtext('설명') == '(없음)':
                continue

            # 신재생 설비 삭제
            next(element.iterfind('연결된시스템')).text = None
            self._edit_heating_equipment(element)

    def _edit_cooling_equipment(self, element: _Element):
        cooling = eq.CoolingSystem.create(element)

        # 효율
        try:
            eff = self.design.value(
                (_Expr.category == '냉방효율')
                & (_Expr.part == cooling.type)
                & (_Expr.source.is_null() | (_Expr.source == cooling.source))
                & (_Expr.scale.is_null() | (_Expr.scale == self.case.scale_a))
            )
        except pl.exceptions.RowsError as e:
            raise editor.EditorError(cooling) from e

        set_child_text(element, '열성능비', eff)

        # 제어
        match cooling.type:
            case 'EHP' | 'GHP':
                if (kind := element.findtext('냉동기종류')) != '실내공조시스템':
                    logger.warning('냉동기종류="%s"', kind)
                    return

                control = self.design.value(
                    (_Expr.category == '냉방제어') & (_Expr.part == 'HP')
                )
                set_child_text(element, '제어방식', control)
            case '흡수식':
                control = self.design.value(
                    expr=(_Expr.category == '냉방제어') & (_Expr.part == '흡수식')
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

            # 신재생 설비 삭제
            next(element.iterfind('연결된시스템')).text = None
            self._edit_cooling_equipment(element)

    def edit_heat_recovery_rate(self):
        recovery = _Expr.category == '열회수율'
        heating = self.design.value(recovery & (_Expr.part == '난방'))
        cooling = self.design.value(recovery & (_Expr.part == '냉방'))

        for element in self.xml.iterfind('tbl_kongjo'):
            if element.findtext('code') == '0' and element.findtext('설명') == '(없음)':
                continue
            if element.findtext('열교환기유형') != '전열교환':
                continue

            set_child_text(element, '열회수율', heating)
            set_child_text(element, '열회수율냉', cooling)

    def edit_lighting_load(self):
        value = self.design.value(
            (_Expr.category == '전기') & (_Expr.part == '평균조명에너지부하율')
        )
        self.xml.set_elements('tbl_zone/조명에너지부하율입력치', value)


@app.command
@dc.dataclass
class GenExisting:
    """기존 건물 등급 파일 생성."""

    paths: Paths

    def gen(self, use: Use):
        root = self.paths.use(use)
        source = tuple(root.glob('*Base*.tpl*'))
        design = _Design(use)

        for src in tqdm(source, desc=use):
            logger.info(src.name)

            c = Case.search(src.name)
            c.grade = Grade.EXST
            stem = src.stem.replace('Base', Grade.EXST)

            dst = self.paths.eco2 / use / c.subdir / f'{stem}.tpl'
            _ExistingBldg(src, use=use, case=c, design=design).edit().write(dst)

    def __call__(self):
        for use, region in itertools.product(USES, REGIONS):
            d = self.paths.eco2 / use / f'{Grade.EXST}_{region}'
            d.mkdir(exist_ok=True)

        self.gen('non-res')
        self.gen('res')


if __name__ == '__main__':
    app()
