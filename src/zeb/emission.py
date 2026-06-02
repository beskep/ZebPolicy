import dataclasses as dc
import functools
from pathlib import Path

import msgspec
import pint


@dc.dataclass(frozen=True)
class EmissionFactor:
    coefficient: float
    unit: str


RawType = dict[str, dict[str, EmissionFactor]]


@dc.dataclass(frozen=True)
class EmissionFactors:
    raw: RawType  # {ref: {source: coefficient}}
    unit: str = 'kg/kWh'

    @classmethod
    def read(cls, path: str | Path | None = None):
        p = (
            Path(__file__).parent / 'emission-factor.toml'
            if path is None
            else Path(path)
        )
        raw = msgspec.toml.decode(p.read_text('utf-8'), type=RawType)
        return cls(raw)

    @functools.cached_property
    def conversion(self):
        """단위환산계수."""
        ur = pint.UnitRegistry[float]()
        ur.define('tonCO2 = 1000 kg')  # NOTE *NO SHORT TON*
        ur.define('kgCO2 = kg')
        ur.define('tonC = (44/12) tonCO2')

        def it():
            for factor in self.raw.values():
                for f in factor.values():
                    yield f.unit

        units = set(it())
        return {
            x: ur.convert(1.0, x.replace('CO₂', 'CO2').replace('-eq', ''), self.unit)
            for x in units
        }

    @functools.cached_property
    def emission_factors(self):
        """에너지원별 CO2 배출 계수 (지정한 `unit` 단위)."""
        return {
            reference: {
                k: v.coefficient * self.conversion[v.unit] for k, v in factor.items()
            }
            for reference, factor in self.raw.items()
        }

    def iter(self):
        for reference, factors in self.emission_factors.items():
            for source, factor in factors.items():
                yield reference, source, factor

    def dataframe(self):
        import polars as pl  # noqa: PLC0415

        return pl.DataFrame(
            list(self.iter()),
            schema=['reference', 'source', 'emission_factor'],
            orient='row',
        )


if __name__ == '__main__':
    import polars as pl
    import rich

    console = rich.get_console()

    factors = EmissionFactors.read()

    console.print('raw')
    console.print(factors.raw)

    console.rule()
    console.print('conversion')
    console.print(factors.conversion)

    console.rule()
    console.print('emission_factors')
    console.print(factors.emission_factors)

    console.rule()
    pl.Config.set_tbl_rows(20)
    console.print(factors.dataframe())
