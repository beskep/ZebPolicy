"""
2024-07-18.

ZEB 인증 확대에 따른 에너지·탄소 절감 성과 산출 케이스 분석
Case A: 등급용 1차에너지소요량 유리
Case B: 에너지자립률 유리
"""

from __future__ import annotations

import math
import string
import tomllib
from collections import ChainMap
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import matplotlib.pyplot as plt
import msgspec
import numpy as np
import polars as pl
import polars.selectors as cs
import seaborn as sns
from matplotlib.ticker import PercentFormatter
from more_itertools import always_iterable

from zeb.utils.cli import App

if TYPE_CHECKING:
    from matplotlib.axes import Axes

StrPath = str | Path
Paths = StrPath | Iterable[StrPath]


class Case(msgspec.Struct):
    energy: tuple[float, float]
    ssr: tuple[float, float]


class Config(msgspec.Struct):
    root: Path
    cases: tuple[Case, ...]

    destination_name: str = '등급기준'

    @staticmethod
    def _dec_hook(t: type, obj):
        if t is Path:
            return Path(obj)
        return obj

    @classmethod
    def read(cls, path: Paths = ('data/config.toml', '.config.toml')):
        # 먼저 입력한 path 우선
        data = ChainMap(
            *(
                tomllib.loads(Path(p).read_text('UTF-8'))['zeb_rating']
                for p in always_iterable(path)
            )
        )
        root = Path(data['root'])

        def _kv():
            for key, value in data.items():
                if isinstance(value, dict) and 'root' in value:
                    v = value | {'root': root / value['root']}
                else:
                    v = value

                yield key, v

        return msgspec.convert(dict(_kv()), type=cls, dec_hook=cls._dec_hook)

    @property
    def analysis(self):
        return self.root / self.destination_name

    def iter_case(self):
        yield from zip(self.cases, string.ascii_uppercase, strict=False)

    @staticmethod
    def color(c: Literal['green', 'yellow'] = 'green'):
        return '#70ad47' if c == 'green' else '#ffc000'


conf = Config.read()
app = App()
_INCH = 2.54


def _extract_case(case: Case, target='비주거_예비인증'):
    def nan_or(v1: float, v2: float):
        return v2 if math.isnan(v1) else v1

    e = '1차에너지소요량'
    eg = '등급용 1차에너지소요량'

    ssr = (
        pl
        .scan_parquet(conf.root / '자립률-소요량.parquet')
        .filter(
            pl.col(eg) >= nan_or(case.energy[0], -np.inf),
            pl.col(eg) < nan_or(case.energy[1], np.inf),
            pl.col('자립률') >= nan_or(case.ssr[0] * 100, -np.inf),
            pl.col('자립률') <= nan_or(case.ssr[1] * 100, np.inf),
        )
        .select(
            '신청번호',
            pl.col(e),
            pl.col(eg),
            '자립률',
        )
        .with_columns((pl.col(e) / pl.col(eg)).alias('1차/등급용1차 비율'))
        .collect()
    )

    return ssr.join(
        pl
        .scan_parquet(conf.root / f'{target}.parquet')
        .filter(pl.col('진행상태') == '인증서발급')
        .collect(),
        on='신청번호',
        how='left',
    )


@app.command
def extract_case():
    # columns:
    #   '연면적',
    #   '1차/등급용1차 비율',
    #   '1차에너지소요량',
    #   '등급용 1차에너지소요량',
    #   '자립률',
    #   # categorical
    #   '등급',
    #   'ZEB등급',
    #   '건물용도',
    #   '건물주용도',
    #   '신청지역',
    #   '주체',

    dst = conf.analysis
    dst.mkdir(exist_ok=True)

    for case, code in zip(conf.cases, string.ascii_uppercase, strict=False):
        name = f'Case{code}'
        print(name)

        df = _extract_case(case).sort('신청번호')
        df.write_parquet(dst / f'{name}.parquet')
        df.write_excel(dst / f'{name}.xlsx')


