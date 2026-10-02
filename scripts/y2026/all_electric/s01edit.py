from dataclasses import dataclass

import cyclopts
import structlog

from zeb.utils.cli import App
from zeb.y2026 import editor

app = App(
    config=cyclopts.config.Toml(
        'env.toml',
        root_keys=['2026', 'all-electric'],
        allow_unknown=True,
        use_commands_as_keys=False,
    ),
    result_action=['call_if_callable', 'print_non_int_sys_exit'],
)
logger = structlog.stdlib.get_logger()


@app.command
@dataclass
class Edit(editor.EditZeb):
    pass


@app.command
@dataclass
class Parse(editor.ParseReport):
    pass


@app.command
@dataclass
class RequiredPV(editor.RequiredPV):
    pass


if __name__ == '__main__':
    # 1. edit --pv zero
    # 2. parse
    # 3. required-pv
    # 4. edit --pv required
    # 5. parse
    app()
