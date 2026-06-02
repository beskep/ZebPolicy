"""기타 그래프."""

from pathlib import Path

import polars as pl
import rich
import seaborn as sns
from cyclopts.config import Toml as TomlConf
from matplotlib.figure import Figure

from zeb import utils
from zeb.utils.cli import App

app = App(
    config=[
        TomlConf('config.toml', root_keys='etc', use_commands_as_keys=False),
        TomlConf('config.toml', root_keys='etc'),
    ],
)


@app.command
def bc_ratio(root: Path, scale: float = 0.7):
    (
        utils.mpl
        .MplTheme(scale, fig_size=(9.35, 5.5))
        .grid()
        .apply({
            'legend.fontsize': 'small',
            'legend.markerscale': 0.8,
            'legend.handletextpad': 0.4,
            'legend.columnspacing': 1,
        })
    )

    src = Path(root / '용적률완화.xlsx')

    @utils.pl.frame_cache(src.with_suffix('.parquet'))
    def read():
        return pl.read_excel(src).unpivot(index=['지역', '등급', '기간', '유형'])

    data = (
        read()
        .lazy()
        .collect()
        .with_columns(
            pl.col('등급').str.replace_many([' ', '('], ['', '\n(']),
            pl.max('value').over('지역').alias('vmax'),
        )
    )
    min_period = data['기간'].min()

    for (region, kind, period), df in data.group_by(
        ['지역', '유형', '기간'], maintain_order=True
    ):
        fig = Figure()
        ax = fig.subplots()

        sns.pointplot(df, x='등급', y='value', hue='variable', ax=ax, alpha=0.8)
        ax.set_xlabel('')
        ax.set_ylabel('B/C Ratio')
        ax.legend(
            loc='upper center' if period == min_period else 'lower center',
            title='',
            ncols=4,
        )

        ax.dataLim.update_from_data_y([df['vmax'][0]])
        ax.autoscale_view(scalex=False)
        ax.set_ylim(0)

        fig.savefig(root / f'01.용적률완화 {region=!s} {kind=!s} {period=!s}.png')


@app.command
def cost(root: Path, scale: float = 0.7):
    utils.mpl.MplTheme(scale, fig_size=(10, 5.5)).grid().apply()

    data = pl.read_excel(root / '공사비.xlsx')
    base = data.filter(pl.col('등급') == '일반수준')['공사비'].item()
    data = data.with_columns(ratio=(pl.col('공사비') - base) / base)
    rich.print(data)

    # 등급별 예상공사비
    fig = Figure()
    ax = fig.add_subplot()
    sns.pointplot(data, x='등급', y='공사비', ax=ax)

    xticks = ax.get_xticks()
    for x, y, r in zip(xticks, data['공사비'], data['ratio'], strict=True):
        ax.annotate(f'{r:+.1%}', xy=(x, y + 50), ha='center')

    ax.margins(y=0.2)
    ax.autoscale_view(scalex=False)
    ax.set_xlabel('')
    ax.set_ylabel('등급별 예상공사비 (천원/m²)')
    fig.savefig(root / '02.공사비-point.png')

    fig = Figure()
    ax = fig.add_subplot()
    sns.barplot(
        data,
        x='등급',
        y='공사비',
        ax=ax,
    )
    ax.bar_label(
        ax.containers[0],  # type: ignore[arg-type]
        labels=[f'{x:+.1%}' if x else '' for x in data['ratio']],
        padding=2,
        weight=500,
        color='navy',
    )

    ax.margins(y=0.2)
    ax.autoscale_view(scalex=False)
    ax.set_xlabel('')
    ax.set_ylabel('등급별 예상공사비 (천원/m²)')
    fig.savefig(root / '02.공사비-bar.png')

    # 등급별 에너지 절감 비용
    fig = Figure()
    ax = fig.add_subplot()
    sns.barplot(data, x='등급', y='에너지절감비용', ax=ax, color='seagreen')
    ax.set_xlabel('')
    ax.set_ylabel('에너지 절감 비용 (원/m²yr)')
    fig.savefig(root / '02.에너지절감비용.png')


if __name__ == '__main__':
    app()