@app.command
def boxplot():
    threshold = 80
    df = pl.read_parquet(conf.root / '자립률-소요량.parquet').with_columns(
        pl
        .when(pl.col('등급용 1차에너지소요량') >= threshold)
        .then(pl.lit('효율등급 1++'))
        .otherwise(pl.lit('효율등급 1+++'))
        .alias('효율 등급'),
        pl.format('ZEB {}', 'ZEB 등급').alias('ZEB 등급'),
    )

    ax: Axes
    for v in ['효율 등급', 'ZEB 등급']:
        df = df.sort(v)
        fig, axes = plt.subplots(2, 1)
        assert isinstance(axes, np.ndarray)

        sns.boxplot(
            df,
            x='등급용 1차에너지소요량',
            y=v,
            ax=axes[0],
            color=conf.color('green'),
        )
        sns.boxplot(
            df,
            x='자립률',
            y=v,
            ax=axes[1],
            color=conf.color('yellow'),
        )
        for ax in axes:
            ax.set_ylabel('')

        if (engine := fig.get_layout_engine()) is not None:
            engine.set(hspace=0.1)  # type: ignore[call-arg]

        for w in range(12, 18, 2):
            fig.set_size_inches(w / _INCH, 9.0 / _INCH)
            fig.savefig(conf.root / f'등급기준/boxplot-{v}-width{w}.png')
            plt.close(fig)


def _profile_area_ratio(*, path: Path, area_avg: bool = False):
    df = pl.read_parquet(path, glob=False)

    area_col = (
        df[:, '소규모사무실_면적':'체육시설_비중'].select(cs.ends_with('_면적')).columns  # type: ignore[index, misc]
    )
    area = (
        df
        .select('신청번호', '1차/등급용1차 비율', *area_col)
        .unpivot(index=['신청번호', '1차/등급용1차 비율'], value_name='면적')
        .with_columns(pl.col('면적').cast(pl.Float64, strict=False))
        .filter(pl.col('면적') != 0)
        .sort('1차/등급용1차 비율', '신청번호')
        .with_columns(pl.col('variable').str.strip_suffix('_면적'))
    )

    if area_avg:
        area_ratio = (
            area
            .group_by('variable')
            .agg(pl.sum('면적'))
            .with_columns((pl.col('면적') / pl.sum('면적').over('len')).alias('면적비'))
            .sort(pl.all())
        )
    else:
        area_ratio = (
            area
            .with_columns(
                (pl.col('면적') / pl.sum('면적').over('신청번호')).alias('면적비')
            )
            .group_by('variable')
            .agg(pl.mean('면적', '면적비'))
        )

    return area_ratio


def _profile_area_ratio_plot(
    df: pl.DataFrame, height=8, width: Iterable[float] | None = None
):
    df = df.sort('면적비', descending=True)
    width = width or range(16, 30, 2)

    for w in width:
        fig, ax = plt.subplots(figsize=(w / _INCH, height / _INCH))

        sns.barplot(
            df,
            x='variable',
            y='면적비',
            color=conf.color(),
        )

        ax.set_xlabel('')
        ax.set_ylabel('면적 비율')
        ax.set_xticks(
            ax.get_xticks(),
            ax.get_xticklabels(),  # type: ignore[arg-type]
            rotation=45,
            ha='right',
            rotation_mode='anchor',
        )
        ax.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))

        ax.bar_label(
            ax.containers[0],  # type: ignore[arg-type]
            df.select(
                pl.format('{}%', pl.col('면적비').mul(100).round().cast(pl.Int16))
            ).to_series(),
            padding=2,
            fontsize='x-small',
        )
        ax.margins(x=0.02, y=0.1)

        yield fig, w


@app.command
def profile(
    *,
    area_avg: bool = False,
    height=8,
    width: list[float] | None = None,
):
    stat = '면적비(전체)' if area_avg else '면적비(건물별 면적비 평균)'

    for _, code in conf.iter_case():
        src = conf.analysis / f'Case{code}.parquet'
        area = _profile_area_ratio(path=src, area_avg=area_avg)
        area.write_excel(conf.analysis / f'Case{code}_{stat}.xlsx', autofit=True)

        for fig, w in _profile_area_ratio_plot(area, height=height, width=width):
            fig.savefig(conf.analysis / f'barplot_Case{code}_{stat}_w{w}h{height}.png')
            plt.close(fig)


if __name__ == '__main__':
    from zeb import utils

    utils.mpl.MplTheme().grid().apply()
    app()
