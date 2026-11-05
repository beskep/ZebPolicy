"""에너지경제연구원 KEEI 2025 장기에너지전망 데이터 해석."""

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import cmap
import cyclopts
import numpy as np
import polars as pl
import ultraplot as uplt
from scipy.interpolate import PchipInterpolator

from zeb.cli import App
from zeb.y2026.config import Paths  # ruff: ignore[typing-only-first-party-import]

app = App(
    config=cyclopts.config.Toml(
        'env.toml',
        root_keys='2026',
        allow_unknown=True,
        use_commands_as_keys=False,
    )
)

_CYCLE = 'tol:bright_alt'


@app.command
@dataclass
class Interpolate:
    paths: Paths

    src: Path = Path('data/2026/outlook.csv')
    """KEEI 전망 원자료 (wide format)."""

    interpolated: Path = Path('data/2026/outlook.parquet')

    variable: str = '에너지 수요'
    start: int = 2024
    """첫 절점 연도."""

    plot: bool = True

    KEYS: ClassVar[tuple[str, ...]] = ('sector', 'variable', 'unit', 'group')

    def _interpolate(self):
        data = (
            pl
            .read_csv(self.src)
            .filter(pl.col('variable') == self.variable)
            .unpivot(index=[*self.KEYS, 'unit'], variable_name='year')
            .with_columns(pl.col('year').cast(pl.Int64))
            .filter(pl.col('year') >= self.start)
            .drop_nulls('value')
            .sort(*self.KEYS, 'year')
        )

        if (unit := data['unit'].unique().item()) != '백만 toe':
            raise ValueError(unit)

        def pchip(group: pl.DataFrame) -> pl.DataFrame:
            # 절점 연도(year)를 x로 사용 → 절점 간격(6, 10, 10년)이 반영됨.
            # Fritsch-Carlson 기울기, 양 끝은 한쪽 3점 차분.
            x = group['year'].to_numpy()
            y = group['value'].to_numpy().astype(float)
            years = np.arange(x.min(), x.max() + 1)
            return (
                pl
                .DataFrame({
                    'year': years,
                    'value': PchipInterpolator(x, y)(years),
                    'observed': np.isin(years, x),  # 관측치(절점) 여부
                })
                .with_columns(pl.lit(group[k].first()).alias(k) for k in self.KEYS)
                .with_columns()
            )

        return (
            pl
            .concat(pchip(g) for g in data.partition_by(self.KEYS, maintain_order=True))
            .select(*self.KEYS, 'year', 'observed', 'value')
            .with_columns(pl.col('value') * (10**6))  # 백만 toe -> toe
            .rename({'value': 'energy_demand_toe'})
        )

    def _plot(self, data: pl.DataFrame):
        sectors = ('서비스', '가정')
        fig, axes = uplt.subplots(nrows=1, ncols=2)
        for (sector, group), sub in data.group_by(
            'sector', 'group', maintain_order=True
        ):
            ax = axes[sectors.index(sector)]
            ax.plot(
                sub['year'],
                sub['energy_demand_toe'],
                label=group,
                cycle=_CYCLE,
                alpha=0.8,
            )

        for sector, ax in zip(sectors, axes, strict=True):
            ax.format(title=sector, xlabel='', ylabel=f'{self.variable} [toe]')
            ax.legend()
            ax.set_ylim(0)

        fig.savefig(self.paths.quantity.root / '01.outlook-energy-demand.png')

    def __call__(self):
        data = self._interpolate()

        if self.interpolated is not None:
            data.write_parquet(self.interpolated)

        if not self.plot:
            return

        self._plot(data)


# TODO: KEEI 예측 소비량 -> 배출량 ECO2 배출계수로 평가

if __name__ == '__main__':
    cmap.register_mpl(_CYCLE)
    app()
