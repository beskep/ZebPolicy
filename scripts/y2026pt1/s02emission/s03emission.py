import functools
from dataclasses import KW_ONLY, dataclass
from itertools import chain
from typing import ClassVar
lazy from collections.abc import Sequence
lazy from pathlib import Path

import eco2.report
import polars as pl
import polars.selectors as cs
import structlog
from cyclopts.config import Toml
from tqdm.rich import tqdm

import zeb.emission
import zeb.y2026.common as comm
from zeb import utils
from zeb.cli import App
from zeb.y2026.common import INDEX, Grade
lazy from zeb.y2026.config import Paths

app = App(
    config=Toml(
        'env.toml', root_keys='2026', allow_unknown=True, use_commands_as_keys=False
    )
)
logger = structlog.stdlib.get_logger()


def _read(src: Path):
    logger.debug(src.as_posix())

    s = src.as_posix()
    if 'existing' in s:
        electrification = '기존'
    elif 'all-electric' in s:
        electrification = '전환'
    else:
        raise ValueError(s)

    try:
        report = eco2.report.BatchReport(src, encoding='utf8').raw
    except UnicodeError, pl.exceptions.ComputeError:
        report = eco2.report.BatchReport(src, encoding='korean').raw

    logger.debug(
        'shape=%s, src=%s, electrification=%s', report.shape, src, electrification
    )

    return report.select(
        pl.lit(src.as_posix()).alias('src'),
        pl.lit(electrification).alias('electrification'),
        pl.all(),
    )


@app.command
def read_raw(*, paths: Paths, batchreport: str = 'batchreport.tab'):
    subdirs = [paths.eco2raw.existing, paths.eco2raw.all_electric]

    # 계산 결과가 없는 폴더 체크
    for d in chain.from_iterable(x.glob('**/') for x in subdirs):
        if not (d / batchreport).exists():
            logger.warning('%s not found: %s', batchreport, d)

    src = list(chain.from_iterable(x.rglob(batchreport) for x in subdirs))
    reports = (_read(x) for x in tqdm(src))
    data = pl.concat(reports, how='vertical_relaxed')

    root = paths.eco2.root
    data.write_parquet(root / '00.raw.parquet')
    data.write_csv(root / '00.raw.csv', include_bom=True)
    root.joinpath('00.raw.glimpse.txt').write_text(data.glimpse(return_type='string'))

    return data


@dataclass
class _Command:
    _: KW_ONLY
    paths: Paths


@app.command
@dataclass
class Parse(_Command):
    INDEX: ClassVar[tuple[str, ...]] = INDEX.CASE
    AREA_FIX: ClassVar[dict[str, str]] = {
        '1878.42 // 1549.68': '1549.68',
        '31031.96 // 19726.98': '19726.98',
        '19017.02 (전:9337.64 326세대)': '19017.02',
        '32928.31 (용:16505.68': '16505.68',
        '35542.73 (용22774.09)': '22774.09',
        '142.841.7416': '142841.7416',
        '135415.55(820세대)': '135415.55',
    }

    def __call__(self):
        root = self.paths.eco2.root
        area = ('대지면적', '연면적', '건축면적')
        data = (
            pl
            .scan_parquet(root / '00.raw.parquet')
            .with_columns(
                pl.col('file').str.extract_groups(comm.Case.PATTERN).alias('group')
            )
            .unnest('group')
            .rename({'scale_a': 'scale.a', 'scale_c': 'scale.c'})
            .with_columns(
                pl.format(
                    '{}.{}.{}.{}.{}',
                    'owner',
                    'scale.a',
                    pl.col('scale.c').fill_null('N'),
                    'purpose',
                    'index',
                ).alias('bldg'),
                pl.col('electrification').cast(pl.Enum(comm.ELECTRIFICATION)),
                pl
                .col('owner')
                .replace_strict({'공': '공공', '민': '민간'})
                .cast(pl.Enum(comm.OWNER)),
                pl
                .col('purpose')
                .replace_strict({
                    '기': '기타',
                    '상': '상업',
                    '교': '교육사회',
                    '공': '공동주택',
                    '단': '단독주택',
                })
                .cast(pl.Enum(comm.PURPOSE)),
                pl.col('region').cast(pl.Enum(comm.REGIONS)),
                pl.col('index').cast(pl.UInt8),
                pl.col(area).cast(pl.String).name.suffix('_원본'),
                pl
                .col(area)
                .str.strip_chars()
                .str.replace_all(',', '')
                .replace(self.AREA_FIX)
                .cast(pl.Float64),
            )
            .select('src', *self.INDEX, pl.all().exclude([*self.INDEX, 'src']))
            .sort(pl.all())
            .collect()
        )

        owner = pl.col('owner')
        grade = pl.col('grade')

        baseline = (
            (data)
            .filter(
                (owner.eq('민간') & grade.eq(Grade.NOPV))
                | (owner.eq('공공') & grade.eq(Grade.BASE))
            )
            .with_columns(pl.lit(Grade.BASE).alias('grade'))
        )
        sub5 = (
            (data)
            .filter(grade == Grade.BASE)
            .with_columns(pl.lit(Grade.SUB5).alias('grade'))
        )
        if baseline.height != sub5.height:
            raise ValueError(baseline.shape, sub5.shape)

        zeb = data.filter(grade.is_in([Grade.BASE, Grade.SUB5, Grade.NOPV]).not_())
        data = (
            pl
            .concat([baseline, sub5, zeb])
            .with_columns(pl.col('grade').cast(pl.Enum([x.value for x in Grade])))
            .with_columns()
        )

        data.write_parquet(root / '01.parsed.parquet')
        data.write_excel(root / '01.parsed.xlsx')
        root.joinpath('01.parsed.glimpse.txt').write_text(
            data.glimpse(return_type='string')
        )

        return data


