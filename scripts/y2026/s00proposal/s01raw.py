"""2026-04-23 2025년 ECO2 raw case 배출량 계산 과정, 전기식 냉난방 비율 검토."""

import dataclasses as dc
import functools
from pathlib import Path  # noqa: TC003

import eco2.report
import polars as pl
import polars.selectors as cs
import xlsxwriter
from cyclopts.config import Toml
from cyclopts.types import ExistingDirectory  # noqa: TC002

from zeb.emission import EmissionFactors
from zeb.utils.cli import App


@dc.dataclass(frozen=True)
class BatchReport(eco2.report.BatchReport):
    source: Path

    @functools.cached_property
    def data(self):
        return super().data.insert_column(
            0, pl.lit(self.source.parent.name).alias('src')
        )


app = App(
    config=Toml('config.toml', root_keys=('2026', 'elec')),
    help_on_error=True,
    result_action=['call_if_callable', 'print_non_int_sys_exit'],
)


@app.default
@dc.dataclass
class Equipment:
    src: ExistingDirectory
    dst: Path | None = None

    @functools.cached_property
    def _dst(self):
        return self.dst or self.src

    @functools.cached_property
    def reports(self):
        return [BatchReport(x) for x in self.src.rglob('batchreport.tab')]

    @functools.cached_property
    def data(self):
        return (
            pl
            .concat([x.data for x in self.reports])
            .with_row_index('prev_index')
            .sort('src', 'index', 'prev_index')
            .drop('prev_index')
        )

    def emission(self):
        factor = (
            EmissionFactors.read().dataframe().filter(pl.col('reference') == 'ECO2')
        )
        v = pl.col('variable')
        return (
            self.data
            .filter(
                pl.col('variable').str.contains(
                    r'^(1차에너지소요량/)|((전기|열)에너지 생산량)'
                ),
                pl.col('unit') == 'kWh',
            )
            .drop(
                '고시일',
                '구분코드',
                '구분',
                '단위',
                cs.matches(r'항목\([대중소]\)(코드)?'),
            )
            .with_columns(
                pl.col('value').str.replace_all(',', '').cast(pl.Float64),
                v.str.extract_groups(
                    r'^(1차에너지소요량/(?<fn1>\w+)/(?<src1>.+))'
                    r'|((?<src2>(전기|열)에너지 생산량)\((?<renewable>\w+)\))$'
                ),
            )
            .with_columns(
                function=v.struct['fn1'].fill_null('생산'),
                source=v.struct['src1'].fill_null(v.struct['src2']),
                renewable=v.struct['renewable'],
            )
            .drop('variable')
            .filter(pl.col('source') != '총량')
            .with_columns(
                pl.col('source').replace_strict({
                    '난방유(등유)': '등유',
                    '액화가스(LPG)': 'LPG',
                    '열에너지 생산량': '열',  # 발전량
                    '전기에너지 생산량': '전력',  # 발전량
                    '전력': '전력',
                    '지역난방연료': '지역난방',
                    '지역난방열': '지역난방',
                    '지역냉방열': '지역냉방',
                    '천연가스(LNG)': 'LNG',
                }),
                value=pl.col('value')
                * pl.col('function').replace_strict(
                    '생산', -1, default=1, return_dtype=pl.Float64
                ),
            )
            .rename({'value': 'requirement'})
            .join(factor.drop('reference'), on='source', how='left', validate='m:1')
            .with_columns(emission=pl.col('requirement') * pl.col('emission_factor'))
        )

    def elec_ratio(self):
        """전기식 냉난방 비율."""
        cols = [
            '전력수요관리시설(냉방)/전기사용설비/용량',
            '전력수요관리시설(난방)/전기사용설비/용량',
            '전력수요관리시설(냉방)/비전기사용설비/용량',
            '전력수요관리시설(난방)/비전기사용설비/용량',
            '전력수요관리시설(냉방)/전기사용설비/비중',
            '전력수요관리시설(난방)/전기사용설비/비중',
        ]
        return (
            self.data
            .filter(pl.col('variable').is_in(cols))
            .with_columns(pl.col('value').str.replace_all(',', '').cast(pl.Float64))
            .pivot(
                'variable',
                index=['index', 'src', 'file'],
                values='value',
                sort_columns=True,
            )
        )

    def __call__(self):
        emission = self.emission()
        elec = self.elec_ratio()

        with xlsxwriter.Workbook(self._dst / 'RawModels2025.xlsx') as wb:
            self.data.write_excel(wb, 'raw', column_widths=100)
            emission.write_excel(wb, '원별배출량', column_widths=100)
            elec.write_excel(wb, '전력비중', column_widths=100)


if __name__ == '__main__':
    app()
