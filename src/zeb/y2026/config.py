import dataclasses as dc
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from zeb.y2026.common import Use


@dc.dataclass
class Paths:
    root: Path

    certificates: Path
    non_res: Path
    res: Path

    eco2: Path = Path('ECO2')  # ECO2 파일 저장, 연산 경로
    raw: Path = Path('00.raw')
    existing: Path = Path('01.existing')
    analysis: Path = Path('02.analysis')

    SUBDIRS: ClassVar[tuple[str, ...]] = (
        'eco2',
        'raw',
        'existing',
        'analysis',
    )

    def __post_init__(self):
        for sub in self.SUBDIRS:
            path: Path = getattr(self, sub)
            if not path.is_relative_to(self.root):
                setattr(self, sub, self.root / path)

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
        config=cyclopts.config.Toml('env.toml', root_keys=['2026', 'paths']),
        result_action='print_non_none_return_zero',
    )

    @app.default
    def main(paths: Paths):
        return paths

    app()