@app.command
@dataclass
class Emission(_Command):
    INDEX: ClassVar[tuple[str, ...]] = INDEX.CASE
    SOURCE: ClassVar[dict[str, str]] = {
        '난방유(등유)': '등유',
        '액화가스(LPG)': 'LPG',
        '열': '열',
        '전기': '전력',
        '전력': '전력',
        '지역난방연료': '지역난방',
        '지역난방열': '지역난방',
        '지역냉방열': '지역냉방',
        '천연가스(LNG)': 'LNG',
    }

    def __call__(self):
        root = self.paths.eco2.root
        emission_factors = zeb.emission.EmissionFactors.read().dataframe()

        fn = pl.col('function')
        src = pl.col('source')

        data = (
            pl
            .scan_parquet(root / '01.parsed.parquet')
            .select(
                *self.INDEX,
                cs.starts_with(
                    # unit kWh
                    '1차에너지소요량/',
                    '전기에너지 생산량',
                    '열에너지 생산량',
                ),
            )
            .unpivot(index=self.INDEX)
            .sort([*self.INDEX, 'variable'])
            .with_columns(
                pl
                .col('variable')
                .str.extract_groups(
                    r'^1차에너지소요량/'
                    r'(?P<function>냉방|난방|급탕|조명|환기)'
                    r'(?:/(?P<src1>[\w()]+))?'
                    r'|(?P<src2>전기|열)에너지 생산량'
                    r'\((?P<renewable>태양광|태양열|열병합|풍력|수열|지열)\)$'
                )
                .alias('groups')
            )
            .unnest('groups')
            .with_columns(
                function=pl
                .when(pl.col('src2').is_null())
                .then(fn)
                .otherwise(pl.lit('생산')),
                source=pl
                .when(pl.col('src1').is_null())
                .then(pl.col('src2'))
                .otherwise(pl.col('src1')),
            )
            .filter(src != '총량')
            .drop('src1', 'src2')
            .with_columns(
                (
                    pl.col('value')
                    * fn.replace_strict('생산', -1, default=1, return_dtype=pl.Float64)
                ).alias('value'),
                src.replace_strict(self.SOURCE),
            )
            .with_columns(
                pl
                .format('{}{}', fn.replace_strict('생산', '생산.', default=''), src)
                .replace_strict({
                    'LPG': 'direct',
                    'LNG': 'direct',
                    '등유': 'direct',
                    '전력': 'indirect',
                    '지역난방': 'indirect',
                    '지역냉방': 'indirect',
                    '생산.전력': 'generation.elec',
                    '생산.열': 'generation.heat',
                })
                .alias('scope')
            )
            .rename({'value': 'delivered'})  # kWh
            .join(emission_factors.lazy(), on='source', how='left', validate='m:m')
            .with_columns(
                pl
                .col('delivered')
                .mul(pl.col('emission_factor'))
                .alias('emission'),  # kg
            )
            .collect()
        )

        data.write_parquet(root / '02.emission.parquet')
        (
            root
            .joinpath()
            .joinpath('02.emission.glimpse.txt')
            .write_text(data.glimpse(return_type='string'), encoding='utf-8')
        )
        (
            data
            .filter(pl.col('bldg') == '공.A1.N.교.1')
            .with_columns()
            .write_csv(root / '02.emission.sample.csv', include_bom=True)
        )

        (
            (utils.pl)
            .PolarsSummary(data.rename({'variable': 'var'}))
            .write_excel(root / '02.emission.summary.xlsx')
        )

        return data


