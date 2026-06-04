from __future__ import annotations

import dataclasses as dc
from itertools import chain, zip_longest
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

import matplotlib.pyplot as plt
import polars as pl
import polars.selectors as cs
import polars.testing
import rich
import seaborn as sns
from cmap import Colormap
from eco2.report import GraphReport, GraphReportRows
from loguru import logger
from matplotlib.layout_engine import ConstrainedLayoutEngine
from matplotlib.ticker import MaxNLocator

import zeb.roadmap.sensitivity as sens
from scripts.roadmap.config import Config
from zeb import utils
from zeb.utils.cli import App

if TYPE_CHECKING:
    from collections.abc import Collection

    from matplotlib.axes import Axes


Method = Literal['beta', 'rf']

REPORT_DIR = '04report'
SENSITIVITY_VARS = {
    'u_wall': '열관류율(구조체)',
    'u_window': '열관류율(창)',
    'shgc': 'SHGC',
    'heating': '난방효율',
    'cooling': '냉방효율',
    'hrv': '열교환효율',
    'cooling_control': '냉방제어',
    'light_density': '조명밀도',
    'pv_area': 'PV 면적',
    'pv_efficiency': 'PV 효율',
}


def get_root() -> Path:
    conf = Config.read()
    return conf.sensitivity.root


app = App()


@app.command
def check_reports(path: Path | None = None):
    path = path or get_root() / REPORT_DIR

    def find_missing_report():
        for xml in path.rglob('*.xml'):
            if not (xls := xml.with_suffix('.xls')).exists():
                yield xls

    if missing := tuple(find_missing_report()):
        for f in missing:
            logger.warning('missing report: {}', f)
    else:
        logger.info('no missing report')


def _read_reports(d: Path, variable: str = '[variable].json', *, monthly: bool = False):
    var = (
        pl
        .read_json(d / variable)
        .with_columns(
            pl
            .col('source', 'destination')
            .str.strip_suffix('.tplx')
            .str.strip_suffix('.xml')
            .str.split('/')
            .list.get(-1)
        )
        .rename({'destination': 'case'})
    )

    rows = GraphReportRows()

    def read_report(path: Path):
        report = GraphReport(path, rows=rows).data()

        if not monthly:
            report = report.filter(pl.col('month').is_null()).drop('month')

        return report.select(pl.lit(path.stem).alias('case'), pl.all())

    report = (
        pl
        .concat(read_report(x) for x in d.glob('*.xls'))
        .join(var, on='case', how='left')
        .with_columns(pl.col('LHS.cooling_control').round())
    )

    if (
        nulls := report
        .select('source', 'case')
        .filter(pl.col('source').is_null())
        .with_columns()
    ).height:
        raise ValueError(nulls)

    return report


@app.command
def convert(*, monthly: bool = False, sample_size: int = 100):
    root = get_root() / REPORT_DIR

    prefix = '[ECO2]'
    suffix = '(월간포함)' if monthly else ''

    for subdir in root.iterdir():
        if not subdir.is_dir():
            continue

        name = f'{prefix} {subdir.name}{suffix}'

        logger.info(subdir.name)
        report = _read_reports(subdir, monthly=monthly)
        report.write_parquet(root / f'{name}.parquet')

        if monthly:
            report.head(sample_size).write_excel(root / f'{name}-sample.xlsx')
        else:
            report.write_excel(root / f'{name}.xlsx')

        rich.print(report.head(2))
        logger.info(
            '{}, n_case={}',
            subdir,
            report.filter(pl.col('variable') == '에너지자립률(전체)').height,
        )


