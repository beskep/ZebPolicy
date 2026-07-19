"""온실가스 배출량 평가."""

import bisect
import dataclasses as dc
import functools
import re
from pathlib import Path  # ruff:ignore[typing-only-standard-library-import]
from typing import ClassVar

import cyclopts
import polars as pl
import polars.selectors as cs
import rich
import xlsxwriter
from cyclopts.types import (
    ExistingDirectory,  # ruff:ignore[typing-only-third-party-import]
)
from loguru import logger

from scripts.y2025.common import Grade
from zeb import utils
from zeb.emission import EmissionFactors
from zeb.utils.cli import App

app = App(
    config=[
        cyclopts.config.Toml(
            'config.toml',
            root_keys='emission',
            use_commands_as_keys=x,
            allow_unknown=True,
        )
        for x in [False, True]
    ],
    result_action=['call_if_callable', 'print_non_int_sys_exit'],
)


@dc.dataclass(frozen=True)
class RowInfo:
    """'계산결과' 파일 행별 정보."""

    row: tuple[int, ...] = (41, 44, 51, 59, 66, 69, 72, 75, 79, 83)
    category: tuple[str | None, ...] = (
        None,
        '전체',
        '난방',
        '냉방',
        '급탕',
        '조명',
        '환기',
        '생산-전력',
        '생산-열',
        '생산-면적당열',
        None,
    )

    def bisect(self, row: int):
        index = bisect.bisect(self.row, row)
        return self.category[index]

    @functools.cached_property
    def remap(self) -> dict[int, str | None]:
        """
        1차에너지 소요량 정보.

        Returns
        -------
        dict[int, str | None]
        """
        return {x: self.bisect(x) for x in range(min(self.row), max(self.row))}


