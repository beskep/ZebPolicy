import dataclasses as dc
from pathlib import Path

import cyclopts

from zeb.utils.cli import App

config = [
    cyclopts.config.Toml('config.toml', root_keys='quantity', use_commands_as_keys=x)
    for x in [False, True]
]
app = App(config=config, result_action=['call_if_callable', 'print_non_int_sys_exit'])


@dc.dataclass
class Dirs:
    root: Path

    raw: Path = Path('00.raw/hub')
    data: Path = Path('01.data')
    sample: Path = Path('02.sample')
    analysis: Path = Path('03.analysis')
    processed: Path = Path('04.processed')

    def __post_init__(self):
        for field in dc.fields(self):
            if field.name == 'root':
                continue

            setattr(self, field.name, self.root / getattr(self, field.name))
