import collections, hmac, json, logging, os, threading, time, uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from . import live, store
from .agent import investigate
from .config import env
from .data import KINDS, QUERY, build
from .security import redact_obj

logging.basicConfig(level=logging.INFO, format="%(message)s"); log = logging.getLogger("sre")
def jlog(**kw): log.info(json.dumps({"ts": round(time.time(), 3), **kw}))
app = FastAPI(title="AI SRE Agent")
COUNT, LAT, STATS = collections.Counter(), [], collections.Counter()
POOL = ThreadPoolExecutor(int(env("MAX_CONCURRENT", "4"))); SEEN, HITS = {}, collections.defaultdict(collections.deque)

class Req(BaseModel):
    scenario: str; seed: int = 1; query: str = QUERY; mode: str = "rules"; service: str = "payment"; at: Optional[float] = None
class Fault(BaseModel):
    kind: str; decoy: bool = False

def guard(request: Request, x_api_key: Optional[str] = Header(None)):
    key = env("API_KEY")
    if key and not hmac.compare_digest(x_api_key or "", key): raise HTTPException(401, "invalid or missing API key")
    who = x_api_key or (request.client.host if request.client else "anon"); q = HITS[who]; now = time.time()
    while q and q[0] < now - 60: q.popleft()
    if len(q) >= int(env("RATE_LIMIT_PER_MIN", "60")): raise HTTPException(429, "rate limit exceeded")
    q.append(now)

@app.middleware("http")
async def access_log(request: Request, call_next):
    rid, t0 = request.headers.get("x-request-id") or uuid.uuid4().hex[:12], time.time(); resp = await call_next(request); resp.headers["X-Request-ID"] = rid
    if request.url.path.startswith("/api"): jlog(event="request", id=rid, method=request.method, path=request.url.path, status=resp.status_code, ms=round((time.time() - t0) * 1000))
    return resp

@app.on_event("startup")
def _start():
    if env("LIVE_ENABLED", "1") != "0": live.start()

def run_investigation(r: Req, labels=None):
    t0 = time.time(); res = redact_obj(investigate(r.scenario, r.seed, r.query, r.mode, True, r.service, r.at, labels)); LAT.append(time.time() - t0)
    COUNT[res["report"]["root_cause"]] += 1; return res

def notify(res):
    url = env("SLACK_WEBHOOK_URL")
    if not url: return
    r = res["report"]; txt = f"*{res['plan'].get('service')}* root cause: `{r['root_cause']}` (confidence {r['confidence']:.0%})\n" + "\n".join(f"• {e['source']}: {e['finding']}" for e in r["evidence"][:4])
    try: httpx.post(url, json={"text": txt}, timeout=10)
    except Exception as e: jlog(event="slack_failed", error=str(e)[:100])

def job(i, alert):
    try:
        lb = alert.get("labels", {}); svc = lb.get("service", "payment"); st = alert.get("startsAt")
        at = datetime.fromisoformat(st.replace("Z", "+00:00")).timestamp() if st else None
        res = run_investigation(Req(scenario="prod", service=svc, at=at, query=f"Why is {svc} alerting: {lb.get('alertname', 'alert')}?"), lb)
        store.save(i, "prod", "done", res); notify(res); jlog(event="investigation_done", id=i, root_cause=res["report"]["root_cause"])
    except Exception as e:
        STATS["failed"] += 1; store.save(i, "prod", "failed", {"error": str(e)[:300]}); jlog(event="investigation_failed", id=i, error=str(e)[:200])

@app.get("/api/health")
def health(): return {"ok": True, "groq": bool(env("GROQ_API_KEY")), "source": env("TELEMETRY_SOURCE", "sim")}
@app.get("/api/ready")
def ready():
    try: store.recent(1)
    except Exception as e: raise HTTPException(503, f"database unavailable: {str(e)[:80]}")
    return {"ready": True}
