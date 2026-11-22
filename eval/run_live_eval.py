"""LIVE fault-injection benchmark: injects each fault into the running stack, waits, investigates from REAL telemetry."""
import json, os, pathlib, sys, time
os.environ.setdefault("LIVE_TICK", "0.7")
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "backend"))
from app import live
from app.agent import investigate
from app.data import KINDS, EXPECTED, QUERY
S = live.start(); print("warming up…"); time.sleep(live.TICK * 22)
ok = wc = 0; rows = []; fails = []
for i, k in enumerate(KINDS):
    S.inject(k, decoy=i % 2 == 1); time.sleep(live.TICK * 14)
    rep = investigate("live", 1, QUERY, os.getenv("EVAL_MODE", "rules"), True)["report"]; hit = rep["root_cause"] == EXPECTED[k]; ok += hit
    rows.append(f"{k}{' (+decoy deploy)' if i % 2 else ''}: {rep['root_cause']} conf={rep['confidence']:.2f} {'PASS' if hit else 'FAIL'}"); print(rows[-1])
    if not hit: fails.append(rows[-1]); wc += rep["root_cause"] != "insufficient_evidence"
    S.clear(); time.sleep(live.TICK * 10)
p = pathlib.Path(__file__).with_name("results.json"); d = json.loads(p.read_text()) if p.exists() else {"results": []}
d["results"] = [r for r in d["results"] if not r["name"].startswith("LIVE")] + [{"name": "LIVE fault injection on running stack (rules + retrieval)", "correct": ok, "total": len(KINDS),
     "accuracy": round(ok / len(KINDS), 3), "wrong_but_confident": wc, "by_kind": {}, "failures": fails}]
p.write_text(json.dumps(d, indent=1)); print(f"LIVE accuracy {ok}/{len(KINDS)}")
