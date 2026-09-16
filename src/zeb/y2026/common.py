import dataclasses as dc
import enum
import re
from typing import ClassVar, Literal

Use = Literal['non-res', 'res']
Region = Literal['중부1', '중부2', '남부', '제주']

USES: tuple[Use, Use] = ('non-res', 'res')
REGIONS: tuple[Region, Region, Region, Region] = ('중부1', '중부2', '남부', '제주')


class INDEX:
    BUILDING = (
        'bldg',
        'use',
        'owner',
        'scale.c',
        'scale.a',
        'purpose',
        'index',
        'region',
        '대지면적',
        '연면적',
        '건축면적',
    )
    CASE = (*BUILDING, 'grade')


class Grade(enum.StrEnum):
    EXST = 'Existing'
    NOPV = 'NOPV'  # baseline 케이스에서 PV 제거
    BASE = 'Base'
    SUB5 = 'Sub5'
    ZEB5 = 'ZEB5'
    ZEB4 = 'ZEB4'
    ZEB3 = 'ZEB3'
    ZEB2 = 'ZEB2'
    ZEB1 = 'ZEB1'
    ZEBP = 'ZEB+'


@dc.dataclass
class Case:
    owner: str
    purpose: str
    index: int
    region: Region
    grade: Grade
    scale_a: str
    scale_c: str | None = None

    PATTERN: ClassVar[str] = (
        r'^(?P<owner>\w)(?:-(?P<scale_c>C\d))?-(?P<scale_a>A\d)'
        r'-(?P<purpose>\w)-(?P<index>\d+)-(?P<region>중부[12]|남부|제주)'
        r'-(?P<grade>ZEB[+1-5]|Existing|Base|NOPV)'
    )

    @classmethod
    def search(cls, s: str, /):
        if not (m := re.search(cls.PATTERN, s)):
            raise ValueError(s)

        g = m.groupdict()
        g['index'] = int(g['index'])

        return cls(**g)  # ty:ignore[invalid-argument-type]

    @property
    def subdir(self):
        return f'{self.grade}_{self.region}'