@dc.dataclass
class SensitivityAnalysis:
    source: dc.InitVar[Path | pl.DataFrame | pl.LazyFrame]
    y: tuple[str, ...] | None = None
    max_y: float | None = None

    x: tuple[str, ...] = dc.field(init=False)
    df: pl.DataFrame = dc.field(init=False)

    DEFAULT_Y: ClassVar[tuple[str, ...]] = (
        '요구량',
        '소요량',
        '1차소요량',
        '등급용1차소요량',
        '에너지자립률(전체)',
    )
    CASE_PATTERN: ClassVar[str] = (
        r'^(?<case_index>\d+)_(?<usage>\w+?)_(?<region>\w+?)_.*$'
    )
    INDEX: ClassVar[tuple[str, ...]] = (
        'case_index',
        'case',
        'usage',
        'region',
        'source',
    )

    def __post_init__(self, source: Path | pl.DataFrame | pl.LazyFrame):
        if self.y is None:
            self.y = self.DEFAULT_Y

        lf = (
            pl.scan_parquet(source, glob=False)
            if isinstance(source, Path)
            else source.lazy()
        )

        lf = (
            lf
            .with_columns(group=pl.col('case').str.extract_groups(self.CASE_PATTERN))
            .unnest('group')
            .with_columns()
        )

        if self.max_y is not None:
            lf = lf.filter(pl.col('value') < self.max_y)

        df = (
            lf
            .drop(cs.starts_with('ECO.'))
            .filter(
                pl.col('variable').is_in(self.y),
                (pl.col('energy') == '합계') | pl.col('energy').is_null(),
            )
            .collect()
        )

        self.x = tuple(
            x.removeprefix('LHS.') for x in df.select(cs.starts_with('LHS')).columns
        )
        self.df = (
            df
            .rename(lambda x: x.removeprefix('LHS.'))
            .pivot('variable', index=[*self.INDEX, *self.x], values='value')
            .with_row_index()
            .with_columns(pl.col('case_index').cast(pl.UInt32))
        )
        self.y = tuple(y for y in self.y if y in self.df.columns)

    def _analyser(
        self,
        data: pl.DataFrame | None = None,
        method: Method = 'beta',
    ):
        assert self.y is not None
        cls: type[sens.SensitivityAnalysis] = (
            sens.StandardizedCoefficient
            if method == 'beta'
            else sens.RandomForestFeatureImportance
        )
        return cls(data=self.df if data is None else data, x=self.x, y=self.y)

    def _analyse_group(
        self,
        group: Collection[str],
        value: Collection[str],
        data: pl.DataFrame,
        method: Method = 'beta',
    ):
        head = (pl.lit(v).alias(g) for g, v in zip(group, value, strict=True))
        return self._analyser(method=method, data=data).frame().select(*head, pl.all())

    def xorder(self):
        return {x: i for i, x in enumerate(['Intercept', *self.x])}

    def yorder(self):
        return {y: i for i, y in enumerate(self.y or self.DEFAULT_Y)}

    def analyse(self, group: Collection[str] | None = None, method: Method = 'beta'):
        xorder = self.xorder()
        yorder = self.yorder()

        if group is None:
            # 전체
            return (
                self
                ._analyser(self.df, method=method)
                .frame()
                .sort(
                    pl.col('y').replace_strict(yorder),
                    pl.col('x').replace_strict(xorder),
                )
            )

        dfs = (
            self._analyse_group(group=group, value=v, data=df)
            for v, df in self.df.group_by(group)
        )
        return pl.concat(dfs).sort(
            *group,
            pl.col('y').replace_strict(yorder),
            pl.col('x').replace_strict(xorder),
        )


@app.command
def check_index():
    root = get_root() / REPORT_DIR

    files = [
        x
        for x in root.glob('*.parquet')
        if x.name.startswith('[ECO2]') and '월간' not in x.name
    ]
    data = pl.scan_parquet(files, glob=False)

    sa = SensitivityAnalysis(data)
    print(sa.df)

    polars.testing.assert_series_equal(
        sa.df.select('index').to_series(),
        sa.df.select('case_index').to_series(),
        check_dtypes=False,
        check_names=False,
    )
    logger.info('이상 없음')


@app.command
def sensitivity(
    *,
    method: Method = 'beta',
    y: str | None = None,
    max_y: float | None = None,
):
    root = get_root() / REPORT_DIR

    m = {'beta': '표준회귀계수', 'rf': '특성중요도'}[method]
    prefix = f'[민감도][{m}]'
    suffix = '' if max_y is None else f' ({"y" if y is None else y} {max_y} 미만)'

    files = [
        x
        for x in root.glob('*.parquet')
        if x.name.startswith('[ECO2]') and '월간' not in x.name
    ]
    data = pl.scan_parquet(files, glob=False)

    sa = SensitivityAnalysis(data, y=None if y is None else (y,), max_y=max_y)
    group_dict = {'source': '건물', 'region': '지역', 'usage': '용도'}

    for group in [
        None,
        ['source'],
        ['region'],
        ['usage'],
        ['usage', 'region'],
    ]:
        g = (
            '전체'
            if group is None
            else 'group=' + '&'.join(group_dict[x] for x in group)
        )
        sensitivity = sa.analyse(group=group, method=method)

        path = root / f'{prefix} {g}{suffix}.parquet'
        sensitivity.write_parquet(path)
        sensitivity.write_excel(path.with_suffix('.xlsx'))


app.command(plot_app := App('plot'))


def _legend_order(ls: list):
    h = len(ls) // 2
    yield from chain.from_iterable(zip_longest(ls[:h], ls[h:]))


