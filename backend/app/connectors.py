"""Real telemetry connectors (Prometheus, Loki, GitHub) normalised to the schema the agent uses:
{metrics: {name: [21 points, 1/min]}, logs: [(idx, level, msg)], deploys: [(idx, desc)], t0: epoch}. Incident sits at index 15."""
import calendar, re, time, httpx
from .config import env
from .security import redact
N, STEP, CLIENT = 21, 60, None
QUERIES = {  # PromQL templates; override with PROM_QUERIES='{"cpu": "..."}' ({service} is substituted)
 "error_rate": '100 * sum(rate(http_requests_total{service="{service}",status=~"5.."}[1m])) / sum(rate(http_requests_total{service="{service}"}[1m]))',
 "p95_ms": '1000 * histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{service="{service}"}[1m])) by (le))',
 "cpu": '100 * avg(rate(container_cpu_usage_seconds_total{pod=~"{service}.*"}[1m]))',
 "memory_mb": 'avg(container_memory_working_set_bytes{pod=~"{service}.*"}) / 1e6',
 "db_conns": '100 * sum(db_pool_active{service="{service}"}) / sum(db_pool_max{service="{service}"})',
 "disk_pct": '100 * (1 - avg(node_filesystem_avail_bytes{mountpoint="/"}) / avg(node_filesystem_size_bytes{mountpoint="/"}))'}
LOKI_Q = '{service="{service}"} |~ "(?i)(error|warn|fatal|exception|timeout)"'
iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
uniso = lambda s: calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
def _client(): return CLIENT or httpx.Client()

def _get(url, **kw):
    for i in range(3):
        try:
            r = _client().get(url, timeout=10, **kw)
            if r.status_code < 500 and r.status_code != 429: r.raise_for_status(); return r.json()
        except (httpx.TransportError, httpx.TimeoutException):
            if i == 2: raise
        time.sleep(0.5 * 2 ** i)
    raise RuntimeError(f"{url} unavailable after retries")

def _series(vals, start, default=0.0):
    a = [None] * N
    for ts, v in vals:
        i = int((float(ts) - start) // STEP)
        if 0 <= i < N:
            try: a[i] = round(float(v), 2)
            except ValueError: pass
    seed = next((x for x in a if x is not None), default)
    for i in range(N): a[i] = seed if a[i] is None else a[i]; seed = a[i]
    return a

def fetch(service, at, labels=None):
    if env("TELEMETRY_SOURCE", "sim") == "sim":
        from .data import build
        return build((labels or {}).get("sim_fault", "bad_deploy"), 1)
    start = at - 15 * STEP; end = start + (N - 1) * STEP; ix = lambda ts: int((ts - start) // STEP)
    M, L, D = {}, [], []
    prom = env("PROM_URL")
    if not prom: raise RuntimeError("PROM_URL is not set")
    import json
    q = {**QUERIES, **json.loads(env("PROM_QUERIES", "{}") or "{}")}
    for name, tpl in q.items():
        res = _get(f"{prom}/api/v1/query_range", params={"query": tpl.replace("{service}", service), "start": start, "end": end, "step": STEP})["data"]["result"]
        M[name] = _series(res[0]["values"], start) if res else [0.0] * N
    loki = env("LOKI_URL")
    if loki:
        streams = _get(f"{loki}/loki/api/v1/query_range", params={"query": (env("LOKI_QUERY") or LOKI_Q).replace("{service}", service),
                       "start": int(start * 1e9), "end": int(end * 1e9), "limit": 500})["data"]["result"]
        for st in streams:
            for ts, line in st["values"]:
                lvl = "FATAL" if re.search(r"(?i)fatal|panic", line) else "ERROR" if re.search(r"(?i)error|exception|timeout|failed", line) else "WARN"
                L.append((max(0, min(N - 1, ix(int(ts) / 1e9))), lvl, redact(line[:300])))
    repo, hdr = env("GITHUB_REPO"), {"Authorization": f"Bearer {env('GITHUB_TOKEN')}"} if env("GITHUB_TOKEN") else {}
    if repo:
        try:
            for c in _get(f"https://api.github.com/repos/{repo}/commits", params={"since": iso(start - 600), "until": iso(end)}, headers=hdr):
                D.append((ix(uniso(c["commit"]["author"]["date"])), redact(f"commit {c['sha'][:7]}: {c['commit']['message'].splitlines()[0][:100]}")))
            for d in _get(f"https://api.github.com/repos/{repo}/deployments", params={"environment": env("GITHUB_ENV", "production")}, headers=hdr):
                if start - 600 <= uniso(d["created_at"]) <= end: D.append((ix(uniso(d["created_at"])), f"deploy {d['sha'][:7]} to {d['environment']}"))
        except Exception: pass   # deploy history is best-effort; the other sources still vote
    return {"metrics": M, "logs": sorted(L), "deploys": D, "t0": start}