@app.get("/api/scenarios")
def scenarios(): return ([{"id": "live", "title": "LIVE stack (real running service)"}] if live.STACK else []) + [{"id": k, "title": v} for k, v in KINDS.items()]
@app.get("/api/telemetry/{kind}")
def telemetry(kind: str, seed: int = 1):
    if kind == "live" and live.STACK: return live.STACK.snapshot()
    if kind not in KINDS: raise HTTPException(404, "unknown scenario")
    return build(kind, seed)

@app.post("/api/investigate", dependencies=[Depends(guard)])
def run(r: Req):
    if r.scenario not in KINDS and r.scenario != "prod" and not (r.scenario == "live" and live.STACK): raise HTTPException(404, "unknown scenario")
    try: res = run_investigation(r)
    except RuntimeError as e: raise HTTPException(503, str(e)[:200])
    res["id"] = uuid.uuid4().hex[:12]; store.save(res["id"], r.scenario, "done", res); return res

@app.post("/api/webhooks/alertmanager", dependencies=[Depends(guard)])
def alertmanager(payload: dict):
    """Alertmanager/PagerDuty-style webhook: one background investigation per firing alert, deduplicated by fingerprint."""
    jobs, now, ttl = [], time.time(), float(env("DEDUPE_SECONDS", "600"))
    for a in payload.get("alerts", []):
        if a.get("status", "firing") != "firing": continue
        fp = a.get("fingerprint") or json.dumps(a.get("labels", {}), sort_keys=True)
        if fp in SEEN and now - SEEN[fp][1] < ttl: STATS["deduped"] += 1; jobs.append({"id": SEEN[fp][0], "deduped": True}); continue
        i = uuid.uuid4().hex[:12]; SEEN[fp] = (i, now); store.save(i, "prod", "running"); POOL.submit(job, i, a); jobs.append({"id": i, "deduped": False})
    return {"jobs": jobs}

@app.get("/api/investigations/{i}", dependencies=[Depends(guard)])
def get_investigation(i: str):
    r = store.get(i)
    if not r: raise HTTPException(404, "not found")
    return r
@app.get("/api/investigations", dependencies=[Depends(guard)])
def list_investigations(): return store.recent(20)

@app.post("/api/live/fault", dependencies=[Depends(guard)])
def fault(f: Fault):
    if not live.STACK: raise HTTPException(503, "live stack disabled")
    if f.kind not in KINDS: raise HTTPException(404, "unknown fault")
    live.STACK.inject(f.kind, f.decoy); return {"injected": f.kind}
@app.post("/api/live/clear", dependencies=[Depends(guard)])
def clear():
    if live.STACK: live.STACK.clear()
    return {"ok": True}
@app.get("/api/live/status", dependencies=[Depends(guard)])
def status():
    S = live.STACK
    if not S: raise HTTPException(503, "live stack disabled")
    return {"fault": S.fault, "tick": S.tick, "latest": S.hist[-1] if S.hist else None, "recent_logs": [m for _, _, m in list(S.logs)[-6:]]}

@app.get("/api/history", dependencies=[Depends(guard)])
def history(): return [{"ts": r["ts"], "scenario": r["scenario"], "root_cause": r["root_cause"] or r["status"]} for r in store.recent(10)]
@app.get("/api/benchmark")
def benchmark():
    p = Path(__file__).parents[2] / "eval" / "results.json"
    return json.loads(p.read_text()) if p.exists() else {}

@app.get("/metrics", response_class=PlainTextResponse)
def metrics():  # Prometheus exposition format
    out = ["# TYPE sre_investigations_total counter"] + [f'sre_investigations_total{{root_cause="{k}"}} {v}' for k, v in COUNT.items()]
    out += [f"sre_jobs_{k}_total {v}" for k, v in STATS.items()]
    if live.STACK and live.STACK.hist: out += [f"sre_live_{k} {v}" for k, v in live.STACK.hist[-1].items() if k not in ("tick", "ts")]
    return "\n".join(out + ["# TYPE sre_investigation_seconds_sum counter", f"sre_investigation_seconds_sum {sum(LAT):.4f}"]) + "\n"

app.mount("/", StaticFiles(directory=Path(__file__).parents[2] / "frontend", html=True), name="ui")
