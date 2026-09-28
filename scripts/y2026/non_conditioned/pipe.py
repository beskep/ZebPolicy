"""2026-09-28 냉난방설비 미설치 평가기준 - 난방 히트펌프 배관 길이 기술통계."""

import contextlib
import functools
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import cyclopts
import fastexcel
import matplotlib.pyplot as plt
import more_itertools as mi
import polars as pl
import seaborn as sns
import statsmodels.api as sm
import structlog
import xlsxwriter
from tqdm.rich import tqdm

# 대상: 전기 히트펌프
# 설비벌 용량, 적용면적, 배관길이 추출


logger = structlog.stdlib.get_logger()
app = cyclopts.App(
    config=cyclopts.config.Toml(
        'env.toml', root_keys=['non-conditioned', 'pipe'], use_commands_as_keys=False
    ),
    result_action=['call_if_callable', 'print_non_int_sys_exit'],
)


@dataclass
class _SelectedColumns:
    selected: list[fastexcel.ColumnInfo]

    def absolute_indices(self):
        return [x.absolute_index for x in self.selected]

    def get_name(self, index: int):
        col = mi.only(x for x in self.selected if x.absolute_index == index)
        return None if (col is None or '__UNNAMED' in col.name) else col.name


@dataclass
class _MultiHeaderSheet:
    reader: fastexcel.ExcelReader
    sheet: str
    header_rows: Sequence[int]
    max_column: int | None = None

    @functools.cached_property
    def use_columns(self):
        return None if self.max_column is None else list(range(self.max_column))

    @functools.cached_property
    def selected(self):
        return tuple(
            _SelectedColumns(
                self.reader.load_sheet_by_name(
                    self.sheet, header_row=x, use_columns=self.use_columns
                ).selected_columns
            )
            for x in self.header_rows
        )

    @functools.cached_property
    def absolute_indices(self):
        return sorted(set(mi.flatten(x.absolute_indices() for x in self.selected)))

    def _column_names(self):
        for idx in self.absolute_indices:
            names = [s.get_name(idx) for s in self.selected]
            yield '.'.join(x for x in names if x)

    @functools.cached_property
    def column_names(self):
        return [(x or f'column{i:02d}') for i, x in enumerate(self._column_names())]

    def read(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.reader.load_sheet_by_name(
                self.sheet,
                skip_rows=max(self.header_rows) + 1,
                column_names=self.column_names,
                use_columns=self.absolute_indices,
            )


@dataclass
class Result:
    stem: str
    hp: pl.DataFrame
    zone: pl.DataFrame
    pipe: pl.DataFrame


@dataclass
class Reader:
    src: str | Path

    @functools.cached_property
    def reader(self):
        return fastexcel.read_excel(self.src)

    def _check_columns(
        self,
        sheet: str,
        header_row: int,
        columns: Sequence[str] | Mapping[int, str],
    ):
        if not isinstance(columns, Mapping):
            columns = dict(enumerate(columns))

        info = (
            (self.reader)
            .load_sheet_by_name(sheet, header_row=header_row, n_rows=0)
            .available_columns()
        )

        for idx, col in columns.items():
            if info[idx].name != col:
                raise ValueError(idx, col, info[idx])

    def heating_heat_pump(self):
        return (
            _MultiHeaderSheet(self.reader, sheet='난방기기', header_rows=[2, 3])
            .read()
            .to_polars()
            .filter(
                pl.col('열생산기기의방식') == '히트펌프',
                pl.col('히트펌프.사용연료2') == '전기',
            )
        )

    def zone(self, sheet='+입력존, 장비링크', max_column: int = 12):
        return (
            _MultiHeaderSheet(
                self.reader,
                sheet=sheet,
                header_rows=[2, 3],
                max_column=max_column,
            )
            .read()
            .to_polars()
            .drop_nulls(pl.first())
        )

    def pipe(self, sheet='배관길이_비주거'):
        pipe = ('배관길이', '최대배관길이', '표준비난방존', '외부')
        with contextlib.redirect_stdout(io.StringIO()):
            self._check_columns(
                sheet,
                header_row=2,
                columns=('구분', '난방기기', *pipe),
            )
            return (
                (self.reader)
                .load_sheet_by_name(sheet, header_row=2, use_columns=list(range(1, 7)))
                .to_polars()
                .with_columns(pl.col(pipe).cast(pl.Float64, strict=False))
            )

    def __call__(self):
        key = '난방기기'
        stem = Path(self.src).stem
        c = pl.lit(stem).alias(name='case')

        hp = (
            self
            .heating_heat_pump()
            .rename(lambda x: f'HP:{x}')
            .rename({'HP:난방기기이름.이름입력': key})
        )

        try:
            zone = self.zone()
        except fastexcel.SheetNotFoundError:
            zone = self.zone('+입력존')

        try:
            pipe = self.pipe()
        except fastexcel.SheetNotFoundError:
            pipe = self.pipe('배관길이')

        pipe = pipe.rename(lambda x: f'pipe:{x}').rename({f'pipe:{key}': key})
        area = zone.group_by(key).agg(pl.sum('면적.(㎡)').alias('zone:난방면적'))

        return Result(
            stem=stem,
            hp=(
                hp
                .join(area.drop_nulls(key), on=key, how='left', validate='1:m')
                .join(pipe.drop_nulls(key), on=key, how='left', validate='1:m')
                .select(c, pl.all())
            ),
            zone=zone.select(c, pl.all()),
            pipe=pipe.select(c, pl.all()),
        )


def _extract(src: Path):
    logger.info(src.name)

    try:
        d = Reader(src)()
    except ValueError, fastexcel.CalamineError:
        logger.exception('xlsm error')
    else:
        return d


@app.command
def parse(root: Path, src: Path | None, dst: Path | None = None):
    src = src or root / '01.sheets'
    sheets = list(src.glob('*.xlsm'))

    results = [_extract(s) for s in tqdm(sheets)]
    results = [x for x in results if x is not None]

    logger.info('count', sheets=len(sheets), results=len(results))

    hp = pl.concat([x.hp for x in results], how='diagonal_relaxed')
    zone = pl.concat([x.zone for x in results], how='diagonal_relaxed')
    pipe = pl.concat([x.pipe for x in results], how='diagonal_relaxed')

    dst = (dst or root) / 'HP.parquet'
    hp.write_parquet(dst)

    with xlsxwriter.Workbook(dst.with_suffix('.xlsx')) as wb:
        hp.write_excel(wb, worksheet='HP')
        zone.write_excel(wb, worksheet='zone')
        pipe.write_excel(wb, worksheet='pipe')


def _marginal_boxplot(a, *, vertical: bool = False, **kwargs):
    kwargs.setdefault('fill', False)
    kwargs.setdefault('color', '0.3')
    kwargs.setdefault('linewidth', 0.8)
    kwargs.setdefault('flierprops', {'alpha': 0.5})

    if vertical:
        sns.boxplot(y=a, **kwargs)
    else:
        sns.boxplot(x=a, **kwargs)


@app.command
@dataclass
class Eda:
    root: Path
    dst: Path = Path('02.EDA')
    max_capacity: float = 150

    @functools.cached_property
    def data(self):
        variables = {
            'HP:난방용량[kW]': '난방용량',
            'pipe:최대배관길이': '최대배관길이',
            'pipe:표준비난방존': '표준비난방존',
            'pipe:외부': '외부',
        }
        return (
            pl
            .scan_parquet(self.root / 'HP.parquet')
            .rename(variables)
            .select(variables.values())
            .filter(pl.col('난방용량') <= self.max_capacity)
            .collect()
        )

    @functools.cached_property
    def output(self):
        d = self.root / self.dst
        d.mkdir(exist_ok=True)
        return d

    def _lm(self, v: str):
        data = self.data.select(v, '난방용량').drop_nulls()
        summary = (
            sm
            .OLS(
                endog=data[v].to_numpy(),
                exog=data.select(pl.lit(1).alias('const'), '난방용량').to_numpy(),
            )
            .fit()
            .summary2(xname=['const', 'capacity'])
            .as_text()
        )
        self.output.joinpath(f'{v}.OLS.txt').write_text(summary, encoding='UTF-8')

    def _joint_plot(self, v: str):
        grid = (
            sns
            .JointGrid(self.data, x='난방용량', y=v, height=4, ratio=9)
            .plot_joint(
                sns.regplot,
                scatter_kws={'alpha': 0.25, 's': 5},
                line_kws={'alpha': 0.8},
            )
            .plot_marginals(_marginal_boxplot, data=self.data)
            .set_axis_labels('난방용량 [kW]', f'{v} [m]')
        )

        sns.utils.despine(
            ax=grid.ax_joint, top=False, right=False, left=False, bottom=False
        )
        grid.ax_marg_x.set_axis_off()
        grid.ax_marg_y.set_axis_off()

        grid.savefig(self.output / f'{v}.png')
        plt.close('all')

    def __call__(self):
        pipe = ('최대배관길이', '표준비난방존', '외부')

        (
            self.data
            .with_columns(
                pl
                .col(pipe)
                .truediv(pl.col('난방용량'))
                .name.suffix('/난방용량 [m/kW]'),
            )
            .rename({'난방용량': '난방용량 [kW]', **{x: f'{x} [m]' for x in pipe}})
            .describe(percentiles=(0.1, 0.25, 0.5, 0.75, 0.9), interpolation='linear')
            .write_csv(self.output / 'describe.csv', include_bom=True)
        )

        plt.style.use('custom.mplstyle')

        for v in pipe:
            self._lm(v)
            self._joint_plot(v=v)


if __name__ == '__main__':
    app()
