"""2025-05-29 주거 분석."""

from __future__ import annotations

import dataclasses as dc
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import cyclopts
import more_itertools as mi
import numpy as np
import polars as pl
import polars.selectors as cs
import rich
import sklearn.preprocessing as skp
from loguru import logger
from scipy.spatial.distance import cdist
from xlsxwriter import Workbook

from zeb import utils

if TYPE_CHECKING:
    from collections.abc import Sequence


class V:
    APPLICATION_NUMBER = '신청번호'
    NAME = '건축물명'

    USE1 = '건물주용도'
    USE2 = '용도분류'

    HEATING_SYSTEM = '온열원설비_난방방식'
    COOLING_SYSTEM = '냉열원설비_냉방방식'

    GFA = '연면적'
    FOOTPRINT_AREA = '건축면적'
    EXCLUSIVE_AREA = '전용면적'

    AVG_EXCLUSIVE_AREA = '평균 전용면적'
    GF_TO_FOOTPRINT_RATIO = '연면적/건축면적비'

    WWR = '창면적비_평균'

    HOUSEHOLD_COUNT = '세대수'
    SECTOR = '주체'
    FLOORS = '건물규모'  # 지하 n층 / 지상 n~n층(n개동)

    HIGHEST_FLOOR = '최고층수'

    COMPLEX_SCALE = '단지규모'
    HOUSEHOLD_SCALE = '세대규모'  # 공동주택 단지 평균 전용면적 기준


app = utils.cli.App(
    config=cyclopts.config.Toml(
        path=Path(__file__).parent / 'config.toml',
        root_keys='residential',
        use_commands_as_keys=False,
    )
)


@app.command
def prep(
    *,
    root: Path,
    complex_breaks: Sequence[int] = (300, 500, 1000),
    household_breaks: Sequence[float] = (60, 85),
):
    source = mi.one(root.glob('*DB.xlsx'))
    logger.info(f'{source=}')

    complex_labels = [f'C{i + 1}' for i in range(len(complex_breaks) + 1)]
    household_labels = [f'A{i + 1}' for i in range(len(household_breaks) + 1)]

    data = (
        pl
        .read_excel(source)
        .with_columns(
            cs.ends_with('면적').cast(pl.Float64, strict=False),
            cs.starts_with('창면적비').cast(pl.Float64, strict=False),
        )
        .with_columns(
            # 평균 전용면적
            (
                pl.col(V.EXCLUSIVE_AREA)  # fmt
                / pl.col(V.HOUSEHOLD_COUNT)
            ).alias(V.AVG_EXCLUSIVE_AREA),
            # 연면적/건축면적비
            (
                pl.col(V.GFA)  # fmt
                / pl.col(V.FOOTPRINT_AREA)
            ).alias(V.GF_TO_FOOTPRINT_RATIO),
        )
        .with_columns(
            # 규모 분류
            pl
            .col(V.HOUSEHOLD_COUNT)
            .cut(complex_breaks, labels=complex_labels, left_closed=True)
            .alias(V.COMPLEX_SCALE),
            pl
            .col(V.AVG_EXCLUSIVE_AREA)
            .cut(household_breaks, labels=household_labels, left_closed=True)
            .alias(V.HOUSEHOLD_SCALE),
        )
        .with_columns(
            # 층수
            pl
            .col(V.FLOORS)
            .str.extract(r'지하\s*(\d*)층')
            .cast(pl.UInt8, strict=False)
            .alias('지하층'),
            pl
            .col(V.FLOORS)
            .str.replace_many(['∼', '츷'], ['~', '층'])  # ruff:ignore[ambiguous-unicode-character-string]
            .str.extract(r'지상\s*(\d+~)?(\d*)\s*층?', group_index=2)
            .cast(pl.UInt8, strict=False)
            .alias(V.HIGHEST_FLOOR),
        )
    )
    rich.print(data)

    data.write_parquet(root / '0000.data.parquet')
    data.write_excel(root / '0000.data.xlsx')


@dc.dataclass
class GroupDistance:
    center: Literal['mean', 'median'] = 'median'
    metric: str = 'euclidean'
    cdist_kwargs: dict = dc.field(default_factory=dict)

    def _dist_to_center(self, array: np.ndarray) -> np.ndarray:
        center: np.ndarray = (
            np.mean(array, axis=0)
            if self.center == 'mean'
            else np.median(array, axis=0)
        )

        return cdist(
            array,
            center.reshape([1, -1]),
            metric=self.metric,
            **self.cdist_kwargs,
        )

    def iter_dist_to_center(
        self,
        data: pl.DataFrame,
        group: str | Sequence[str],
        values: str | Sequence[str],
    ):
        for _, df in data.group_by(group):
            dist = self._dist_to_center(df.select(values).to_numpy())
            yield df.with_columns(pl.Series('distance', dist.ravel()))

    def dist_to_center(
        self,
        data: pl.DataFrame,
        group: str | Sequence[str],
        values: str | Sequence[str],
    ):
        return pl.concat(
            self.iter_dist_to_center(data=data, group=group, values=values)
        )


