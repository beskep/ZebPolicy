"""
EHP_COP.

조건:
  건물용도(E열)     = "주거용 이외"
  난방설비(HE열)    = "히트펌프"
  난방사용연료(HG열) = "전기"
  냉방설비(HL열)    = "압축식"
  냉방사용연료(HN열) = "전기"
  지열(JU열)        = 미적용 (비어있음/NaN)

분석대상:
  HJ열 : 난방효율
  HQ열 : 냉방효율
"""

from pathlib import Path  # ruff: ignore[typing-only-standard-library-import]
from typing import Literal

import cyclopts
import matplotlib.pyplot as plt
import openpyxl
import polars as pl
import polars.selectors as cs
import seaborn as sns
from matplotlib.figure import Figure

COP = ('온열원설비_효율', '냉열원설비_효율')


app = cyclopts.App(
    config=cyclopts.config.Toml(
        'env.toml', root_keys=['non-conditioned', 'ehp-cop'], use_commands_as_keys=False
    ),
    result_action=['call_if_callable', 'print_non_int_sys_exit'],
)


def read_excel_column_as_string(
    src: str | Path,
    column: str,
    sheet_name: int | str = 0,
) -> list:
    """
    Excel 컬럼을 형식을 고려하여 문자열로 해석.

    퍼센트 형식 셀: "10%" 반환
    일반 숫자 형식 셀: "0.1" 반환

    Parameters
    ----------
    src : str
    column : str
        0부터 시작하는 인덱스(0='A') 또는 'A', 'B' 같은 컬럼 문자
    sheet_name : int | str, optional

    Returns
    -------
    list

    Raises
    ------
    ValueError
        columns 이름 오류
    """
    wb = openpyxl.load_workbook(src, data_only=True)
    ws = wb.worksheets[sheet_name] if isinstance(sheet_name, int) else wb[sheet_name]

    # 헤더에서 컬럼 찾기
    col_letter = None
    for cell in ws[1]:
        if str(cell.value) == column:
            col_letter = cell.column_letter
            break
    if not col_letter:
        msg = f"Column '{column}' not found"
        raise ValueError(msg)

    result = []
    for cell in ws[col_letter][1:]:  # 헤더 스킵
        if cell.value is None:
            result.append(None)
            continue

        # 퍼센트 형식 확인
        is_percent = '%' in cell.number_format

        if is_percent and isinstance(cell.value, (int, float)):
            # 0.1 → "10%"
            pct = cell.value * 100
            result.append(f'{int(pct)}%' if pct % 1 == 0 else f'{pct:.10g}%')
        else:
            result.append(str(cell.value))

    return result


def read(src: Path):
    cache = src.with_suffix('.parquet')

    if cache.exists():
        return pl.read_parquet(cache)

    df = pl.read_excel(src)
    eff = read_excel_column_as_string(src, column='온열원설비_효율')
    df = df.with_columns(pl.Series('온열원설비_효율_str', eff))

    df.write_parquet(cache)

    return df.with_columns(cs.string().str.strip_chars())


