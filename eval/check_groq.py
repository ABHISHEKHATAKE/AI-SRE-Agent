import os, sys, pathlib, httpx
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "backend"))
key, model = os.getenv("GROQ_API_KEY"), os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
print("key set:", bool(key), "| model:", model)
if not key: sys.exit("GROQ_API_KEY is not set in this terminal")

r = httpx.post("https://api.groq.com/openai/v1/chat/completions", timeout=30, headers={"Authorization": f"Bearer {key}"},
               json={"model": model, "messages": [{"role": "user", "content": "say ok"}], "max_tokens": 5})
print("direct Groq call:", r.status_code, r.text[:300])

from app.agent import investigate
from app.data import QUERY
out = investigate("db_pool", 1, QUERY, "llm", True); rep = out["report"]
print("root_cause:", rep["root_cause"], "| confidence:", rep["confidence"], "| Groq answered:", rep["llm"])
print("note:", rep.get("note") or "-")
for t in out["trace"]: print("  ", t["step"], "|", t["note"])