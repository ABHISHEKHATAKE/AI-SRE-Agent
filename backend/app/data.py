"""Parameterised fault-injection generator + knowledge base. Minutes are relative to 14:00."""
import random

KINDS = {"bad_deploy": "Bad code deploy", "config_change": "Bad config change", "db_pool": "DB pool exhaustion",
         "memory_leak": "Memory leak / OOM", "dependency_timeout": "Upstream dependency timeout",
         "cpu_saturation": "CPU saturation", "disk_full": "Disk full", "unknown": "Ambiguous blip (no clear cause)"}
EXPECTED = {k: k for k in KINDS}; EXPECTED["unknown"] = "insufficient_evidence"
QUERY = "Why did the payment service experience a sudden spike in 500 errors at 2:15 PM?"

# 3 wordings per fault; the 3rd is deliberately NOT matched by the log regexes (tests retrieval/LLM).
MSG = {
 "bad_deploy": ["NullPointerException at TaxCalculator.apply(PaymentController.java:88)", "ClassCastException at LedgerMapper.toDto(Ledger.java:41)", "unexpected null returned by pricing client after v2 release"],
 "config_change": ["Invalid configuration: missing property payment.tax.rate", "feature flag evaluation failed for new-checkout", "config reload error: could not parse tax-rules.yaml"],
 "db_pool": ["HikariPool-1 - Connection is not available, request timed out after 30000ms", "FATAL: too many connections for role payments", "timed out waiting for a free database connection from pool"],
 "memory_leak": ["java.lang.OutOfMemoryError: Java heap space", "container payment-7f OOMKilled, restarting", "process killed by kernel, memory cgroup limit exceeded"],
 "dependency_timeout": ["upstream fraud-service timeout after 5000ms", "circuit breaker open for fraud-service", "call to downstream ledger-api exceeded deadline of 5s"],
 "cpu_saturation": ["thread pool exhausted, rejecting tasks", "CPU throttling detected on payment pods", "request queue saturated, load average 24"],
 "disk_full": ["OSError: [Errno 28] No space left on device", "failed to write WAL segment: disk full", "write failed on /var/log, filesystem 100% used"],
}
NOISE = ["retrying request, attempt 2", "cache miss ratio high on /quotes", "slow query 820ms on ledger.entries", "TLS handshake retry to metrics-sidecar"]
DECOY = ["docs: update README", "payment-ui v3.1: css tweak", "ci: bump test runner"]

KB = [  # (id, category, text): runbooks (RB) + past incidents (INC)
 ("RB-deploy", "bad_deploy", "Bad deploy: errors start right after a release. New exceptions in application code. Mitigation: roll back the release, redeploy previous version, add canary."),
 ("RB-config", "config_change", "Bad config: invalid configuration, missing property, feature flag failures after config push. Mitigation: revert the config change, validate config in CI."),
 ("RB-db", "db_pool", "DB pool exhaustion: connection not available, too many connections, high db connections. Mitigation: find leaked connections and slow queries, raise pool size, restart pods."),
 ("RB-mem", "memory_leak", "Memory leak: heap growth, OutOfMemoryError, OOMKilled restarts. Mitigation: restart or scale pods, raise memory limit temporarily, take a heap dump."),
 ("RB-dep", "dependency_timeout", "Dependency timeout: downstream slow, timeout after N ms, circuit breaker open, healthy cpu and memory. Mitigation: enable fallback, tune timeouts, contact owning team."),
 ("RB-cpu", "cpu_saturation", "CPU saturation: thread pool exhausted, throttling, high load average, queue saturated. Mitigation: scale out, find hot loop with profiler, shed load."),
 ("RB-disk", "disk_full", "Disk full: no space left on device, WAL write failures, filesystem full. Mitigation: free space, rotate logs, expand volume, add disk alerts."),
 ("INC-101", "bad_deploy", "INC-101 payment returned unexpected null from pricing client after v2 release, HTTP 500 spike, rolled back release."),
 ("INC-102", "bad_deploy", "INC-102 ClassCastException in mapper right after deploy, rollback fixed it."),
 ("INC-103", "bad_deploy", "INC-103 AttributeError NoneType object has no attribute after deploy of new tax calculation, rolled back release."),
 ("INC-111", "config_change", "INC-111 config reload error, could not parse yaml rules file after config push, feature flag evaluation failed, reverted config."),
 ("INC-121", "db_pool", "INC-121 timed out waiting for a free database connection from pool, leaked connections after slow query, too many connections."),
 ("INC-131", "memory_leak", "INC-131 process killed by kernel, memory cgroup limit exceeded, heap growth over hours, pods restarted."),
 ("INC-141", "dependency_timeout", "INC-141 call to downstream ledger-api exceeded deadline, fraud-service timeouts, circuit breaker opened."),
 ("INC-151", "cpu_saturation", "INC-151 request queue saturated, load average high, CPU throttling and thread pool exhausted after traffic surge."),
 ("INC-161", "disk_full", "INC-161 write failed, filesystem 100% used, failed to write WAL segment, disk full, no space left on device."),
]

