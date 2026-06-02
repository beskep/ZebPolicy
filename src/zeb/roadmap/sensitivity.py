from __future__ import annotations

import dataclasses as dc
from functools import cached_property
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, overload

import numpy as np
import pandas as pd
import pingouin as pg
import polars as pl
from more_itertools import always_iterable
from SALib import ProblemSpec
from SALib.analyze import fast, sobol
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split

if TYPE_CHECKING:
    from collections.abc import Collection


SALibMethod = Literal['sobol']  # TODO 분석 방법 추가


class NotCalculatedError(ValueError):
    pass


@dc.dataclass
class SensitivityAnalysis:
    data: pl.DataFrame | pl.LazyFrame

    _: dc.KW_ONLY

    x: str | Collection[str]
    y: str | Collection[str]

    def sensitivity(self) -> dict[tuple[str, str], float]:
        """`{(y, x): sensitivity}`"""  # noqa: D400
        raise NotImplementedError

    def frame(self) -> pl.DataFrame:
        raise NotImplementedError


@dc.dataclass
class StandardizedCoefficient(SensitivityAnalysis):
    _: dc.KW_ONLY

    kwargs: dict | None = None

    @cached_property
    def std(self) -> pl.DataFrame:
        x = tuple(always_iterable(self.x))
        y = tuple(always_iterable(self.y))
        return (
            self.data
            .lazy()
            .select(*x, *y)
            .std()
            .unpivot(x, index=y, variable_name='x', value_name='sx')
            .unpivot(y, index=['x', 'sx'], variable_name='y', value_name='sy')
            .collect()
        )

    @cached_property
    def lm(self) -> pl.DataFrame:
        kwargs = self.kwargs or {}
        x = self.data.lazy().select(self.x).collect().to_pandas()

        def lm(ycol: str) -> pl.DataFrame:
            y = self.data.lazy().select(ycol).collect().to_numpy().ravel()
            lm = pg.linear_regression(X=x, y=y, **kwargs)
            assert isinstance(lm, pd.DataFrame)
            return (
                pl
                .from_pandas(lm)
                .select(pl.lit(ycol).alias('y'), pl.all())
                .rename({'names': 'x'})
            )

        df = pl.concat(lm(y) for y in always_iterable(self.y))

        head = ['y', 'sy', 'x', 'sx', 'coef', 'beta']
        return (
            df
            .join(self.std, on=['x', 'y'], how='left')
            .with_columns(beta=(pl.col('coef') * pl.col('sx') / pl.col('sy')))
            .select(*head, *(x for x in df.columns if x not in head))
        )

    @overload
    def beta(self, *, as_dict: Literal[True] = ...) -> dict[tuple[str, str], float]: ...

    @overload
    def beta(self, *, as_dict: Literal[False]) -> pl.DataFrame: ...

    def beta(self, *, as_dict: bool = True):
        lm = self.lm.select('y', 'x', 'beta').drop_nulls()
        if as_dict:
            return {(r[0], r[1]): r[2] for r in lm.iter_rows()}
        return lm

    def sensitivity(self) -> dict[tuple[str, str], float]:
        return self.beta(as_dict=True)

    def frame(self) -> pl.DataFrame:
        return self.lm


