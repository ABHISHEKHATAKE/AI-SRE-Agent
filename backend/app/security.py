"""Redaction of secrets/PII before data reaches an LLM, a database or Slack."""
import re
from .config import env
_P = [("jwt", re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")), ("awskey", re.compile(r"AKIA[0-9A-Z]{16}")),
      ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")), ("ip", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"))]
_SECRET = re.compile(r"(?i)((?:authorization|api[_-]?key|token|secret|password|passwd)[\"']?\s*[:=]\s*(?:bearer\s+)?)[^\s,;\"']+")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
def _luhn(d):
    s = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch) * (2 if i % 2 else 1); s += n - 9 if n > 9 else n
    return s % 10 == 0
def redact(t):
    if not isinstance(t, str) or env("REDACT", "1") == "0": return t
    t = _SECRET.sub(r"\1[REDACTED:secret]", t)
    for name, p in _P[:3]: t = p.sub(f"[REDACTED:{name}]", t)
    t = _CARD.sub(lambda m: "[REDACTED:card]" if 13 <= len(re.sub(r"\D", "", m.group())) <= 19 and _luhn(re.sub(r"\D", "", m.group())) else m.group(), t)
    return _P[3][1].sub("[REDACTED:ip]", t)
def redact_obj(o):
    if isinstance(o, str): return redact(o)
    if isinstance(o, list): return [redact_obj(x) for x in o]
    if isinstance(o, tuple): return tuple(redact_obj(x) for x in o)
    if isinstance(o, dict): return {k: redact_obj(v) for k, v in o.items()}
    return o
