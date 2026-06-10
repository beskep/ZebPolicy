import dataclasses as dc
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zeb.y2026.common import Use


@dc.dataclass
class Paths:
    root: Path
    eco2: Path  # ECO2 파일 저장, 연산 경로

    raw: Path
    existing: Path

    certificates: Path
    non_res: Path
    res: Path

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