@dc.dataclass
class SALib(SensitivityAnalysis):
    _: dc.KW_ONLY

    bounds: Any = None
    problem_spec: ProblemSpec = dc.field(init=False)

    def __post_init__(self):
        x = list(always_iterable(self.x))
        y = list(always_iterable(self.y))
        bounds = [[0.0, 1.0] for _ in x] if self.bounds is None else self.bounds
        self.problem_spec = ProblemSpec({'names': x, 'bounds': bounds, 'outputs': y})

    def init(self):
        data = self.data.lazy()
        x = data.select(self.x).collect().to_numpy()
        y = data.select(self.y).collect().to_numpy()
        y = y.ravel() if y.shape[1] == 1 else y
        self.problem_spec.set_samples(x).set_results(y)

    def analyse(
        self,
        method: SALibMethod = 'sobol',
        *,
        second_order: bool = False,
        **kwargs,
    ):
        fn = {'fast': fast.analyze, 'sobol': sobol.analyze}[method]

        if method == 'sobol':
            kwargs['calc_second_order'] = second_order

        self.init()
        self.problem_spec.analyze(fn, **kwargs)
        return self

    def _iter_analysis(self):
        psdf = self.problem_spec.to_df()
        nested: list[list[pd.DataFrame]] = psdf if isinstance(psdf[0], list) else [psdf]  # pyright: ignore[reportAssignmentType]

        for y, dfs in zip(always_iterable(self.y), nested, strict=True):
            yield (
                pl
                .from_pandas(pd.concat(dfs, axis=1).reset_index())
                .select(pl.lit(y).alias('y'), pl.all())
                .rename({'index': 'x'})
            )

    def sensitivity(self) -> dict[tuple[str, str], float]:
        """
        First-order sensitivity (S1).

        Returns
        -------
        dict[tuple[str, str], float]

        Raises
        ------
        NotCalculatedError
        """
        if not self.problem_spec.analysis:
            raise NotCalculatedError

        df = self.frame().select('y', 'x', 'S1')
        return {(r[0], r[1]): r[2] for r in df.iter_rows()}

    def frame(self) -> pl.DataFrame:
        if not self.problem_spec.analysis:
            raise NotCalculatedError

        return pl.concat(self._iter_analysis())


class _RFDataset(NamedTuple):
    xtrain: pl.DataFrame
    xtest: pl.DataFrame
    ytrain: pl.DataFrame | pl.Series
    ytest: pl.DataFrame | pl.Series

    def select_y(self, y: str):
        if not (
            isinstance(self.ytrain, pl.DataFrame)
            and isinstance(self.ytest, pl.DataFrame)
        ):
            raise TypeError

        return _RFDataset(
            xtrain=self.xtrain,
            xtest=self.xtest,
            ytrain=self.ytrain.select(y).to_series(),
            ytest=self.ytest.select(y).to_series(),
        )


@dc.dataclass
class RandomForestFeatureImportance(SensitivityAnalysis):
    """
    Feature importance from random forest model.

    References
    ----------
    https://scikit-learn.org/stable/auto_examples/ensemble/plot_forest_importances.html
    """

    _: dc.KW_ONLY

    fit_on_init: dc.InitVar[bool] = True
    random_state: int | None = 42
    forest: RandomForestRegressor = dc.field(default_factory=RandomForestRegressor)

    _importance: dict[str, dict[str, Any]] = dc.field(default_factory=dict)

    def __post_init__(self, fit_on_init: bool):
        self.forest.random_state = self.random_state

        if fit_on_init:
            self.fit()

    def _fit(self, data: _RFDataset):
        self.forest.fit(data.xtrain, data.ytrain)
        std = np.std(
            [tree.feature_importances_ for tree in self.forest.estimators_], axis=0
        )
        return {
            'x': data.xtrain.columns,
            'importance': self.forest.feature_importances_,
            'std': std,
            'se': std / np.sqrt(self.forest.n_estimators),
            'train_score': self.forest.score(data.xtrain, data.ytrain),
            'test_score': self.forest.score(data.xtest, data.ytest),
        }

    def fit(self, test_size: float = 0.2):
        self._importance = {}

        data = _RFDataset(
            *train_test_split(
                self.data.lazy().select(self.x).collect(),
                self.data.lazy().select(self.y).collect(),
                test_size=test_size,
                random_state=self.random_state,
            )
        )
        for y in always_iterable(self.y):
            self._importance[y] = self._fit(data.select_y(y))

        return self

    @property
    def importance(self):
        if not self._importance:
            raise NotCalculatedError

        return self._importance

    def _sensitivity(self):
        for y, d in self.importance.items():
            for x, i in zip(d['x'], d['importance'], strict=True):
                yield (y, x), float(i)

    def sensitivity(self) -> dict[tuple[str, str], float]:
        return dict(self._sensitivity())

    def frame(self) -> pl.DataFrame:
        return pl.concat(
            pl.DataFrame(d).select(pl.lit(y).alias('y'), pl.all())
            for y, d in self.importance.items()
        )
