from fastapi.testclient import TestClient

from stock_screener_30d.web import app

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_dashboard():
    r = client.get("/")
    assert r.status_code == 200
    assert "Stock Screener 30d" in r.text
    assert "Not financial advice" in r.text
    assert "Backend Validation" in r.text


def test_backtest_cached_endpoint():
    r = client.get("/api/backtest")
    assert r.status_code == 200


def test_logs_endpoint():
    r = client.get("/api/logs")
    assert r.status_code == 200
    assert "logs" in r.json()


def test_scan_latest_endpoint():
    r = client.get("/api/scan/latest")
    assert r.status_code == 200


def test_comparison_endpoint():
    r = client.get("/api/comparison")
    assert r.status_code == 200
    data = r.json()
    assert "paper" in data
    assert "verdict" in data