def build(kind, seed=1):
    r = random.Random(f"{kind}-{seed}"); n = lambda b, j: round(b + r.uniform(-j, j), 2)
    on, v = r.randint(11, 15), seed % 3
    M = {"error_rate": [n(.5, .2) for _ in range(21)], "p95_ms": [n(180, 20) for _ in range(21)], "cpu": [n(35, 5) for _ in range(21)],
         "memory_mb": [n(500, 15) for _ in range(21)], "db_conns": [n(40, 4) for _ in range(21)], "disk_pct": [n(55, 1) for _ in range(21)]}
    L = [(3, "INFO", "GET /health ok"), (7, "WARN", "slow response 410ms on /receipts")]
    L += [(r.randint(1, 20), "WARN", r.choice(NOISE)) for _ in range(r.randint(1, 3))]
    D = [(-40, "payment v1.8.2: receipt formatting")]
    if seed % 2 == 1 and kind not in ("bad_deploy", "config_change"): D.append((on - r.randint(3, 8), r.choice(DECOY)))  # decoy deploy
    if seed % 4 == 0 and kind in MSG:  # red herring from a different failure class
        L.append((r.randint(5, on - 1), "WARN", MSG[r.choice([k for k in MSG if k != kind])][0]))
    def ramp(k, a, f):
        for i in range(a, 21): M[k][i] = round(f(i), 2)
    if kind in MSG:
        ramp("error_rate", on, lambda i: n(25, 4))
        if kind == "bad_deploy": D.append((on - 1, f"payment v2.{seed}.0: enable new tax calculation")); ramp("p95_ms", on, lambda i: n(900, 80))
        if kind == "config_change": D.append((on - 1, "config: update tax-rules.yaml via config service"))
        if kind == "db_pool": ramp("db_conns", on - 4, lambda i: n(99, 1)); ramp("p95_ms", on, lambda i: n(30000, 500))
        if kind == "memory_leak": ramp("memory_mb", 0, lambda i: 500 + i * 150 + n(0, 10)); ramp("cpu", on - 3, lambda i: n(80, 4))
        if kind == "dependency_timeout": ramp("p95_ms", on, lambda i: n(5000, 100))
        if kind == "cpu_saturation": ramp("cpu", on - 2, lambda i: n(97, 1)); ramp("p95_ms", on, lambda i: n(1500, 100))
        if kind == "disk_full": ramp("disk_pct", on - 5, lambda i: n(98, 1))
        L += [(i, "ERROR", MSG[kind][v] + " -> HTTP 500") for i in range(on + 1, 21, 2)]
    else: ramp("error_rate", 15, lambda i: n(3, .3))
    return {"metrics": M, "logs": sorted(L), "deploys": D}
