"""Benchmark: 40 generated incidents (8 kinds x 5 seeds). Ablation: rules vs rules+retrieval, plus the Groq agent if GROQ_API_KEY is set.
GROQ_SEEDS="1,2,3,4,5" runs the Groq agent on more incidents (default 1,2)."""
import json, os, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "backend"))
from app.agent import investigate
from app.data import KINDS, EXPECTED, QUERY

def run(name, mode, retr, seeds):
    ok = wc = used = 0; by = {}; fails = []; fb = []
    for k in KINDS:
        for s in seeds:
            rep = investigate(k, s, QUERY, mode, retr)["report"]; got = rep["root_cause"]; hit = got == EXPECTED[k]
            ok += hit; used += bool(rep["llm"]); by.setdefault(k, [0, 0]); by[k][0] += hit; by[k][1] += 1
            if mode == "llm" and not rep["llm"]: fb.append(f"{k}#{s}: {rep.get('note') or 'fell back to rules'}")
            if not hit:
                fails.append(f"{k}#{s}: expected {EXPECTED[k]}, got {got}" + (f" | {rep['note']}" if rep.get("note") else "")); wc += got != "insufficient_evidence"
            if mode == "llm": time.sleep(2)
    n = len(KINDS) * len(seeds)
    if mode == "llm": print(f"Groq actually answered {used}/{n}; fallbacks: {fb or 'none'}")
    return {"name": name, "correct": ok, "total": n, "accuracy": round(ok / n, 3), "wrong_but_confident": wc, "groq_answered": used if mode == "llm" else None,
            "by_kind": {k: f"{a}/{b}" for k, (a, b) in by.items()}, "failures": fails}

res = [run("rules (no retrieval)", "rules", False, range(1, 6)), run("rules + retrieval", "rules", True, range(1, 6))]
if os.getenv("GROQ_API_KEY"):
    seeds = [int(x) for x in os.getenv("GROQ_SEEDS", "1,2").split(",")]
    res.append(run(f"Groq agent ({os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')}) + verifier (seeds {','.join(map(str, seeds))})", "llm", True, seeds))
pathlib.Path(__file__).with_name("results.json").write_text(json.dumps({"results": res}, indent=1))
md = "# Eval results\n\n| mode | accuracy | wrong-but-confident | Groq answered |\n|---|---|---|---|\n" + "\n".join(
    f"| {r['name']} | {r['correct']}/{r['total']} ({r['accuracy']:.0%}) | {r['wrong_but_confident']} | {r['groq_answered'] if r['groq_answered'] is not None else '-'} |" for r in res)
md += "\n\n## Failures\n" + "\n".join(f"**{r['name']}**\n" + "\n".join(f"- {f}" for f in r["failures"]) + "\n" for r in res)
pathlib.Path(__file__).with_name("results.md").write_text(md); print(md)
if res[1]["accuracy"] < float(os.getenv("MIN_ACC", "0.9")): sys.exit(f"accuracy gate failed: {res[1]['accuracy']}")