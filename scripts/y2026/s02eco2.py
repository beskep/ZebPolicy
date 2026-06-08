import dataclasses as dc
import functools
import itertools
import re
import shutil
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING

import eco2
import structlog
from cyclopts.config import Toml
from eco2 import editor

from zeb.utils import tqdm
from zeb.utils.cli import App
from zeb.y2026.common import Grade, Use, Uses
from zeb.y2026.config import Paths  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Iterable

app = App(
    config=[
        Toml('env.toml', root_keys=['2026', 'paths'], use_commands_as_keys=False),
        Toml('env.toml', root_keys=['2026', 'eco2']),
    ]
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

    residential: Path
    non_residential: Path
    paths: Paths

    p_grade: re.Pattern = dc.field(init=False)
    p_region: re.Pattern = dc.field(init=False)

    def __post_init__(self):
        self.p_grade = re.compile(r'((ZEB[+1-5])|Base|Existing|NOPV)')
        self.p_region = re.compile(r'(중부[12]|남부|제주)')

    @functools.cached_property
    def dst(self):
        return self.paths.eco2

    def case(self, name: str):
        if not (g := self.p_grade.search(name)):
            raise ValueError(name)
        if not (r := self.p_region.search(name)):
            raise ValueError(name)

        # grade_region
        return f'{g.group()}_{r.group()}'

    def copy(self, src: Path, use: Use):
        dst = self.dst / use / self.case(src.name) / f'{src.stem}.tpl'

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

            d = self.dst / use / self.case(stem) / f'{stem}.tpl'

            eco = editor.Eco2Editor(s)
            _remove_renewable(eco.xml, pv_only=True)
            eco.write(d, dsr=False)

    def copy_batch(self, use: Use, /):
        logger.info(use)

        match use:
            case 'non-res':
                root = self.non_residential
            case 'res':
                root = self.residential

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


if __name__ == '__main__':
    app()
