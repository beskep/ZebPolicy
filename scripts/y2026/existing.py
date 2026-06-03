"""2026-06-02 기존 건물 ZEB 전환 분석."""

import cyclopts

from zeb.utils.cli import App

app = App(
    config=cyclopts.config.Toml(
        '.env.toml', allow_unknown=True, use_commands_as_keys=False
    )
)
