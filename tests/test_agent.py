import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "backend"))
from fastapi.testclient import TestClient
from app.main import app
from app.agent import investigate
from app.data import QUERY
c = TestClient(app)
def test_known_fault(): assert investigate("db_pool", 2, QUERY)["report"]["root_cause"] == "db_pool"
def test_abstains_when_unclear(): assert investigate("unknown", 1, QUERY)["report"]["root_cause"] == "insufficient_evidence"
def test_api(): assert c.post("/api/investigate", json={"scenario": "disk_full", "seed": 2}).json()["report"]["confidence"] >= 0.5
def test_metrics(): assert "sre_investigations_total" in c.get("/metrics").text
def test_live_endpoints_disabled_without_startup(): assert c.post("/api/live/fault", json={"kind": "db_pool"}).status_code == 503
