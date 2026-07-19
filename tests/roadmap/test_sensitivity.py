from functools import lru_cache

import pandas as pd
import pingouin as pg
import polars as pl
import polars.selectors as cs
import pytest
import seaborn as sns
from polars import col as c

import zeb.roadmap.sensitivity as sens


@lru_cache(maxsize=8)
def get_data():
    return (
        pl
        .from_pandas(sns.load_dataset('tips'))
        .with_columns(pl.col('time').to_physical())
        .with_columns()
    )


@pytest.mark.parametrize(
    'xx',
    [
        ['total_bill'],
        ['size'],
        ['total_bill', 'size'],
    ],
)
def test_standardized_coefficient(xx: list[str]):
    """표준화 회귀계수 (Standardization Regression Coefficient) 계산 테스트."""
    data = get_data()
    y = 'tip'

    # 표준화 회귀계수
    sc = sens.StandardizedCoefficient(data, x=xx, y=y)
    beta = sc.beta(as_dict=True)

    # 표준화 후 회귀
    standardized = (
        data  # ruff:ignore[pandas-use-of-dot-pivot-or-unstack]
        .select(cs.numeric())
        .with_row_index()
        .unpivot(index='index')
        .with_columns(
            avg=pl.mean('value').over('variable'),
            std=pl.std('value').over('variable'),
        )
        .with_columns(standardized=(c('value') - c('avg')) / c('std'))
        .pivot('variable', index='index', values='standardized')
    )
    lm = pg.linear_regression(
        X=standardized.select(xx).to_pandas(),
        y=standardized[y].to_numpy(),
        as_dataframe=True,
    )
    assert isinstance(lm, pd.DataFrame)
    coef = dict(zip(lm['names'], lm['coef'], strict=True))

    # test
    for x in xx:
        assert coef[x] == pytest.approx(beta[y, x]), (x, beta, coef)


@pytest.mark.parametrize(
    ('sa', 'method'),
    [
        (sens.StandardizedCoefficient, None),
        (sens.SALib, 'sobol'),
        (sens.RandomForestFeatureImportance, None),
    ],
)
@pytest.mark.parametrize(
    ('x', 'y'),
    [
        (['size'], ['tip']),
        (['size', 'time'], ['tip']),
        (['size'], ['tip', 'total_bill']),
        (['size', 'time'], ['tip', 'total_bill']),
    ],
)
def test_sensitivity(
    sa: type[sens.SensitivityAnalysis],
    method: str | None,
    x: list[str],
    y: list[str],
):
    analysis = sa(data=get_data(), x=x, y=y)

    if method is None:
        print(analysis.sensitivity())
        print(analysis.frame())
        return

    if len(x) == 1:
        return

    assert isinstance(analysis, sens.SALib)
    analysis.analyse(method=method)  # type: ignore[arg-type]
    print(analysis.sensitivity())
    print(analysis.frame())
