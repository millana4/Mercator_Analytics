# ---------- GET /cards/{sku}/measurements ----------
import pandas as pd

from tests.integration.conftest import DATA_DIR


def _any_real_sku() -> int:
    """Берёт первый попавшийся sku прямо из мокового CSV с замерами -
    не завязываемся на то, что где-то в тестах уже посчитан снепшот."""
    csv_files = sorted(DATA_DIR.glob("ch_measurement_*.csv"))
    df = pd.read_csv(csv_files[-1], nrows=50)
    return int(df["sku"].iloc[0])


def test_measurements_for_real_sku_returns_points(client):
    sku = _any_real_sku()
    resp = client.get(f"/api/v1/cards/{sku}/measurements", params={"days": 30})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) > 0
    assert "parsed_at" in body[0]
    assert "price" in body[0]


def test_measurements_sorted_by_date(client):
    sku = _any_real_sku()
    resp = client.get(f"/api/v1/cards/{sku}/measurements", params={"days": 365})
    dates = [pd.Timestamp(row["parsed_at"]) for row in resp.json()]
    assert dates == sorted(dates)


def test_measurements_unknown_sku_returns_empty_list(client):
    resp = client.get("/api/v1/cards/1/measurements")
    assert resp.status_code == 200
    assert resp.json() == []


def test_measurements_default_days_is_30(client):
    sku = _any_real_sku()
    resp_default = client.get(f"/api/v1/cards/{sku}/measurements")
    resp_explicit = client.get(f"/api/v1/cards/{sku}/measurements", params={"days": 30})
    assert len(resp_default.json()) == len(resp_explicit.json())


def test_measurements_days_validation(client):
    resp = client.get("/api/v1/cards/1/measurements", params={"days": 0})
    assert resp.status_code == 422  # ge=1 в Query — граница задана в шаге 6