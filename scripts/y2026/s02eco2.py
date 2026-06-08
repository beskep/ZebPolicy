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


def _remove_renewable(src: Path, *, pv_only: bool = False):
    eco = editor.Eco2Editor(src)

    for element in tuple(eco.xml.iterfind('tbl_new')):
        if element.findtext('code') == '0':
            continue

        if pv_only and element.findtext('기기종류') != '태양광':
            continue

        eco.xml.ds.remove(element)

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

    grade_pattern: re.Pattern = dc.field(init=False)

    def __post_init__(self):
        self.grade_pattern = re.compile(r'((ZEB[+1-5])|Base|Existing)')

    @functools.cached_property
    def dst(self):
        return self.paths.eco2

    def grade(self, path: Path):
        if not (m := self.grade_pattern.search(path.name)):
            raise ValueError(path)

        return m.group()

    def copy(self, src: Path, use: Use):
        grade = self.grade(src)
        dst = self.dst / use / grade

        match src.suffix:
            case '.tpl':
                shutil.copy2(src, dst)
            case '.tplx':
                eco2.Eco2.read(src).write(dst / f'{src.stem}.tpl')
            case _:
                raise ValueError(src)

    def nopv(self, src: Iterable[Path], use: Use):
        dst = self.dst / use / Grade.NOPV

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
            d = dst / f'{stem}.tpl'

            eco = _remove_renewable(s, pv_only=True)
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
        for use, grade in itertools.product(Uses, Grade):
            if grade == Grade.SUB5:
                continue

            if grade != Grade.NOPV:
                continue

            self.dst.joinpath(use, grade).mkdir(parents=True, exist_ok=True)

        self.copy_batch('non-res')
        self.copy_batch('res')


if __name__ == '__main__':
    app()
