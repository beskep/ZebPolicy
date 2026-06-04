import dataclasses as dc
from pathlib import Path  # noqa: TC003


@dc.dataclass
class Paths:
    root: Path
    raw: Path
    existing: Path

    certificates: Path


if __name__ == '__main__':
    import cyclopts

    app = cyclopts.App(
        config=cyclopts.config.Toml('env.toml', root_keys='2026'),
        result_action='print_non_none_return_zero',
    )

    @app.default
    def main(paths: Paths):
        return paths

    app()
