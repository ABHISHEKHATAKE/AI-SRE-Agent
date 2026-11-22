import pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "backend"))
import httpx
from fastapi.testclient import TestClient
from app import connectors, security, tools
from app.main import app
c = TestClient(app)

def test_redaction():
    t = security.redact("user bob@example.com card 4111 1111 1111 1111 Authorization: Bearer abc123xyz from 10.1.2.3")
    assert not any(x in t for x in ("bob@", "4111", "abc123xyz", "10.1.2.3"))

def test_auth_required_when_key_set(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    assert c.post("/api/investigate", json={"scenario": "db_pool"}).status_code == 401
    assert c.post("/api/investigate", json={"scenario": "db_pool"}, headers={"X-API-Key": "secret"}).status_code == 200
    assert c.get("/api/health").status_code == 200

def test_webhook_background_job_and_dedupe():
    p = {"alerts": [{"status": "firing", "fingerprint": "fp-t1", "startsAt": "2026-11-22T14:15:00Z", "labels": {"service": "payment", "alertname": "High5xx", "sim_fault": "memory_leak"}}]}
    a = c.post("/api/webhooks/alertmanager", json=p).json()["jobs"][0]; assert not a["deduped"]
    assert c.post("/api/webhooks/alertmanager", json=p).json()["jobs"][0]["deduped"]
    for _ in range(80):
        r = c.get(f"/api/investigations/{a['id']}").json()
        if r["status"] != "running": break
        time.sleep(0.1)
    assert r["status"] == "done" and r["result"]["report"]["root_cause"] == "memory_leak"

def test_real_connectors_end_to_end(monkeypatch):
    for k, v in {"TELEMETRY_SOURCE": "real", "PROM_URL": "http://prom", "LOKI_URL": "http://loki", "GITHUB_REPO": "o/r"}.items(): monkeypatch.setenv(k, v)
    at = 1_700_000_000; start = at - 900
    def handler(req):
        u = str(req.url)
        if "/loki/api/v1/query_range" in u:
            return httpx.Response(200, json={"data": {"result": [{"values": [[str(int((start + 16 * 60) * 1e9)), "HikariPool-1 - Connection is not available, timed out; user bob@example.com"]]}]}})
        if "/api/v1/query_range" in u:
            q = req.url.params["query"]
            f = (lambda i: 100) if "db_pool_active" in q else (lambda i: 30 if i >= 14 else 0.5) if "status=~" in q else (lambda i: 200 if "histogram" in q else 40)
            return httpx.Response(200, json={"status": "success", "data": {"result": [{"values": [[start + i * 60, str(f(i))] for i in range(21)]}]}})
        return httpx.Response(200, json=[])
    monkeypatch.setattr(connectors, "CLIENT", httpx.Client(transport=httpx.MockTransport(handler)))
    d = connectors.fetch("payment", at)
    assert len(d["metrics"]["error_rate"]) == 21 and "bob@example.com" not in str(d["logs"])
    v = tools.signals(d, tools.search_logs(d)); assert len(v["db_pool"]) >= 2