@dc.dataclass
class ReprCase:
    source: str | Path

    group_vars: Sequence[str] = (
        V.COMPLEX_SCALE,
        V.HOUSEHOLD_SCALE,
        V.USE2,
        V.SECTOR,
    )
    reference_vars: Sequence[str] = (
        V.APPLICATION_NUMBER,
        V.NAME,
        V.HEATING_SYSTEM,
        V.COOLING_SYSTEM,
    )
    value_vars: Sequence[str] = (
        V.FOOTPRINT_AREA,
        V.AVG_EXCLUSIVE_AREA,
        V.GF_TO_FOOTPRINT_RATIO,
        V.HOUSEHOLD_COUNT,
        V.HIGHEST_FLOOR,
        V.WWR,
    )

    method: Literal['param', 'non-param'] = 'non-param'
    null: Literal['drop', 'mean', 'median'] = 'drop'
    metric: str = 'euclidean'
    sample_count: int = 100

    data: pl.DataFrame = dc.field(init=False)

    def __post_init__(self):
        cols = [*self.group_vars, *self.reference_vars, *self.value_vars]

        if x := sorted(set(mi.duplicates_everseen(cols))):
            msg = f'Duplicated vars: {x}'
            raise ValueError(msg)

        data = (
            pl
            .scan_parquet(self.source)
            .with_columns(pl.col(V.HEATING_SYSTEM).replace({'': None}))
            .drop_nulls(V.HEATING_SYSTEM)
            .sort(V.APPLICATION_NUMBER)
            .select(cols)
            .with_row_index()
            .collect()
        )

        if x := data.select(self.value_vars).select(~cs.numeric()).columns:
            msg = f'Non-numeric columns in value_vars: {x}'
            raise ValueError(msg)

        group_index = (
            data
            .select(self.group_vars)
            .unique()
            .sort(pl.all())
            .with_row_index('group_index')
        )

        self.data = (
            data
            .join(group_index, on=self.group_vars, how='left')
            .select('index', 'group_index', *cols)
            .with_columns()
        )

    def normalized_distance(self):
        values = self.data.select('index', *self.value_vars)

        match self.null:
            case 'drop':
                values = values.drop_nulls()
            case 'mean':
                values = values.fill_null(strategy='mean')
            case 'median':
                values = values.fill_null(pl.all().median())

        scaler = skp.StandardScaler() if self.method == 'param' else skp.RobustScaler()
        scaled = (
            pl
            .DataFrame(
                scaler.fit_transform(values.drop('index').to_numpy()),
                schema=self.value_vars,
            )
            .with_columns(values['index'])
            .with_columns()
        )

        cols = ['index', 'group_index', *self.group_vars, *self.reference_vars]
        data = (
            self.data
            .select(cols)
            .join(scaled, on='index', how='left')
            .drop_nulls(self.value_vars)
        )

        return (
            GroupDistance(
                center='mean' if self.method == 'param' else 'median',
                metric=self.metric,
            )
            .dist_to_center(data, group=self.group_vars, values=self.value_vars)
            .with_columns(
                pl.col('distance').rank().over('group_index').alias('distance_rank')
            )
            .sort('group_index', 'index')
        )

    def write(self, path: str | Path):
        norm = self.normalized_distance().sort('group_index', 'distance_rank')
        data = (
            self.data
            .join(
                norm.select('index', 'distance', 'distance_rank'),
                on='index',
                how='left',
            )
            .sort('group_index', 'distance_rank')
            .with_columns()
        )

        fmt: dict = {
            'distance': {
                'type': '2_color_scale',
                'min_color': '#FFFFFF',
                'max_color': '#FF0000',
            }
        }

        with Workbook(path, options={'nan_inf_to_errors': True}) as wb:

            def write(data: pl.DataFrame, worksheet: str, *, cond: dict | None = None):
                data.write_excel(
                    wb,
                    worksheet=worksheet,
                    conditional_formats=cond,
                    column_widths=min(120, max(60, int(1600 / data.width))),
                )

            gray = wb.add_format({'bg_color': '#E0E0E0'})
            fmt |= {
                self.group_vars: {
                    'type': 'formula',
                    'criteria': '=ISODD($B2)',
                    'format': gray,
                }
            }

            write(data, worksheet='data', cond=fmt)
            write(norm, worksheet='normalized', cond=fmt)


@app.command
def repr_case(
    root: Path,
    method: Literal['param', 'non-param'] = 'non-param',
    null: Literal['drop', 'mean', 'median'] = 'drop',
):
    """집단별 대표 케이스 선정 (집단 중앙과 거리 계산)."""
    rc = ReprCase(source=root / '0000.data.parquet', method=method, null=null)
    rc.write(root / f'0001.disatance {method} {null=}.xlsx'.replace("'", ''))


if __name__ == '__main__':
    app()