@app.command
@dataclass
class ChangeRate(_Command):
    """시나리오 평가를 위한 배출량 변화율 평가."""

    # TODO: '공.A2.N.교.2' 포함 여부 결정

    reference: str = 'ECO2'

    # TODO: owner 포함한 물량 통계 이용 시 owner = True
    owner: bool = False

    @property
    def output(self):
        return self.paths.eco2.root

    @functools.cached_property
    def emission(self):
        c = (
            pl.format('{}-{}-{}', 'purpose', 'scale', 'owner')
            if self.owner
            else pl.format('{}-{}', 'purpose', 'scale')
        )
        gfa = pl.col('연면적')
        emission = pl.read_parquet(self.paths.eco2.root / '02.emission.parquet')

        assert (
            emission
            .filter(pl.col('scope') == 'generation.heat')
            .select((pl.col('emission') == 0).all())
            .item()
        )

        return (
            emission
            .filter(
                pl.col('reference') == self.reference,
                pl.col('grade').is_in([Grade.NOPV, Grade.EXST]).not_(),
                pl.col('scope') != 'generation.heat',
            )
            .with_columns(
                pl.col('scope').replace({'generation.elec': 'PV'}),
                pl.col('delivered').truediv(gfa).alias('delivered'),  # kWh/m²
                pl.col('emission').truediv(gfa).alias('emission'),  # kg/m²
            )
            .group_by([*INDEX.CASE, 'source', 'scope'])
            .agg(pl.sum('delivered', 'emission'))
            .with_columns(
                pl
                .when(pl.col('purpose').is_in(['공동주택', '단독주택']))
                .then(pl.col('scale.c'))
                .otherwise(pl.col('scale.a'))
                .alias('scale'),
            )
            .with_columns(c.alias('case'))
        )

    @staticmethod
    def _baseline(v: str, group: Sequence[str]):
        return pl.col(v).filter(pl.col('grade') == Grade.BASE).sum().over(group)

    @functools.cached_property
    def rate(self):
        variables = [
            'delivered',
            'emission',
            'delivered.baseline',
            'emission.baseline',
            'emission.baseline_scope',
        ]

        # NOTE: 전력 소요량과 PV 발전량 모두 합한 값을 분모로 둠
        # 즉, baseline 계산 시 scope 부호(indirect, PV)를 구분하지 않음
        group = [*INDEX.BUILDING, 'source']

        return (
            self.emission
            .with_columns(
                # TODO: 전환 모델 비교 대상 변경
                self._baseline('delivered', group).alias('delivered.baseline'),
                self._baseline('emission', group).alias('emission.baseline'),
                # 전력 소요, 발전량 구분
                self._baseline('emission', [*group, 'scope']).alias(
                    'emission.baseline_scope'
                ),
            )
            .group_by('electrification', 'case', 'grade', 'source', 'scope')
            .agg(pl.col(variables).mean(), pl.col(variables).std().name.suffix(':std'))
            .with_columns(
                (pl.col('emission') - pl.col('emission.baseline_scope'))
                .truediv(pl.col('emission.baseline'))
                .fill_nan(0)
                .alias('emission_change_rate')
            )
            .sort(pl.all())
        )

    def __call__(self):
        cols = [
            'electrification',
            'case',
            'grade',
            'source',
            'scope',
            pl.col('delivered.baseline').alias('delivered_baseline__kWh/m2'),
            'emission_change_rate',
        ]
        self.rate.write_csv(self.output / '03.change-rate.csv', include_bom=True)
        self.rate.select(cols).write_csv(
            self.output / '03.change-rate_table.csv', include_bom=True
        )


if __name__ == '__main__':
    app()
