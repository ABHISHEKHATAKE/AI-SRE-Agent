import json, os
def env(k, d=""): return os.environ.get(k, d)
# Detection thresholds: tune per environment with THRESHOLDS='{"cpu":85,"mem_growth":0.3}'
TH = {"error_rate": 5, "db": 90, "mem_growth": 0.5, "cpu": 90, "disk": 95, "p95_ms": 4000, "min_sources": 2}
try: TH.update(json.loads(env("THRESHOLDS", "{}") or "{}"))
except ValueError: pass
