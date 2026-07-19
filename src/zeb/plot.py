from typing import TYPE_CHECKING, Any

import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from seaborn._base import variable_type  # ruff:ignore[import-private-name]

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pandas as pd
    from matplotlib.axes import Axes


def _category(data: pd.DataFrame, x: str, y: str) -> str:
    xy = (x, y)
    dtypes = [variable_type(data[v]) for v in xy]

    match dtypes:
        case ('numeric', t) if t != 'numeric':
            return y
        case (t, 'numeric') if t != 'numeric':
            return x

    raise TypeError(tuple(zip(xy, [data[v].dtype for v in xy], dtypes, strict=True)))


def boxstrip(  # ruff:ignore[too-many-arguments]
    data: pd.DataFrame,
    *,
    x: str,
    y: str,
    hue: str | None = None,
    order: Sequence[str] | None = None,
    hue_order: str | None = None,
    palette: Any = None,
    ax: Axes | None = None,
    box_width: float = 0.25,
    offset: float = 0.25,
    **kwargs,
):
    """Boxplot + Stripplot (Raincloud 대신)."""
    category = _category(data=data, x=x, y=y)
    count = data[category].nunique()

    if ax is None:
        ax = plt.gca()

    common = {
        'data': data,
        'x': x,
        'y': y,
        'hue': hue,
        'order': order,
        'hue_order': hue_order,
        'color': sns.color_palette(n_colors=1)[0] if hue is None else None,
        'palette': palette,
        'ax': ax,
    }
    sns.stripplot(**common, zorder=1, **kwargs.get('strip', {}))
    sns.boxplot(
        **common,
        width=box_width,
        positions=np.arange(count) - offset,
        showfliers=False,
        zorder=2,
        **kwargs.get('box', {}),
    )

    if category is x:
        ax.set_xticks(np.array(ax.get_xticks()) - offset / 2)
    else:
        ax.set_yticks(np.array(ax.get_yticks()) - offset / 2)

    ax.autoscale_view()

    return ax


if __name__ == '__main__':
    data = sns.load_dataset('tips')
    fig, axes = plt.subplots(1, 2, figsize=(12, 6))
    boxstrip(data, x='day', y='total_bill', ax=axes[0])
    boxstrip(data, x='total_bill', y='day', ax=axes[1])
    fig.tight_layout()
    plt.show()
