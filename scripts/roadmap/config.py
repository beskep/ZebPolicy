import tomllib
from collections.abc import Iterable
from pathlib import Path

import msgspec
import pydash as pyd
from more_itertools import always_iterable

StrPath = str | Path
Paths = StrPath | Iterable[StrPath]


def _read_toml(path: str | Path):
    return tomllib.loads(Path(path).read_text('UTF-8'))


class CertDirs(msgspec.Struct):
    region: Path
    prep: Path
    plot: Path


class Cnst(msgspec.Struct, frozen=True):
    YEAR2DAYS: int = 365

    USE: str = '건물용도'
    MAIN_USE: str = '건물주용도'
    CERT_DURATION: str = '예비인증소요일'

    POLICY_INTERVAL: str = '정책구간'
    AREA_INTERVAL: str = '면적구간'

    PERIOD: str = POLICY_INTERVAL
    SCALE: str = AREA_INTERVAL

    REGIONS: tuple[str, str, str, str] = ('중부1', '중부2', '남부', '제주')


class Certification(msgspec.Struct):
    root: Path
    source: Path
    dirs: CertDirs
    cnst: Cnst = Cnst()

    def __post_init__(self):
        if not self.source.exists():
            self.source = self.root / self.source

        for f in self.dirs.__struct_fields__:
            v: Path = self.root / getattr(self.dirs, f)
            setattr(self.dirs, f, v)


class IntervalEdaVariable(msgspec.Struct):
    index: tuple[str, ...]
    misc: tuple[str, ...]
    area: tuple[str, ...]
    transmittance: tuple[str, ...]
    wwr: tuple[str, ...]
    profile: tuple[str, ...]
    mechanical: tuple[str, ...]
    electrical: tuple[str, ...]
    renewable: tuple[str, ...]

    def iter(self) -> Iterable[tuple[str, str]]:
        for field in self.__struct_fields__:
            for column in getattr(self, field):
                yield field, column


class IntervalEdaEquipment(msgspec.Struct):
    heating: str
    cooling: str


class IntervalEDA(msgspec.Struct):
    root: Path
    variable: IntervalEdaVariable
    equipment: tuple[IntervalEdaEquipment, ...]  # 대표 모델 냉온열원설비

    def iter_var(self) -> Iterable[tuple[str, str]]:
        yield from self.variable.iter()

    def iter_equipment(self):
        for eq in self.equipment:
            yield {'온열원설비 분류': eq.heating, '냉열원설비 분류': eq.cooling}


class Sensitivity(msgspec.Struct):
    root: Path


class Energy(msgspec.Struct):
    root: Path
    cpr: list[list[str]]


class Config(msgspec.Struct):
    root: Path

    certification: Certification
    interval_eda: IntervalEDA
    sensitivity: Sensitivity
    energy: Energy

    @staticmethod
    def _dec_hook(t: type, obj):
        if t is Path:
            return Path(obj)
        return obj

    @classmethod
    def read(cls, path: Paths = ('data/config.toml', '.config.toml')):
        # 먼저 입력한 path 우선
        data = pyd.merge(*(_read_toml(p) for p in always_iterable(path)))
        root = Path(data['root'])

        def _kv():
            for key, value in data.items():
                if isinstance(value, dict) and 'root' in value:
                    v = value | {'root': root / value['root']}
                else:
                    v = value

                yield key, v

        return msgspec.convert(dict(_kv()), type=cls, dec_hook=cls._dec_hook)


if __name__ == '__main__':
    import rich

    rich.print(Config.read())
