from typing import Literal

import matplotlib.pyplot as plt
import polars as pl
import polars.selectors as cs
import seaborn as sns
from cyclopts import App

from scripts.roadmap.config import Config
from zeb.roadmap.classification import Breaks
from zeb.utils.mpl import MplTheme

app = App()


@app.command
def dist_light_pv(*, figsize: tuple[float, float] = (11.5, 7)):
    """제로에너지건축물의 구성 기술 영향도 및 민감도 분석 -- 조명밀도, PV."""
    conf = Config.read()
    plot_dir = conf.root / 'plot'
    plot_dir.mkdir(exist_ok=True)

    lf = (
        pl
        .scan_parquet(conf.interval_eda.root / '02설계요소민감도분석.parquet')
        .select(
            pl.col('건물주용도').alias('용도'),
            pl.col('평균조명에너지부하율').alias('조명 밀도 [W/m²]'),
            pl.col('태양광_모듈면적').truediv('건축면적').alias('태양광 모듈 면적비'),
        )
        .with_columns()
    )

    usage = {
        '교육연구시설': '교육연구',
        '업무시설': '업무',
        '제1종 근린생활시설': '제1종 근생',
    }
    data = (
        pl
        .concat([
            lf.with_columns(pl.lit('전체').alias('용도')),
            lf.filter(pl.col('용도').is_in(usage.keys())).with_columns(
                pl.col('용도').replace(usage)
            ),
        ])
        .with_columns(
            cs.contains('조명', 'PV').cast(pl.Float64, strict=False),
            hue=pl.col('용도') == '전체',
        )
        .collect()
    )

    MplTheme(context='paper', fig_size=figsize).grid().apply()
    kwargs = {
        'legend': False,
        'order': [*usage.values(), '전체'],
        'hue': 'hue',
        'linewidth': 0.75,
        'fliersize': 4,
        'flierprops': {'alpha': 0.8},
    }

    fig, ax = plt.subplots()
    sns.boxplot(
        data,
        x='조명 밀도 [W/m²]',
        y='용도',
        ax=ax,
        palette=['xkcd:light orange', '.95'],
        **kwargs,
    )
    ax.set_ylabel('')
    fig.savefig(plot_dir / 'ZEB-조명밀도.png')
    plt.close(fig)

    fig, ax = plt.subplots()
    sns.boxplot(
        data,
        x='태양광 모듈 면적비',
        y='용도',
        ax=ax,
        palette=['#7dba7f', '.95'],
        **kwargs,
    )
    ax.set_ylabel('')
    fig.savefig(plot_dir / 'ZEB-PV 면적비.png')
    plt.close(fig)


@app.command
def light_density(
    *,
    kind: Literal['bar', 'box', 'line'] = 'box',
    figsize: tuple[float, float] = (13.5, 6.2),
):
    """중간발표 ppt p10 -- 전기-조명밀도."""
    conf = Config.read()
    plot_dir = conf.root / 'plot'
    plot_dir.mkdir(exist_ok=True)

    p = '정책구간'
    ld = '평균조명에너지부하율'

    data = (
        pl
        .scan_parquet(conf.interval_eda.root / '01정책효과분석.parquet')
        .select(p, ld)
        .sort(p)
        .collect()
    )

    MplTheme('paper', fig_size=figsize).grid(alpha=0.5).apply()

    fig, ax = plt.subplots()

    kwargs = {'x': p, 'y': ld, 'ax': ax}
    match kind:
        case 'bar':
            sns.barplot(data, alpha=0.8, **kwargs)
        case 'box':
            sns.boxplot(data, boxprops={'alpha': 0.8}, **kwargs)
        case 'line':
            sns.pointplot(
                data, marker='D', capsize=0.08, err_kws={'alpha': 0.8}, **kwargs
            )

    sns.despine(ax=ax, top=True, right=True, left=True, bottom=False)
    ax.set_xlabel('')
    ax.set_ylabel('')

    fig.savefig(plot_dir / f'중간발표-정책구간별 조명밀도 {kind}.png')


@app.command
def p4_consumption(
    kind: Literal['bar', 'box', 'violin'] = 'box',
    *,
    figsize: tuple[float, float] = (12.6, 7.5),
):
    """중간발표 ppt p11 -- P4 정책구간 에너지소요량 분포."""
    conf = Config.read()
    plot_dir = conf.root / 'plot'
    plot_dir.mkdir(exist_ok=True)

    policy = '정책구간'
    owner = '주체'
    area = '연면적'
    scale = '규모'
    consumption = '에너지소요량'

    breaks = Breaks(period=[], area=[3000, 10000])
    data = (
        pl
        .scan_parquet(conf.interval_eda.root / '01정책효과분석.parquet')
        .filter(pl.col(policy) == 'P4')
        .select(
            owner,
            breaks.cut(pl.col(area), breaks='area').alias(scale),
            pl.col('에너지소요량_합계').alias(consumption),
        )
        .sort(owner, scale)
        .collect()
    )

    MplTheme(1, fig_size=figsize).grid(alpha=0.5).apply()
    palette = ['#5591c7', '#70ad47']

    fig, ax = plt.subplots()
    kwargs = {'x': consumption, 'y': scale, 'hue': owner, 'palette': palette, 'ax': ax}

    match kind:
        case 'box':
            sns.boxplot(data, gap=0.1, **kwargs)
        case 'bar':
            sns.barplot(data, **kwargs)
        case 'violin':
            sns.violinplot(data, cut=0, **kwargs)

    ax.set_xlabel('에너지소요량 (kWh/m²yr)')
    ax.set_ylabel('')
    if legend := ax.get_legend():
        legend.set_title('')

    sns.despine(ax=ax, top=True, right=True, left=kind == 'bar', bottom=True)

    fig.savefig(plot_dir / f'중간발표-P4 소요량-{kind}.png')


if __name__ == '__main__':
    app()
