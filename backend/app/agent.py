import os, re, time
from typing import TypedDict
from langgraph.graph import StateGraph, END
from .data import build
from .config import TH
from . import tools, llm, live, connectors

class S(TypedDict, total=False):
    kind: str; seed: int; query: str; mode: str; use_retrieval: bool; service: str; at: float; labels: dict
    data: dict; plan: dict; claim: dict; votes: dict; logs: list; report: dict; trace: list; note: str

def _t(s, name, t0, note): return (s.get("trace") or []) + [{"step": name, "ms": round((time.time() - t0) * 1000, 1), "note": note}]

def plan_step(s):
    t0 = time.time(); m = re.search(r"(\d{1,2}):(\d{2})", s["query"]); mi = int(m.group(2)) if m else 15
    k, svc = s["kind"], s.get("service") or "payment"; ext = k in ("live", "prod")
    data = live.STACK.snapshot() if k == "live" else connectors.fetch(svc, s.get("at") or time.time() - 300, s.get("labels")) if k == "prod" else build(k, s["seed"])
    p = {"service": svc, "incident_minute": mi, "window": [0 if ext else max(0, mi - 10), 20], "source": k if ext else "synthetic"}
    return {"plan": p, "data": data, "trace": _t(s, "plan", t0, f"window {tools.hm(p['window'][0], data)}-{tools.hm(p['window'][1], data)} source={p['source']}")}

def investigate_step(s):
    """LLM mode: Groq chooses tools in a loop and proposes a root cause. Rules mode: deterministic pipeline."""
    t0 = time.time()
    if s["mode"] == "llm" and os.getenv("GROQ_API_KEY"):
        try:
            claim, steps = llm.run_agent(s["query"], tools.Toolbox(s["data"]))
            tr = (s.get("trace") or []) + [{"step": f"tool:{x['tool']}", "ms": 0, "note": str(x["args"])[:80]} for x in steps]
            return {"claim": claim, "trace": tr + [{"step": "llm_claim", "ms": round((time.time() - t0) * 1000), "note": claim["root_cause"]}]}
        except Exception as e:
            return {"note": f"LLM failed ({str(e)[:60]}); used rules", "trace": _t(s, "llm_error", t0, str(e)[:80])}
    return {"trace": _t(s, "rules_pipeline", t0, "deterministic gather")}

def verify_step(s):
    """Every conclusion (LLM's or rules') is checked against independent evidence sources."""
    t0 = time.time(); lo, hi = s["plan"]["window"]; logs = tools.search_logs(s["data"], lo, hi)
    v = tools.signals(s["data"], logs, s.get("use_retrieval", True))
    return {"votes": v, "logs": logs, "trace": _t(s, "verify", t0, ", ".join(f"{k}={len(x)}" for k, x in v.items()) or "no signals")}

def finalize_step(s):
    t0 = time.time(); v, claim, note, d = s["votes"], s.get("claim"), s.get("note", ""), s["data"]
    cat = claim["root_cause"] if claim else (max(v, key=lambda k: len(v[k])) if v else "insufficient_evidence")
    sup = v.get(cat, {})
    if cat == "insufficient_evidence" or len(sup) < TH["min_sources"]:
        if claim and cat != "insufficient_evidence": note = f"LLM claimed {cat} but only {len(sup)} verifier source(s) support it; downgraded"
        rep = {"root_cause": "insufficient_evidence", "confidence": 0.0, "evidence": [], "timeline": [], "llm": bool(claim),
               "summary": "Not enough independent evidence sources agree. No root cause asserted.",
               "mitigation": ["Widen the time window", "Check dependencies and recent config changes", "Escalate to the on-call engineer"]}
    else:
        tl = sorted([{"t": tools.hm(m, d), "event": f"[{l}] {x}"} for m, l, x in s["logs"] if l != "WARN"][:3] + [{"t": tools.hm(m, d), "event": f"[DEPLOY] {x}"} for m, x in d["deploys"] if m >= 0], key=lambda x: x["t"])
        rep = {"root_cause": cat, "confidence": len(sup) / 4, "evidence": [{"source": k, "finding": x} for k, x in sup.items()], "timeline": tl, "llm": bool(claim),
               "summary": claim["summary"] if claim else f"Independent sources agree on {cat}.", "mitigation": (claim or {}).get("mitigation") or [tools.RUNBOOK.get(cat, "")]}
    rep["alternatives"] = {k: len(x) / 4 for k, x in v.items() if k != rep["root_cause"]}; rep["note"] = note
    return {"report": rep, "trace": _t(s, "finalize", t0, rep["root_cause"])}

g = StateGraph(S)
for n, f in [("plan_step", plan_step), ("investigate_step", investigate_step), ("verify_step", verify_step), ("finalize_step", finalize_step)]: g.add_node(n, f)
g.set_entry_point("plan_step"); g.add_edge("plan_step", "investigate_step"); g.add_edge("investigate_step", "verify_step"); g.add_edge("verify_step", "finalize_step"); g.add_edge("finalize_step", END)
GRAPH = g.compile()

def investigate(kind, seed, query, mode="rules", use_retrieval=True, service="payment", at=None, labels=None):
    o = GRAPH.invoke({"kind": kind, "seed": seed, "query": query, "mode": mode, "use_retrieval": use_retrieval, "service": service, "at": at, "labels": labels or {}, "trace": []})
    return {"scenario": kind, "seed": seed, "query": query, "plan": o["plan"], "report": o["report"], "trace": o["trace"]}
