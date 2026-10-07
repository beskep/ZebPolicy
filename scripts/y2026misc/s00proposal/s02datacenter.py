# 2026-05-26 데이터센터 1차에너지소요량 비교

import dataclasses as dc
from datetime import date
from pathlib import Path

import cyclopts
import polars as pl
import polars.selectors as cs
import rich
import seaborn as sns
from matplotlib.figure import Figure

from zeb import utils

app = cyclopts.App(
    config=cyclopts.config.Toml(
        'config.toml',
        root_keys=('2026', 'datacenter'),
        use_commands_as_keys=False,
    )
)


@cyclopts.Parameter(name='*')
@dc.dataclass
class Config:
    root: Path
    src: Path
    datacenters: tuple[str, ...]
    ws: Path = Path('2026')
    gfa_min: float = 20000
    gfa_max: float = 60000
    year_min: int = 2019
    metropolitan: tuple[str, ...] = ('서울', '경기', '인천')

    def __post_init__(self):
        self.ws = self.root / self.ws


@app.command
def prep(conf: Config):
    data = pl.read_excel(conf.root / conf.src, sheet_id=0)

    conf.ws.mkdir(exist_ok=True)

    for sheet, df in data.items():
        df.write_parquet(conf.ws / f'00.{sheet}.parquet')
        conf.ws.joinpath(f'01.glimpse_{sheet}.txt').write_text(
            df.glimpse(return_type='string')
        )


@app.command
def pair(conf: Config):
    name = '건축물명'
    data = (
        pl
        .scan_parquet(conf.ws / '00.주거용이외.parquet')
        .select(
            name,
            '연면적',
            '전산실_비중',
            '1차에너지소요량_합계',
            '1차에너지소요량_냉방',
        )
        .with_columns(cs.string().exclude(name).replace('', None).cast(pl.Float64))
        .collect()
    )

    rich.print(data)

    utils.mpl.MplTheme().grid().apply()
    sns.pairplot(data.drop(name).to_pandas()).savefig(conf.ws / '02.pairplot.png')
    (
        sns
        .pairplot(data.filter(pl.col(name).is_in(conf.datacenters)).to_pandas())
        .set()
        .savefig(conf.ws / '02.pairplot-datacenter.png')
    )


@app.command
def xlsx(conf: Config):
    data = (
        pl
        .read_parquet(conf.ws / '00.주거용이외.parquet')
        .with_columns(pl.col('전산실_비중').cast(pl.Float64, strict=False))
        .sort('전산실_비중')
    )
    data.write_excel(conf.ws / '03.주거용이외.xlsx')


@app.command
def main(conf: Config, *, metropolitan: bool = True):
    data = (
        pl
        .scan_parquet(conf.ws / '00.주거용이외.parquet')
        .unique('신청번호')
        .with_columns(
            pl.col('연면적').cast(pl.Float64, strict=False),
            cs.starts_with('전산실', '1차에너지소요량'),
            pl.col('인증신청일').str.strip_chars().str.to_date(),
            pl.col('신청지역').str.strip_chars(),
        )
        .filter(
            pl.col('연면적') >= conf.gfa_min,
            pl.col('연면적') <= conf.gfa_max,
            pl.col('인증신청일') >= date(conf.year_min, 1, 1),
        )
        .collect()
        .insert_column(
            0,
            pl
            .col('건축물명')
            .is_in(conf.datacenters)
            .replace_strict({False: '그 외', True: '데이터센터'})
            .alias('type'),
        )
    )

    if metropolitan:
        data = data.filter(pl.col('신청지역').is_in(conf.metropolitan))

    on = [
        f'{p}1차에너지소요량_{f}'
        for f in ['난방', '냉방', '급탕', '조명', '환기', '합계']
        for p in ['', '등급산출_']
    ]
    unpivot = (
        data
        .unpivot(on, index=['신청번호', 'type'], variable_name='v')
        .with_columns(
            pl
            .col('v')
            .str.starts_with('등급산출')
            .replace_strict({True: '등급용1차소요량', False: '1차소요량'})
            .alias('variable'),
            pl
            .col('v')
            .str.strip_prefix('등급산출_')
            .str.strip_prefix('1차에너지소요량_')
            .alias('function'),
        )
        .with_columns(pl.col('value').cast(pl.Float64))
        .drop('v')
    )

    rich.print(unpivot)

    data.write_excel(conf.ws / '04.비교대상.xlsx')
    (
        unpivot
        .group_by('type', 'variable', 'function')
        .agg(pl.mean('value').alias('avg'), pl.std('value').alias('std'))
        .sort(pl.all())
        .write_excel(conf.ws / '04.average.xlsx')
    )

    (
        utils.mpl
        .MplTheme(palette='tol:light-alt')
        .grid()
        .apply({'lines.solid_capstyle': 'butt'})
    )
    kwargs = {
        'x': 'value',
        'y': 'function',
        'linewidth': 0,
        'edgecolor': 'gray',
        'capsize': 0.3,
        'err_kws': {'lw': 0.8},
    }

    fig = Figure()
    ax = fig.subplots()
    sns.barplot(
        unpivot.with_columns(hue=pl.format('{} / {}', 'type', 'variable')),
        hue='hue',
        hue_order=[
            '데이터센터 / 1차소요량',
            '데이터센터 / 등급용1차소요량',
            '그 외 / 1차소요량',
            '그 외 / 등급용1차소요량',
        ],
        ax=ax,
        palette='Paired',
        **kwargs,
    )
    ax.set_xlabel('1차에너지소요량 [kWh/m²]')
    ax.set_ylabel('')
    ax.legend(title='')
    fig.savefig(conf.ws / '04.bar.png')

    for variable in ['1차소요량', '등급용1차소요량']:
        fig = Figure()
        ax = fig.subplots()
        sns.barplot(
            unpivot.filter(pl.col('variable') == variable),
            hue='type',
            hue_order=['데이터센터', '그 외'],
            ax=ax,
            **kwargs,
        )
        prefix = '등급용 ' if variable.startswith('등급용') else ''
        ax.set_xlabel(f'{prefix}1차에너지소요량 [kWh/m²]')
        ax.set_ylabel('')
        ax.legend(title='')
        fig.savefig(conf.ws / f'04.bar-{variable}.png')


if __name__ == '__main__':
    app()
