"""LIVE demo stack: a real running payment service whose faults are produced by real resource exhaustion, real
exceptions and real timeouts. Telemetry (metrics, logs, deploy events) is sampled from the running process.
Runs in-process so the whole project deploys as ONE free container. (Isolation: pool/CPU/memory are real; the
memory limit and disk quota are enforced in-app because a free host can't be safely filled or OOM-killed.)"""
import collections, os, queue, random, sqlite3, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FTimeout
import psutil

TICK = float(os.getenv("LIVE_TICK", "2")); RPS = float(os.getenv("LIVE_RPS", "6")); POOL_SIZE = 10
NOISE = ["retrying request, attempt 2", "cache miss ratio high on /quotes", "TLS handshake retry to metrics-sidecar"]
DECOY = ["docs: update README", "payment-ui v3.1: css tweak", "ci: bump test runner"]
class ConfigError(Exception): pass

class Stack:
    def __init__(s):
        s.lock = threading.Lock(); s.tick = 0; s.gen = 0; s.fault = None
        s.pool = queue.Queue()
        for _ in range(POOL_SIZE): s.pool.put(sqlite3.connect(":memory:", check_same_thread=False))
        s.workers = ThreadPoolExecutor(4); s.stop = threading.Event(); s.leak = []; s.held = []
        s.mem_limit = 200_000_000; s.quota = 20_000_000; s.disk = 2_000_000
        s.fraud = 0.02; s.version = "v1.8.2"; s.config = {"payment.tax.rate": 0.18}
        s.logs = collections.deque(maxlen=800); s.hist = collections.deque(maxlen=120); s.cnt = [0, 0, []]
        s.deploys = [(time.time(), "payment v1.8.2: receipt formatting")]

    # ---- the service itself ----
    def tax(s, amount):
        if s.version.startswith("v2"):
            cfg = None                      # regression shipped in v2: tax config never loaded
            return amount * cfg.rate        # -> real AttributeError
        rate = s.config.get("payment.tax.rate")
        if rate is None: raise ConfigError("Invalid configuration: missing property payment.tax.rate")
        return amount * rate

    def serve(s):
        if s.fault == "unknown" and random.random() < 0.12: raise ConnectionError("transient network error, retrying")
        if sum(map(len, s.leak)) >= s.mem_limit: raise MemoryError("OutOfMemoryError: container memory limit exceeded (OOMKilled)")
        try: conn = s.pool.get(timeout=1.0)
        except queue.Empty: raise RuntimeError("HikariPool-1 - Connection is not available, request timed out after 1000ms")
        try: conn.execute("select 1+1").fetchone()
        finally: s.pool.put(conn)
        if s.fraud > 4.5: time.sleep(4.5); raise TimeoutError("upstream fraud-service timeout after 4500ms")
        time.sleep(s.fraud)
        try: s.workers.submit(lambda: sum(i * i for i in range(20000))).result(timeout=1.0)
        except FTimeout: raise RuntimeError("thread pool exhausted, rejecting tasks")
        s.tax(100)
        if s.disk + 200 > s.quota: raise OSError(28, "No space left on device")
        s.disk += 200

    def handle(s):
        t0 = time.time(); ok = True
        try: s.serve()
        except Exception as e:
            ok = False; fr = traceback.extract_tb(e.__traceback__)[-1]
            s.logs.append((time.time(), "ERROR", f"{type(e).__name__}: {e} at payment/{fr.name}:{fr.lineno} -> HTTP 500"))
        with s.lock: s.cnt[0] += 1; s.cnt[1] += not ok; s.cnt[2].append((time.time() - t0) * 1000)

    # ---- fault injection ----
    def clear(s):
        s.gen += 1; s.fault = None; s.stop.set(); s.stop = threading.Event(); s.version = "v1.8.2"; s.config["payment.tax.rate"] = 0.18
        s.fraud = 0.02; s.leak = []; s.disk = 2_000_000
        while s.held: s.pool.put(s.held.pop())

    def inject(s, kind, decoy=False):
        s.clear(); s.fault = kind; now = time.time(); gen = s.gen
        if decoy and kind not in ("bad_deploy", "config_change"): s.deploys.append((now - random.uniform(3, 6) * TICK, random.choice(DECOY)))
        if kind == "bad_deploy": s.version = f"v2.{random.randint(1, 9)}.0"; s.deploys.append((now, f"payment {s.version}: enable new tax calculation"))
        elif kind == "config_change": s.config.pop("payment.tax.rate", None); s.deploys.append((now, "config: update tax-rules.yaml via config service"))
        elif kind == "db_pool": threading.Thread(target=lambda: [s.held.append(s.pool.get()) for _ in range(POOL_SIZE)], daemon=True).start()
        elif kind == "memory_leak": threading.Thread(target=s._leak, args=(gen,), daemon=True).start()
        elif kind == "cpu_saturation":
            ev = s.stop
            def burn():
                while not ev.is_set(): sum(range(20000))
            for _ in range(4): s.workers.submit(burn)
        elif kind == "dependency_timeout": s.fraud = 8.0
        elif kind == "disk_full": s.disk = s.quota - 100

    def _leak(s, gen):
        while s.gen == gen and sum(map(len, s.leak)) < s.mem_limit: s.leak.append(b"x" * 25_000_000); time.sleep(TICK)

    # ---- telemetry ----
    def sampler(s):
        p = psutil.Process(); p.cpu_percent(None)
        while True:
            time.sleep(TICK)
            with s.lock: n, e, lat = s.cnt; s.cnt = [0, 0, []]
            lat.sort(); p95 = lat[int(.95 * (len(lat) - 1))] if lat else (s.hist[-1]["p95_ms"] if s.hist else 0)
            if not s.fault and s.disk > .6 * s.quota: s.disk = 2_000_000   # log rotation
            if random.random() < .2: s.logs.append((time.time(), "WARN", random.choice(NOISE)))
            s.tick += 1
            s.hist.append({"tick": s.tick, "ts": time.time(), "error_rate": round(100 * e / max(n, 1), 2), "p95_ms": round(p95, 1),
                           "cpu": round(min(100, p.cpu_percent(None)), 1), "memory_mb": round(p.memory_info().rss / 1e6, 1),
                           "db_conns": 100 * (POOL_SIZE - s.pool.qsize()) / POOL_SIZE, "disk_pct": round(100 * s.disk / s.quota, 1)})

    def loadgen(s):
        sem = threading.Semaphore(60)
        def one():
            try: s.handle()
            finally: sem.release()
        while True:
            time.sleep(1 / RPS)
            if sem.acquire(blocking=False): threading.Thread(target=one, daemon=True).start()

    def snapshot(s):
        """Last 21 ticks in the same schema the agent already uses (1 tick = 1 'minute')."""
        h = list(s.hist)[-21:]
        if not h: raise RuntimeError("stack is warming up, retry in a few seconds")
        while len(h) < 21: h.insert(0, h[0])
        t0 = h[0]["ts"]; ix = lambda ts: int((ts - t0) // TICK)
        return {"metrics": {k: [x[k] for x in h] for k in ("error_rate", "p95_ms", "cpu", "memory_mb", "db_conns", "disk_pct")},
                "logs": sorted((min(20, ix(t)), l, m) for t, l, m in list(s.logs) if ix(t) >= 0),
                "deploys": [(ix(t), d) for t, d in s.deploys]}

STACK = None
def start():
    global STACK
    if STACK: return STACK
    STACK = Stack()
    for f in (STACK.sampler, STACK.loadgen): threading.Thread(target=f, daemon=True).start()
    return STACK
