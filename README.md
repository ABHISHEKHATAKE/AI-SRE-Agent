# AI SRE Agent: autonomous incident investigation

Ask "why did payment service spike with 500s at 2:15 PM?" and the agent investigates logs, metrics, deploys and a knowledge base of runbooks and past incidents, then returns a root cause with evidence, timeline, mitigation and confidence.

## How it works (LangGraph: plan → investigate → verify → finalize)
- **Groq tool-calling agent** (`llama-3.3-70b-versatile`): the LLM chooses which tools to call (`search_logs`, `get_metric`, `list_deploys`, `search_knowledge_base`) in a loop, then submits a report.
- **Verifier**: every conclusion, from the LLM or the rules pipeline, is checked against independent evidence sources (logs, metrics, deploys, retrieval). Confidence = agreeing sources / 4. Fewer than 2 sources means `insufficient_evidence`, and an LLM claim without support is downgraded.
- **Rules mode** (no API key needed) runs the same verifier deterministically and doubles as the benchmark baseline.
- **Retrieval**: TF-IDF cosine search over runbooks and past incidents.
- **Observability**: per-step agent trace in every response, plus a Prometheus-format `/metrics` endpoint.

## Live fault injection (real telemetry)
`backend/app/live.py` runs an actual payment service under synthetic traffic. Faults are injected into the running process and produce **real** symptoms:
a bounded DB connection pool exhausted by leaked connections, real memory growth (RSS), real CPU burners starving a worker pool, real request timeouts to a slow dependency,
a code regression that raises a real `AttributeError` with a real stack location, and a config push that removes a required property. Metrics, logs and deploy events are sampled from the process
and fed to the same agent (`scenario = live`). In the UI choose **LIVE stack**, inject a fault, wait, then Investigate. `python eval/run_live_eval.py` benchmarks all faults end to end.
Honest limits: memory limit and disk quota are enforced in-app (a free host can't be safely OOM-killed or filled), and services run in one process so the demo deploys as one free container.

## Observability stack (local)
`docker compose up --build` adds Prometheus (scrapes `/metrics`) and Grafana (provisioned dashboard at :3000) next to the app. Render's free tier runs the app container alone.

## Benchmark (`python eval/run_eval.py`)
40 generated incidents (8 fault classes x 5 seeds) with random onset times, noisy logs, decoy deploys, red-herring log lines, and paraphrased messages. Results in `eval/results.md`; with `GROQ_API_KEY` set it also evaluates the LLM agent.
Caveat: telemetry and the knowledge base are authored by the same person, so treat accuracy as a regression benchmark, not a real-world claim.

## Run
```
pip install -r backend/requirements.txt
cd backend && uvicorn app.main:app --reload     # http://localhost:8000
pip install pytest && pytest tests              # from project root
```
Docker: `docker build -t sre . && docker run -p 8000:8000 -e GROQ_API_KEY=... sre`

## Deploy free (Render)
Push to GitHub, Render → New → Blueprint (uses `render.yaml`), set `GROQ_API_KEY` (free at console.groq.com). Free instances sleep when idle (~30s wake); history is SQLite in `/tmp`.

## Production path
Replace `data.build` with Prometheus/Loki/GitHub queries, `search_kb` with Qdrant + embeddings, SQLite with Postgres, add OpenTelemetry spans around graph nodes.

## Production use
See `PRODUCTION.md`. Set `TELEMETRY_SOURCE=real`, `API_KEY`, `PROM_URL`, `LOKI_URL`, `GITHUB_REPO`, `DATABASE_URL`, `SLACK_WEBHOOK_URL`, `LIVE_ENABLED=0`, and point Alertmanager's webhook at `POST /api/webhooks/alertmanager`.
