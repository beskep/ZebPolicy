import enum
from typing import Literal

Use = Literal['non-res', 'res']

Uses: tuple[Use, Use] = ('non-res', 'res')


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