@app.command
@dc.dataclass
class EvaluateEmission:
    """유형별 소요량, 배출량 평가."""

    src: ExistingDirectory
    dst: ExistingDirectory | None = None

    sample_rows: int = 100

    factor: EmissionFactors = dc.field(default_factory=EmissionFactors.read)
    row_info: RowInfo = dc.field(default_factory=RowInfo)

    INDEX: ClassVar[tuple[str, ...]] = (
        'building-use',
        'ownership',
        'scale',
        'scale1',
        'scale2',
        'use',
        'bldg',
        'region',
        'grade',
        'emission_reference',
    )
    SOURCE_REMAP: ClassVar[dict[str, str]] = {
        '천연가스(LNG)': 'LNG',
        '액화가스(LPG)': 'LPG',
        '난방유(등유)': '등유',
        '지역난방': '지역난방',  # '지역난방열료 소요량' ㅋ
        '지역난방열': '지역난방',
        '지역냉방열': '지역냉방',
        '전력': '전력',
        '전기에너지': '전력',  # 발전량
        '열에너지': '열',  # 발전량
    }
    SCOPE: ClassVar[dict[str, str]] = {
        'LNG': 'direct',
        'LPG': 'direct',
        '등유': 'direct',
        '지역난방': 'indirect',
        '지역냉방': 'indirect',
        '전력': 'indirect',
        '열': 'indirect',
    }

    @functools.cached_property
    def _emission_factor(self):
        return self.factor.dataframe().rename({'reference': 'emission_reference'})

    def evaluate(self, src: Path):
        group = pl.col('group')
        var = pl.col('variable')

        function = (
            pl
            .when(group.str.starts_with('그래프'))
            .then(
                # '그래프' 파일
                group.str.extract('(난방|냉방|급탕|조명|환기|신재생|합계)')
            )
            .otherwise(
                # '계산결과' 파일
                pl.col('index').replace_strict(self.row_info.remap, default=None)
            )
        )
        source = (
            pl
            .when((group == '1차에너지 소요량') & var.str.contains('1차에너지').not_())
            .then(
                var.str
                .strip_suffix(' 소요량')
                .str.replace('열료', '')  # ㅋㅋㅋㅋ
                .str.extract(r'^(.*?)( 생산량\(\w+\))?$')
            )
            .otherwise(pl.lit(None))
            .replace_strict(self.SOURCE_REMAP, default=None)
        )

        data = (
            pl
            .scan_parquet(src)
            .filter(
                group.fill_null('etc').is_in(['etc', '1차에너지 소요량'])
                | group.str.starts_with('그래프')
                | (var == '사용면적(조명)')
            )
            .with_columns(
                pl.col('기호&계수').cast(pl.Float64, strict=False).alias('계수'),
                function=function,
                source=source,
            )
            .with_columns(scope=pl.col('source').replace_strict(self.SCOPE))
            .with_columns(
                # 소요량 [kWh] (생산량은 음수)
                requirement=pl.col('value')
                * pl
                .col('function')
                .str.contains('생산')
                .replace_strict({True: -1, False: 1}),
            )
        )

        data = pl.concat([
            data.with_columns(pl.lit('ECO2').alias('emission_reference')),
            data.with_columns(pl.lit('reference').alias('emission_reference')),
        ])
        return (
            data
            .join(
                self._emission_factor.lazy(),
                on=['emission_reference', 'source'],
                how='left',
                validate='m:1',
            )
            .with_columns(
                # 탄소 배출량 [kg]
                emission=(pl.col('requirement') * pl.col('emission_factor'))
            )
            .drop('pv', 'index', '기호&계수')
        )

    def _iter_data(self):
        p = re.compile(r'^00.(?P<use>(non-)?residential)-(?P<pv>\w+)$')

        for src in self.src.glob('00.*.parquet'):
            if (m := p.match(src.stem)) is None:
                raise ValueError(src)

            use = m.group('use')
            pv = m.group('pv')
            no_pv = 'zero' in pv
            logger.info('use={} | NoPV={} | src={}', use, no_pv, src)

            yield (
                self
                .evaluate(src)
                .select(
                    pl.lit(use).alias('building-use'),
                    pl.lit(not no_pv).alias('pv-installed'),
                    pl.all(),
                )
                .with_columns(pl.col('grade').replace({'Base': Grade.BASE}))
            )

    def _prep(self):
        ownership = pl.col('ownership')
        grade = pl.col('grade')
        pv = pl.col('pv-installed')

        data = (
            pl
            .concat(self._iter_data())
            .with_columns(
                ownership.replace_strict({'공': '공공', '민': '민간'}),
                pl.col('use').replace_strict({
                    x[0]: x for x in ['교육사회', '상업', '기타', '공동', '단독']
                }),
                pl.col('bldg').cast(pl.UInt8),
            )
            .collect()
        )

        # ZEB5-ZEB+ 등급 외 비교 케이스 설정
        # - Baseline: 민간 PV 제외 + 공공 자립률 13%
        # - Sub-ZEB5(준ZEB5): 민간, 공공 자립률 13%
        # (pv-install이 True인 Baseline 케이스는 자립률 13%)
        base = (
            (data)
            .filter(
                grade == Grade.BASE,
                (~pv & ownership.eq('민간')) | (pv & ownership.eq('공공')),
            )
            .with_columns(
                pl.lit(Grade.BASE).alias('grade'),
                pl.lit(value=True).alias('pv-installed'),
            )
        )
        sub5 = (
            (data)
            .filter(pv, grade == Grade.BASE)
            .with_columns(pl.lit(Grade.SUB5).alias('grade'))
        )
        zeb = data.filter(pv, grade.str.starts_with('ZEB'))
        no_pv = data.filter(~pv)  # 참고용 PV 제외 케이스

        assert base.height == sub5.height
        assert 2 * (base.height + zeb.height) == data.height

        return pl.concat([base, sub5, zeb, no_pv])

    def calculate_emission(self, data: pl.DataFrame):
        """케이스별 최종 소요량, 탄소배출량 산정."""
        var = pl.col('variable')
        fn = pl.col('function')
        value = pl.col('value')
        index = [*self.INDEX, 'pv-installed']
        data = data.with_columns(cs.starts_with('scale').fill_null('-'))

        # ('그래프' 파일) 연간 소요량 [kWh/m²]
        requirement = (
            (data)
            .filter(var == '소요량', fn == '합계')
            .select(*index, value.alias('소요량'))
        )

        # ('그래프' 파일) 연간 소비량 (소요량 합계 + |소요량 신재생|) [kWh/m²]
        consumption = (
            data
            .filter(var == '소요량', fn.is_in(['합계', '신재생']))
            .with_columns(
                pl
                .when(fn == '신재생')
                .then(value.abs())
                .otherwise(value)
                .alias('value')
            )
            .group_by(index)
            .agg(value.sum().alias('소비량'))
        )

        # ('계산결과' 파일) 탄소 배출량 [kg]
        emission = (
            data
            .filter(pl.col('group') == '1차에너지 소요량', fn != '생산-열')
            .drop_nulls('source')
            .with_columns(
                pl
                .when(fn == '생산-전력')
                .then(pl.lit('PV배출량'))
                .otherwise(
                    pl.col('scope').replace_strict({
                        'direct': '직접배출량',
                        'indirect': '간접배출량',
                    })
                )
                .alias('scope'),
            )
            .group_by([*index, 'scope'])
            .agg(pl.sum('emission'))
            .pivot('scope', index=index, values='emission', sort_columns=True)
            .with_columns(
                pl.sum_horizontal(
                    ['PV배출량', '직접배출량', '간접배출량'],
                ).alias('배출량')
            )
        )

        area_var = ['연면적', '사용면적(조명)', 'PV면적', 'BIPV면적', '외벽면적']
        area = (
            data
            .filter(var.is_in(area_var))
            .with_columns(var.replace({'사용면적(조명)': '조명면적'}))
            .unique()
            .pivot('variable', index=index, values='value', sort_columns=True)
        )

        return (
            requirement
            .join(consumption, on=index, how='full', coalesce=True, validate='1:1')
            .join(emission, on=index, how='full', coalesce=True, validate='1:1')
            .join(area, on=index, how='left', validate='1:1')
            .sort(index)
        )

    def write_summary(
        self,
        data: pl.DataFrame,
        path: str | Path,
        column_width: int = 100,
    ):
        """유형별 평균, 절감량 정리."""
        data = (
            (data)
            .filter(pl.col('pv-installed'))
            .with_columns(
                pl.col('use').replace({'교육사회': '교육사회용', '상업': '상업용'})
            )
            .with_columns(
                pl.col('building-use').replace_strict({
                    'residential': '주거',
                    'non-residential': '비주거',
                }),
                index=pl.format(
                    '{}-{}-{}-{}등급',
                    'ownership',
                    'use',
                    'scale',
                    pl.col('grade').str.replace('ZEB', ''),
                ),
            )
        )
        base_index = [
            x
            for x in self.INDEX
            if x not in {'bldg', 'region', 'grade', 'scale1', 'scale2'}
        ]
        index = [*base_index, 'grade', 'index']
        variables = ['소요량', '소비량', '배출량']

        # 연면적당
        normalized = (
            data
            .with_columns([(pl.col(x) / pl.col('연면적')).alias(x) for x in variables])
            .group_by(index)
            .agg(pl.mean(*variables))
            .sort(pl.all())
        )

        unpivot = normalized.unpivot(variables, index=index)
        base = (
            unpivot
            .filter(pl.col('grade') == 'Baseline')
            .drop('grade', 'index')
            .rename({'value': 'baseline'})
        )
        ratio = (
            unpivot
            .filter(pl.col('grade') != 'Baseline')
            .join(base, on=[*base_index, 'variable'], how='left', validate='m:1')
            .with_columns(ratio=pl.col('value') / pl.col('baseline'))
            .with_columns((1.0 - pl.col('ratio')).alias('절감률'))
            .with_columns(pl.col('절감률').clip(upper_bound=1.0).alias('절감률(clip)'))
        )

        # 공공 준5등급에 민간 준5등급 절감률 대입
        assert (
            ratio
            .filter(pl.col('ownership') == '공공', pl.col('grade') == Grade.SUB5)
            .select(pl.col('절감률').eq(0).all())
            .item()
        )
        sub5 = (
            ratio
            .filter(pl.col('ownership') == '민간', pl.col('grade') == Grade.SUB5)
            .select(
                *base_index,
                'grade',
                'variable',
                pl.col('절감률(clip)').alias('절감률(민간)'),
            )
            .drop('ownership')
        )
        rich.print(sub5.glimpse(return_type='string'))
        ratio = (
            (ratio)
            .join(sub5, on=list(set(ratio.columns) & set(sub5.columns)), how='left')
            .with_columns(
                pl
                .when(pl.col('ownership') == '공공', pl.col('grade') == Grade.SUB5)
                .then(pl.col('절감률(민간)'))
                .otherwise(pl.col('절감률(clip)'))
                .alias('절감률(준5보정)')
            )
        )

        var_units = {
            '소요량': '소요량[kWh]',
            '소비량': '소비량(소요+발전)[kWh]',
            '배출량': '배출량[kg]',
        }

        with xlsxwriter.Workbook(path) as wb:
            # 평균
            (
                data
                .group_by(index)
                .agg(pl.mean(*variables))
                .sort(pl.all())
                .rename(var_units)
                .write_excel(wb, worksheet='소요량&배출량', column_widths=column_width)
            )

            # 평균 연면적당
            (
                (normalized)
                .rename({k: v.replace(']', '/m²]') for k, v in var_units.items()})
                .write_excel(
                    wb, worksheet='연면적당 소요량&배출량', column_widths=column_width
                )
            )

            # 변화율
            ratio.write_excel(wb, worksheet='변화율', column_widths=column_width)

            # 변화율 pivot
            (
                ratio
                .with_columns(
                    pl
                    .format('{}({})', 'variable', 'emission_reference')
                    .replace({'소요량(reference)': '소요량'})
                    .alias('variable')
                )
                .filter(
                    pl.col('variable').is_in([
                        '소요량',
                        '배출량(reference)',
                        '배출량(ECO2)',
                    ])
                )
                .pivot(
                    'variable',
                    index=[x for x in index if x != 'emission_reference'],
                    values='절감률(준5보정)',
                )
                .write_excel(wb, worksheet='절감률pivot', column_widths=column_width)
            )

    def __call__(self):
        dst = self.dst or self.src

        categorized = self._prep()
        categorized.write_parquet(dst / '01.emission-categorized.parquet')

        by = ['building-use', 'grade', 'pv-installed', 'emission_reference']
        grouped = categorized.group_by(by, maintain_order=True)
        sample = pl.concat([df.head(self.sample_rows) for _, df in grouped])
        sample.write_excel(dst / '01.emission-categorized-sample.xlsx')

        emission = self.calculate_emission(categorized)
        emission.write_parquet(dst / '01.emission.parquet')
        emission.write_excel(dst / '01.emission.xlsx', column_widths=80)

        self.write_summary(emission, dst / '02.emission-summary.xlsx')

        return emission