def eda(
    root: Path,
    src: Path,
    heating_type: Literal['전체', '개별식', '중앙식제외'],
    *,
    figsize: tuple[float, float]
    | tuple[float, float, Literal['in', 'cm', 'px']]
    | None = (16, 8, 'cm'),
):
    df = read(src)

    # 지열 미적용 여부: 비어있음(NaN) 또는 빈 문자열("") 모두 "미적용"으로 처리
    df = df.with_columns(pl.col('지열_냉방_용량').fill_null('').replace({'': '미적용'}))
    print('원본 shape:', df.shape)

    # 조건 필터링
    filtered = df.filter(
        pl.col('인증구분') == '본인증',
        pl.col('건물용도') == '주거용 이외',
        pl.col('온열원설비_난방방식').is_in(['히트펌프', 'EHP']),
        pl.col('온열원설비_사용연료') == '전기',
        pl.col('냉열원설비_냉방방식') == '압축식',
        pl.col('냉열원설비_사용연료') == '전기',
        pl.col('지열_냉방_용량') == '미적용',
    )

    match heating_type:
        case '개별식':
            filtered = filtered.filter(pl.col('냉난방방식_난방') == '개별식')
        case '중앙식제외':
            filtered = filtered.filter(pl.col('냉난방방식_난방') != '중앙식')

    print('조건을 만족하는 건 shape:', filtered.shape)

    # 난방효율(HJ열) 퍼센트 형태 제거
    filtered = filtered.filter(pl.col('온열원설비_효율_str').str.contains('%').not_())
    print('난방효율 백분율 제거 후 shape:', filtered.shape)

    stats_df = filtered.select(COP).describe(
        percentiles=(0.1, 0.25, 0.5, 0.75, 0.9), interpolation='linear'
    )
    stats_df.write_csv(
        root / f'난방_냉방_효율_기본통계_{heating_type}.csv', include_bom=True
    )
    print('기본통계:', stats_df)

    # 하위 10%, 평균 dictionary
    stats = (
        (stats_df)
        .rename(dict(zip(COP, ('heating', 'cooling'), strict=True)))
        .rows_by_key('statistic', named=True, unique=True)
    )

    melted = (
        (filtered)
        .unpivot(COP, variable_name='contents', value_name='COP')
        .with_columns(
            pl.col('contents').replace_strict(
                dict(zip(COP, ('Heating', 'Cooling'), strict=True))
            )
        )
    )

    fig = Figure(figsize=figsize)
    ax = fig.subplots()

    sns.boxplot(
        data=melted,
        x='COP',
        y='contents',
        hue='contents',
        legend=False,  # 범례 표시 여부
        showfliers=True,  # 이상치 표시 여부
        showmeans=True,  # 평균 표시 여부
        meanprops={
            'marker': 'D',
            'markerfacecolor': 'black',
            'markeredgecolor': 'black',
            'markersize': '6',
        },  # 평균 표시 스타일
        width=0.5,  # 박스 폭
        palette=['#FF9999', '#99CCFF'],  # 색상 지정
        order=['Heating', 'Cooling'],  # 순서 지정
        ax=ax,
    )

    ax.set_ylabel('')
    ax.set_xlabel('COP')

    ax.scatter(
        [stats['10%']['heating'], stats['10%']['cooling']],
        [0, 1],
        color='darkred',
        marker='o',
        s=80,
        zorder=2,
    )

    ax.text(
        stats['10%']['heating'],
        0.08,
        f'Bottom 10%: {stats["10%"]["heating"]:.2f}',
        color='darkred',
        ha='center',
        va='top',
    )
    ax.text(
        stats['10%']['cooling'],
        1.08,
        f'Bottom 10%: {stats["10%"]["cooling"]:.2f}',
        color='darkred',
        ha='center',
        va='top',
    )

    ax.text(
        stats['mean']['heating'],
        -0.08,
        f'Mean: {stats["mean"]["heating"]:.2f}',
        color='black',
        ha='center',
        va='bottom',
    )
    ax.text(
        stats['mean']['cooling'],
        1 - 0.08,
        f'Mean: {stats["mean"]["cooling"]:.2f}',
        color='black',
        ha='center',
        va='bottom',
    )

    plot = root / f'난방_냉방_효율_박스플롯_{heating_type}.png'
    fig.savefig(plot)

    print(f"시각화 완료. '{plot.name}'로 저장합니다.")


@app.default
def main(root: Path, src: Path):
    src = root / src

    sns.set_theme(
        context='notebook',  # 그래프 요소, 글자 스케일 조정
        style='ticks',
        palette='pastel',
    )

    plt.rcParams['axes.edgecolor'] = '0.4'
    plt.rcParams['xtick.color'] = '0.4'
    plt.rcParams['ytick.color'] = '0.4'
    plt.rcParams['xtick.labelcolor'] = '0'
    plt.rcParams['ytick.labelcolor'] = '0'
    plt.rcParams['axes.unicode_minus'] = False  # 마이너스 기호 깨짐 방지
    plt.rcParams['figure.constrained_layout.use'] = True
    plt.rcParams['savefig.dpi'] = 300

    eda(root, src, heating_type='전체')
    eda(root, src, heating_type='개별식')
    eda(root, src, heating_type='중앙식제외')


if __name__ == '__main__':
    app()
