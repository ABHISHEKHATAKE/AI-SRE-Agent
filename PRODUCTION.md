# Production guide

## What is production-ready here
| Area | Implementation |
|---|---|
| Real data | `connectors.py`: Prometheus (PromQL templates, override with `PROM_QUERIES`), Loki, GitHub commits and deployments. Retries with backoff and timeouts. |
| Trigger | `POST /api/webhooks/alertmanager`: background investigation per firing alert, deduplicated by fingerprint (`DEDUPE_SECONDS`), concurrency capped (`MAX_CONCURRENT`). |
| Delivery | Result stored and retrievable at `GET /api/investigations/{id}`; optional Slack message (`SLACK_WEBHOOK_URL`). |
| Auth + limits | `X-API-Key` on every investigate/webhook/live/history endpoint when `API_KEY` is set; per-key rate limit. |
| Data safety | Secrets, emails, card numbers, JWTs, AWS keys and IPs are redacted before reaching the LLM, DB or Slack (`security.py`). Tool output is marked untrusted and the system prompt forbids following it; the verifier remains the final authority on every claim. |
| Storage | SQLAlchemy: SQLite by default, Postgres via `DATABASE_URL`. |
| Reliability | LLM failure falls back to rules; hard cap of 8 tool steps; 429 backoff; `/api/ready` checks the DB. |
| Observability | JSON access logs with request ids, Prometheus `/metrics` (investigations, failed/deduped jobs, latency), Grafana dashboard in `docker-compose.yml`. |
| Quality gate | `.github/workflows/ci.yml` runs tests and fails the build if benchmark accuracy < `MIN_ACC` (0.9). |
| Container | Non-root user, healthcheck, single process. |

## Go-live checklist
1. Set `TELEMETRY_SOURCE=real`, `LIVE_ENABLED=0`, `API_KEY`, `DATABASE_URL`, and the Prometheus/Loki/GitHub settings. Use read-only credentials.
2. Check your metric names against `connectors.QUERIES`; override any that differ with `PROM_QUERIES`.
3. Tune `THRESHOLDS` per service (defaults assume CPU/disk in %, memory in MB, DB pool utilisation in %).
4. Confirm your data policy allows sending redacted logs to Groq, or point `llm.py` at a self-hosted model.
5. **Shadow mode for 2-4 weeks**: let it investigate real alerts alongside engineers, record the true root cause of each incident, and measure accuracy and wrong-but-confident rate before anyone relies on it.
6. Run behind HTTPS (reverse proxy / ingress). Keep to one process per instance; scale by running more instances with a shared Postgres (note: alert dedupe state is in-memory per instance).

## Known limits
- Connectors are tested with mocked HTTP only, not against your live systems.
- Metric semantics (PromQL) and thresholds need per-environment calibration.
- Retrieval is TF-IDF over the bundled 15 documents: index your own runbooks/postmortems (`data.KB`) or swap in a vector store.
- No automated remediation by design: recommendations only.