@plot_app.command
def plot_total(
    *,
    figsize: tuple[float, float] = (23, 7.4),
    vertical: bool = True,
    horizontal: bool = True,
):
    root = get_root() / REPORT_DIR
    data = (
        pl
        .scan_parquet(root / '[민감도][표준회귀계수] 전체.parquet', glob=False)
        .filter(pl.col('x') != 'Intercept')
        .with_columns(
            pl.col('x').replace_strict(SENSITIVITY_VARS),
            pl.col('y').replace_strict({
                '요구량': 'E요구량',
                '소요량': 'E소요량',
                '1차소요량': '1차E소요량',
                '등급용1차소요량': '등급용1차E소요량',
                '에너지자립률(전체)': 'E자립률',
            }),
        )
        .collect()
    )

    figsize_inches = (figsize[0] / 2.54, figsize[1] / 2.54)

    ax: Axes
    if vertical:
        fig, ax = plt.subplots()
        palette = list(Colormap('seaborn:tab20').color_stops.color_array[:10])
        sns.barplot(data=data, x='y', y='beta', hue='x', ax=ax, palette=list(palette))
        fig.set_size_inches(figsize_inches)
        ax.set_xlabel('')
        ax.set_ylabel('표준화 회귀계수')

        sns.despine(ax=ax, left=True, bottom=True)
        ax.axhline(y=0, c='0.25')

        handles, labels = ax.get_legend_handles_labels()
        ax.legend(
            list(_legend_order(handles)),
            list(_legend_order(labels)),
            ncols=len(palette) // 2,
        )
        fig.savefig(root / '민감도-전체-수직.png')
        plt.close(fig)

    if horizontal:
        grid = (
            sns
            .FacetGrid(data=data, col='y', despine=False)
            .map_dataframe(sns.barplot, x='beta', y='x', color='#7dba7f')
            .set_titles('')
            .set_axis_labels('', '')
        )
        grid.figure.set_size_inches(figsize_inches)
        ax = grid.figure.axes[0]
        ax.xaxis.set_major_locator(MaxNLocator(nbins=5))
        for name, ax in grid.axes_dict.items():
            ax.set_xlabel(f'{name} 민감도', fontdict={'size': 'small'})

        ConstrainedLayoutEngine().execute(grid.figure)
        grid.savefig(root / '민감도-전체-수평.png')
        plt.close(grid.figure)


@plot_app.command
def plot_group(
    hue: Literal['region', 'usage'],
    *,
    figsize: tuple[float, float] = (11, 12),
):
    root = get_root() / REPORT_DIR
    data = (
        pl
        .concat(
            (
                pl.scan_parquet(x, glob=False).select(
                    pl.lit(x.stem).alias('group'), pl.all()
                )
                for x in root.glob('*.parquet')
                if x.name.startswith('[민감도]')
            ),
            how='diagonal',
        )
        .filter(pl.col('x') != 'Intercept', pl.col('y') == '1차소요량')
        .with_columns(
            pl.col('group').str.extract(' (group=)?(.*)$', group_index=2),
            pl.col('usage').replace({
                '교육': '교육연구시설',
                '근린': '제1종근린생활시설',
                '업무': '업무시설',
            }),
            pl.col('x').replace_strict(SENSITIVITY_VARS),
        )
        .reverse()
        .collect()
    )

    if hue == 'region':
        kwargs = {'palette': 'Blues', 'hue_order': ['남부', '중부2', '전체']}
        plot_data = data.filter(pl.col('group').is_in(['전체', '지역'])).with_columns(
            pl.col('region').fill_null('전체')
        )
    else:
        kwargs = {
            'palette': 'Greens',
            'hue_order': ['제1종근린생활시설', '업무시설', '교육연구시설'],
        }
        plot_data = data.filter(pl.col('group') == '용도')

    utils.mpl.MplTheme(context='paper').grid(alpha=0.5).apply()
    figsize_inches = (figsize[0] / 2.54, figsize[1] / 2.54)
    fig, ax = plt.subplots(figsize=figsize_inches)
    sns.barplot(data=plot_data, x='beta', y='x', hue=hue, ax=ax, **kwargs)
    sns.utils.despine(ax=ax, top=True, right=True, left=True, bottom=True)

    if legend := ax.get_legend():
        legend.set_title('')
        legend.set_loc('lower left')

    for container in ax.containers:
        ax.bar_label(
            container,  # type: ignore[arg-type]
            fmt=lambda x: f'{x:.3f}',
            padding=5,
            size='small',
            c='0.25',
        )

    ax.set_xmargin(0.25)
    ax.axvline(x=0, c='0.5', lw=0.8)
    ax.set_xlabel('')
    ax.set_ylabel('')

    fig.savefig(root / f'민감도-{hue}.png')


if __name__ == '__main__':
    utils.mpl.MplTheme().grid(alpha=0.5).apply({'legend.fontsize': 'small'})
    utils.terminal.LogHandler()

    app()
