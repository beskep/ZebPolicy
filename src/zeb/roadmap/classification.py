from __future__ import annotations

import tomllib
from datetime import date  # noqa: TC003
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import msgspec
import polars as pl

if TYPE_CHECKING:
    from collections.abc import Sequence


type LocalGovDict = dict[str, list[str]]  # {광역자치단체: [기초자치단체, ...]}
type RegionDict = dict[str, LocalGovDict]
type RegionAbbrev = dict[str, str]


class Breaks(msgspec.Struct):
    period: list[date]
    area: list[float]

    @classmethod
    def read(cls, path: str | Path = 'data/breaks.toml'):
        return msgspec.toml.decode(Path(path).read_bytes(), type=cls)

    def cut[T: (pl.Expr, pl.Series)](
        self,
        obj: T,
        breaks: Literal['period', 'area'],
        *,
        left_closed=True,
        include_breaks=False,
    ) -> T:
        if breaks == 'period':
            breaks_ = pl.Series('breaks', self.period).dt.epoch('d').to_list()
            labels = [f'P{i}' for i in range(len(breaks_) + 1)]
            obj = obj.dt.epoch('d')
        else:
            breaks_ = self.area
            labels = [f'A{i + 1}' for i in range(len(breaks_) + 1)]

        return obj.cut(
            breaks=breaks_,
            labels=labels,
            left_closed=left_closed,
            include_breaks=include_breaks,
        )


def _pattern(data: Sequence[str]):
    if not data:
        return '.^'  # never match

    s = '|'.join(data)
    return f'.*({s}).*'


class Region:
    def __init__(
        self,
        region: str | Path | RegionDict = 'data/region.json',
        abbrev: str | Path | RegionAbbrev | None = 'data/region_abbrev.json',
        replace: str | Path | dict[str, str] | None = 'data/region_replace.toml',
    ) -> None:
        self._region: RegionDict = (
            msgspec.json.decode(Path(region).read_bytes(), type=RegionDict)
            if isinstance(region, str | Path)
            else region
        )

        match abbrev:
            case None:
                abbrev_ = None
            case dict():
                abbrev_ = abbrev
            case str() | Path():
                abbrev_ = msgspec.json.decode(
                    Path(abbrev).read_bytes(), type=RegionAbbrev
                )

        self._abbrev = abbrev_

        if abbrev_ is not None:
            self._region = {
                k: self._abbrev_region(v, abbrev_) for k, v in self._region.items()
            }

        match replace:
            case None:
                r = {}
            case dict():
                r = replace
            case str() | Path():
                r = tomllib.loads(Path(replace).read_text('UTF-8'))

        self._replace = r

    @staticmethod
    def _abbrev_region(data: LocalGovDict, abbrev: RegionAbbrev):
        return data | {abbrev[k]: v for k, v in data.items() if k in abbrev}

    def guess_expr(self, expr: pl.Expr, target: Literal['광역', '기초']):
        guess = expr
        for src, dst in self._replace.items():
            guess = guess.str.replace(src, dst)

        for region, gov in self._region.items():
            pattern = (
                _pattern([k for k, v in gov.items() if not v])
                if target == '광역'
                else _pattern(list(chain.from_iterable(gov.values())))
            )
            guess = guess.str.replace(pattern=pattern, value=region)

        return (
            pl
            .when(guess.is_in(list(self._region.keys())))
            .then(guess)
            .otherwise(pl.lit(None))
        )

    def guess(
        self,
        data: pl.DataFrame | pl.LazyFrame,
        *,
        address: str | pl.Expr = '주소',
        gov: str | pl.Expr | None = '광역자치단체',
    ):
        address = pl.col(address) if isinstance(address, str) else address

        a1 = self.guess_expr(address, '기초')
        a2 = self.guess_expr(address, '광역')

        if gov is not None:
            expr_gov = pl.col(gov) if isinstance(gov, str) else gov
            g2 = self.guess_expr(expr_gov, '광역')
        else:
            g2 = pl.lit(None)

        c = ('_주소추정1', '_주소추정2', '_신청지추정')
        return (
            data
            .with_columns(a1.alias(c[0]), a2.alias(c[1]), g2.alias(c[2]))
            .with_columns(
                pl
                .when(pl.col(c[0]).is_not_null())
                .then(pl.col(c[0]))
                .when(pl.col(c[1]).is_not_null())
                .then(pl.col(c[1]))
                .otherwise(pl.col(c[2]))
                .alias('지역')
            )
            .with_columns(
                pl
                .when(
                    pl.lit(bool(gov))
                    & (expr_gov == '경기')
                    & (address.str.contains('광주'))
                )
                .then(pl.lit('중부2'))
                .otherwise(pl.col('지역'))
                .alias('지역')
            )
        )


if __name__ == '__main__':
    import rich

    rich.print(Breaks.read())
