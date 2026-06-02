"""2025-06-16 설계현황 분석."""

from __future__ import annotations

import dataclasses as dc
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import cyclopts
import polars as pl
import polars.selectors as cs
import rich
import xlsxwriter

from zeb import utils

if TYPE_CHECKING:
    from collections.abc import Sequence


@dc.dataclass
class Summary:
    source: str | Path
    sheet: Literal['주거용', '주거용이외']

    groups: Sequence[str] = (
        '용도',
        '지역구분',
        '단지분류',
        '전용면적 분류',
        '규모분류',
        '등급',
        '주체',
    )
    values: Sequence[str] = (
        '벽체_열관류율',
        '지붕_열관류율',
        '바닥_열관류율',
        '창호_열관류율',
        '평균조명에너지부하율',
        '설치용량_세대',
        '모듈면적비_전용면적',
        '모듈면적비',
        '지열_난방_효율',
        '지열_냉방_효율',
    )
    equipment_groups: Sequence[str] = ('난방설비', '냉방설비', '급탕설비')

    equipment_values: Sequence[str] = (
        '온열원설비_효율',
        '냉열원설비_효율',
        '급탕설비_효율',
        '전열교환기_열회수효율_난방',
        '전열교환기_열회수효율_냉방',
    )

    data: pl.DataFrame = dc.field(init=False)

    def __post_init__(self):
        cols = pl.read_excel(
            self.source, sheet_name=self.sheet, read_options={'n_rows': 1}
        ).columns
        cols = list(
            set(cols)
            & {
                *self.groups,
                *self.values,
                *self.equipment_groups,
                *self.equipment_values,
            }
        )

        cols_set = set(cols)
        self.groups = [x for x in self.groups if x in cols_set]
        self.values = [x for x in self.values if x in cols_set]

        values = (set(self.values) | set(self.equipment_values)) & cols_set

        self.data = (
            pl
            .read_excel(self.source, sheet_name=self.sheet, columns=cols)
            .with_columns(cs.string().replace({'': None}))
            .with_columns(cs.contains('율').cast(pl.Float64, strict=False))
            .with_columns(pl.col(values).replace({0: None}))
        )

    def _data(self, columns: Sequence[str]):
        return self.data.select(columns).filter(~pl.all_horizontal(pl.all().is_null()))

    def desc_values(self):
        def it():
            for group in self.groups:
                yield (
                    utils.pl
                    .PolarsSummary(self._data([group, *self.values]), group=group)
                    .describe()
                    .select(pl.lit(group).alias('group'), pl.all())
                    .rename({f'group:{group}': 'group value'})
                )

        return pl.concat(it())

    def desc_equipment(self, *, group_combination: bool = True):
        groups = [x for x in ['단지분류', '규모분류', '등급'] if x in self.data.columns]

        def describe(group: str, equipment: str):
            return (
                utils.pl
                .PolarsSummary(
                    self._data([group, equipment, *self.equipment_values]),
                    group=[group, equipment],
                )
                .describe()
                .select(pl.lit(group).alias('group'), pl.all())
                .rename({f'group:{group}': 'group value'})
            )

        for eq in self.equipment_groups:
            yield eq, pl.concat(describe(g, eq) for g in groups)

            if group_combination:
                desc = utils.pl.PolarsSummary(
                    self._data([*groups, eq, *self.equipment_values]),
                    group=[*groups, eq],
                ).describe()
                yield f'{eq} 조합', desc

    def desc_transmittance(self, group: Sequence[str]):
        values = [x for x in self.values if '열관류율' in x]
        group = [x for x in group if x in self.groups]
        desc = utils.pl.PolarsSummary(
            self._data([*group, *values]), group=group
        ).describe()
        return group, desc

    def describe(self, path: str | Path, column_widths: int = 100):
        with xlsxwriter.Workbook(path) as wb:
            self.desc_values().write_excel(wb, column_widths=column_widths)

            for eq, desc in self.desc_equipment():
                desc.write_excel(wb, worksheet=eq, column_widths=column_widths)

            for group in [['지역구분', '등급'], ['등급', '규모분류', '단지분류']]:
                g, desc = self.desc_transmittance(group)
                sheet = f'열관류율 {"-".join(g)}'
                desc.write_excel(wb, worksheet=sheet, column_widths=column_widths)


app = utils.cli.App(
    config=cyclopts.config.Toml(
        path=Path(__file__).parent / 'config.toml',
        root_keys='design',
    )
)


@app.default
def main(
    src: Path,
    dst: Path | None = None,
    sheets: tuple[str, ...] = ('주거용', '주거용이외'),
):
    console = rich.get_console()
    dst = dst or src.parent

    for sheet in sheets:
        summary = Summary(src, sheet=sheet)  # type: ignore[arg-type]
        console.print(summary.data.glimpse(return_type='string'))
        summary.data.write_excel(dst / f'{sheet}-raw.xlsx')
        summary.describe(dst / f'{sheet}.xlsx')


if __name__ == '__main__':
    app()
