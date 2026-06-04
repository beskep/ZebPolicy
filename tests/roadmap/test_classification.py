import datetime

import polars as pl
import pytest

import zeb.roadmap.classification as clsf


def _to_date(text: str):
    return datetime.date.fromisoformat(text)


@pytest.mark.parametrize(
    ('breaks', 'data', 'labels'),
    [
        (
            ['2000-01-01'],
            ['1999-12-31', '2000-01-01', '2000-01-02'],
            ['P0', 'P1', 'P1'],
        ),
        (
            ['2000-01-01', '2000-07-01', '2001-01-01'],
            ['2000-01-01', '2000-03-01', '2000-07-01', '2000-12-31', '2001-01-01'],
            ['P1', 'P1', 'P2', 'P2', 'P3'],
        ),
    ],
)
def test_breaks_period(breaks: list[str], data: list[str], labels: list[str]):
    value = pl.Series('date', [_to_date(x) for x in data])

    b = clsf.Breaks(period=[_to_date(x) for x in breaks], area=[])
    labels_ = b.cut(
        value, breaks='period', left_closed=True, include_breaks=False
    ).to_list()

    assert labels_ == labels


@pytest.mark.parametrize(
    ('breaks', 'data', 'labels'),
    [([0], [-1, 0, 1], ['A1', 'A2', 'A2'])],
)
def test_breaks_area(breaks: list[float], data: list[float], labels: list[str]):
    b = clsf.Breaks(period=[], area=breaks)
    labels_ = b.cut(
        pl.Series('data', data), breaks='area', left_closed=True, include_breaks=False
    ).to_list()

    assert labels_ == labels
