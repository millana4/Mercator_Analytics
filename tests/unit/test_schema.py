import pandas as pd
import pytest

from app.connectors.schema import CARDS, conform, to_naive_local


def test_conform_missing_column_raises():
    df = pd.DataFrame({"card_id": ["a"], "sku": [1]})
    with pytest.raises(ValueError, match="url"):
        conform(df, CARDS)


def test_conform_keeps_only_spec_columns_in_order():
    df = pd.DataFrame({
        "card_id": ["a"], "stratum_id": ["b"], "sku": [1], "name": ["n"],
        "url": ["u"], "collection": ["c"], "status": ["active"],
        "created_at": ["2026-08-01"], "dropped_at": [None],
        "out_of_stock_since": [None], "last_measured_at": [None],
        "extra_junk_column": ["ignored"],
    })
    out = conform(df, CARDS)
    assert list(out.columns) == list(CARDS.keys())
    assert "extra_junk_column" not in out.columns


def test_to_naive_local_strips_timezone():
    s = pd.Series(["2026-08-01T12:00:00+03:00"])
    out = to_naive_local(s)
    assert out.dt.tz is None
    assert out.iloc[0].hour == 12