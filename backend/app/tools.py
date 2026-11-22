import collections, json, math, re
import time
from .data import KB
from .config import TH
from .security import redact

tokz = lambda s: re.findall(r"[a-z0-9]{3,}", s.lower())
_docs = [(i, c, t, collections.Counter(tokz(t))) for i, c, t in KB]
_df = collections.Counter(w for *_, cnt in _docs for w in cnt)
_idf = lambda w: math.log((len(_docs) + 1) / (_df.get(w, 0) + 1)) + 1
def _vec(cnt):
    v = {w: c * _idf(w) for w, c in cnt.items()}; nr = math.sqrt(sum(x * x for x in v.values())) or 1
    return {w: x / nr for w, x in v.items()}
_dv = [_vec(d[3]) for d in _docs]
RUNBOOK = {c: t for i, c, t in KB if i.startswith("RB")}

def search_kb(text, k=3):
    """TF-IDF cosine retrieval over runbooks + past incidents (swap for Qdrant + embeddings at scale)."""
    q = _vec(collections.Counter(tokz(text)))
    top = sorted(((sum(q.get(w, 0) * x for w, x in dv.items()), d[0], d[1], d[2]) for d, dv in zip(_docs, _dv)), reverse=True)[:k]
    return [{"id": i, "category": c, "score": round(s, 3), "text": t} for s, i, c, t in top]

PAT = {"db_pool": r"connection is not available|too many connections", "memory_leak": r"outofmemory|oomkilled",
       "dependency_timeout": r"upstream .* timeout|circuit breaker", "cpu_saturation": r"thread pool exhausted|cpu throttling",
       "disk_full": r"no space left|disk full", "config_change": r"invalid configuration|feature flag evaluation", "bad_deploy": r"attributeerror|typeerror|nonetype|exception at"}
def hm(m, d=None):
    if d and d.get("t0"): return time.strftime("%H:%M", time.gmtime(d["t0"] + m * 60)) + "Z"
    return f"{(840 + m) // 60}:{(840 + m) % 60:02d}"

def search_logs(d, lo=0, hi=20): return [l for l in d["logs"] if l[1] != "INFO" and lo <= l[0] <= hi]
def first_anomaly(d): return next((i for i, v in enumerate(d["metrics"]["error_rate"]) if v > TH["error_rate"]), None)

def signals(d, logs, use_retrieval=True):
    """Independent evidence sources vote per hypothesis. Confidence = agreeing sources / 4."""
    votes, M, fe = {}, d["metrics"], first_anomaly(d)
    vote = lambda c, src, why: votes.setdefault(c, {}).__setitem__(src, why)
    for m, lvl, msg in logs:
        for c, p in PAT.items():
            if re.search(p, msg.lower()): vote(c, "logs", f"{lvl} at {hm(m, d)}: {msg[:90]}")
    deploy_cat = None
    if fe is not None:
        for dm, desc in d["deploys"]:
            if re.search(r"^(docs|ci|test|chore):|readme|\bcss\b|test runner", desc.lower()): continue  # can't affect runtime
            if re.search(r"^(docs|ci|test|chore):|readme|\bcss\b|test runner", desc.lower()): continue  # can't affect runtime
            if 0 <= fe - dm <= 10:
                deploy_cat = "config_change" if desc.startswith("config") else "bad_deploy"
                vote(deploy_cat, "deploys", f"Deploy at {hm(dm, d)} ({desc}) {fe - dm} min before first errors")
        db, md, p95, cpu, disk = max(M["db_conns"]), (M["memory_mb"][-1] - M["memory_mb"][0]) / max(M["memory_mb"][0], 1), max(M["p95_ms"]), max(M["cpu"]), max(M["disk_pct"])
        if db > TH['db']: vote("db_pool", "metrics", f"DB connections saturated (max {db})")
        elif md > TH['mem_growth']: vote("memory_leak", "metrics", f"Memory grew {md*100:.0f}% over the window")
        elif cpu > TH['cpu']: vote("cpu_saturation", "metrics", f"CPU saturated (max {cpu}%)")
        elif disk > TH['disk']: vote("disk_full", "metrics", f"Disk usage {disk}%")
        elif p95 >= TH['p95_ms']: vote("dependency_timeout", "metrics", f"p95 {p95:.0f}ms while DB/CPU/memory healthy")
        elif deploy_cat: vote(deploy_cat, "metrics", f"Error rate jumped at {hm(fe, d)} while resources stayed healthy")  # an unexplained spike only implicates a change if one exists
    errs = " ".join(sorted({l[2] for l in logs if l[1] in ("ERROR", "FATAL")}))
    if use_retrieval and errs:
        hit = search_kb(errs)[0]
        if hit["score"] >= 0.2: vote(hit["category"], "retrieval", f"Similar to {hit['id']} (score {hit['score']})")
    # tie-break: on equal source counts, prefer hypotheses backed by metrics (hardest evidence to spoof); max() keeps first on ties
    return dict(sorted(votes.items(), key=lambda kv: "metrics" not in kv[1]))

class Toolbox:
    """Tools exposed to the Groq agent. Output is redacted and marked as untrusted data (prompt-injection defence)."""
    def __init__(self, d): self.d = d
    def call(self, name, a):
        d = self.d
        if name == "search_logs":
            q = (a.get("query") or "").lower()
            out = [f"{hm(m, d)} {l} {msg[:300]}" for m, l, msg in d["logs"] if a.get("start_min", 0) <= m <= a.get("end_min", 20) and (q in msg.lower() if q else l != "INFO")][:40]
        elif name == "get_metric":
            v = d["metrics"].get(a.get("name")); out = {"per_minute_series": v} if v else {"error": "unknown", "available": list(d["metrics"])}
        elif name == "list_deploys": out = [f"{hm(m, d)} {x}" for m, x in d["deploys"]]
        elif name == "search_knowledge_base": out = search_kb(a.get("text", ""))
        else: out = {"error": "unknown tool"}
        return redact(json.dumps({"untrusted_tool_output": out}))
