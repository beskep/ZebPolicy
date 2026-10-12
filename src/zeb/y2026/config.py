from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zeb.y2026.common import Use

_NULL = Path('[NULL]')


def _resolve(node: object, root: Path) -> None:
    if not is_dataclass(node):
        raise TypeError(node)

    for f in fields(node):
        v = getattr(node, f.name)
        if is_dataclass(v):
            _resolve(v, root)
        elif f.type is not Path or f.metadata.get('absolute', False):
            continue
        else:
            setattr(node, f.name, root.joinpath(v).resolve())


@dataclass
class _Existing:
    raw: Path = Path('00.existing/00.raw')
    analysis: Path = Path('01.existing/01.analysis')

    certificates: Path = field(metadata={'absolute': True}, default=_NULL)


@dataclass
class _Eco2Raw:
    root: Path = Path('ECO2Raw')
    existing: Path = Path('ECO2Raw/01.existing')
    all_electric: Path = Path('ECO2Raw/02.all-electric')


@dataclass
class _Eco2:
    root: Path = Path('01.ECO2')
    parsed: Path = Path('01.ECO2/01.parsed')  # FIXME
    analysis: Path = Path('01.ECO2/02.analysis')  # FIXME

    non_res: Path = Path('../../ZebSysImprov/03.eco2/01.비주거/03.PV/cases')
    res: Path = Path('../../ZebSysImprov/03.eco2/02.주거/04.output-pv-required/cases')

    def use(self, use: Use):
        """2025년 tpl 파일 경로."""
        match use:
            case 'non-res':
                return self.non_res
            case 'res':
                return self.res


@dataclass
class _DataCenter:
    root: Path = Path('02.datacenter')
    src: Path = Path('02.datcenter/src')
    edit: Path = Path('02.datcenter/edit')
    plot: Path = Path('02.datcenter/plot')


@dataclass
class _Quantity:
    root: Path = Path('03.quantity')
    raw: Path = Path('03.quantity/01.raw')
    data: Path = Path('03.quantity/02.data')
    eda: Path = Path('03.quantity/03.eda')


@dataclass
class Paths:
    root: Path

    eco2raw: _Eco2Raw = field(default_factory=_Eco2Raw)  # ECO2 파일 저장, 연산 경로

    existing: _Existing = field(default_factory=_Existing)
    eco2: _Eco2 = field(default_factory=_Eco2)
    datacenter: _DataCenter = field(default_factory=_DataCenter)
    quantity: _Quantity = field(default_factory=_Quantity)

    def __post_init__(self) -> None:
        self.root = self.root.expanduser().resolve()
        _resolve(self, self.root)


if __name__ == '__main__':
    import cyclopts

    app = cyclopts.App(
        config=cyclopts.config.Toml('env.toml', root_keys='2026', allow_unknown=True),
        result_action='print_non_none_return_zero',
    )

    @app.default
    def main(paths: Paths):
        return paths

    app()
