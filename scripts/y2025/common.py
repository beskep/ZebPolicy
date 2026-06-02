import dataclasses as dc
import enum
import functools
from typing import ClassVar

import numpy as np
from cmap import Colormap


class Grade(enum.StrEnum):
    NO_PV = 'NO-PV'
    BASE = 'Baseline'
    SUB5 = '준ZEB5'
    ZEB5 = 'ZEB5'
    ZEB4 = 'ZEB4'
    ZEB3 = 'ZEB3'
    ZEB2 = 'ZEB2'
    ZEB1 = 'ZEB1'
    ZEBP = 'ZEB+'


@dc.dataclass
class ZebPalette:
    palette: str = 'tol:bright'
    value_range: tuple[float, float] = (0.1, 0.9)

    baseline: str = '#546E7A'
    sub5: str = '#B0BEC5'

    INDEX: ClassVar[dict[str, tuple[int, ...]]] = {
        'tol:light': (6, 7, 5, 2, 0, 1),
        'tol:vibrant': (4, 5, 3, 2, 0, 1),
        'tol:bright': (5, 4, 3, 2, 0, 1),
    }

    @functools.cached_property
    def colors(self):
        cmap = Colormap(self.palette)

        if index := self.INDEX.get(self.palette):
            array = cmap(index)
        else:
            count = sum(1 for x in Grade if x.startswith('ZEB'))
            array = cmap(np.linspace(*self.value_range, count))

        colors = ['#FF0000', self.baseline, self.sub5, *array]
        return dict(zip(Grade, colors, strict=True))
