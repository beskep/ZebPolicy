from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zeb.y2026.common import Use


def _resolve(node: object, root: Path) -> None:
    if not is_dataclass(node):
        raise TypeError(node)

    for f in fields(node):
        v = getattr(node, f.name)
        if is_dataclass(v):
            _resolve(v, root)
        elif f.metadata.get('absolute', False):
            continue
        else:
            setattr(node, f.name, root / v)


@dataclass
class _Eco2:
    root: Path = Path('01.ECO2')
    raw: Path = Path('01.ECO2/00.raw')
    existing: Path = Path('01.ECO2/01.existing')
    analysis: Path = Path('01.ECO2/02.analysis')


@dataclass
class _DataCenter:
    root: Path = Path('02.datacenter')
    src: Path = Path('02.datcenter/src')
    edit: Path = Path('02.datcenter/edit')
    plot: Path = Path('02.datcenter/plot')


@dataclass
class Paths:
    root: Path

    certificates: Path = field(metadata={'absolute': True})
    non_res: Path = field(metadata={'absolute': True})
    res: Path = field(metadata={'absolute': True})

    eco2raw: Path = Path('ECO2Raw')  # ECO2 파일 저장, 연산 경로
    eco2: _Eco2 = field(default_factory=_Eco2)
    datacenter: _DataCenter = field(default_factory=_DataCenter)

    def __post_init__(self) -> None:
        self.root = self.root.expanduser().resolve()
        _resolve(self, self.root)

    def use(self, use: Use):
        """2025년 tpl 파일 경로."""
        match use:
            case 'non-res':
                return self.non_res
            case 'res':
                return self.res


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