@app.command
def evaluate_sample(src: Path, dst: Path | None = None, rows: int = 1000):
    dst = dst or src.parent / f'{src.stem}-presentation-sample.xlsx'
    n = int(rows / 2)

    df = (
        pl
        .scan_parquet(src)
        .filter(pl.col('group') == '1차에너지 소요량', pl.col('function') != '생산-열')
        .drop_nulls('source')
        .collect()
    )
    df = pl.concat([x.head(n) for _, x in df.group_by('building-use')])

    (
        df
        .with_columns(
            pl.col('building-use').replace_strict({
                'residential': '주거',
                'non-residential': '비주거',
            }),
            pl.col('function').replace({'생산-전력': '전력 생산'}),
        )
        .sort([*EvaluateEmission.INDEX, 'grade', 'function', 'source'])
        .rename({
            'building-use': '주거구분',
            'ownership': '소유',
            'scale': '규모',
            'use': '건물용도',
            'bldg': '건물번호',
            'region': '지역',
            'grade': '등급',
            'value': '1차에너지 소요량 [kWh]',
            'function': '용도',
            'source': '에너지원',
            'emission_factor': '배출계수 [kg/kWh]',
            'emission': '배출량 [kg]',
        })
        .drop('scale1', 'scale2', 'variable', 'group', 'requirement', 'unit', '계수')
        .write_excel(dst)
    )


if __name__ == '__main__':
    utils.terminal.LogHandler.set(10)

    app()
