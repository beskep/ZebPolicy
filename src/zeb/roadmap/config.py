from __future__ import annotations

import tomllib
from collections import ChainMap
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec

if TYPE_CHECKING:
    from collections.abc import Mapping

StrPath = str | Path
Paths = StrPath | Iterable[StrPath]


def _read_toml(path: StrPath):
    return tomllib.loads(Path(path).read_text('UTF-8'))


class Config:
    @staticmethod
    def _dec_hook(t: type, obj):
        if t is Path:
            return Path(obj)
        return obj

    @classmethod
    def _read(
        cls,
        path: Paths = ('data/config.toml', '.config.toml'),
    ) -> Mapping[str, Any]:
        if isinstance(path, Iterable):
            return ChainMap(*(_read_toml(p) for p in path))

        return _read_toml(path)

    @classmethod
    def read(cls, path: Paths = ('data/config.toml', '.config.toml')):
        data = cls._read(path)
        return msgspec.convert(data, cls, dec_hook=cls._dec_hook)
