"""Persistence via SQLAlchemy: SQLite by default, Postgres when DATABASE_URL is set."""
import json, os, tempfile, time
from sqlalchemy import create_engine, text
from .config import env
_e = None
def eng():
    global _e
    if _e is None:
        url = env("DATABASE_URL") or "sqlite:///" + (env("DB_PATH") or os.path.join(tempfile.gettempdir(), "investigations.db"))
        for old in ("postgres://", "postgresql://"):
            if url.startswith(old): url = "postgresql+psycopg://" + url[len(old):]
        _e = create_engine(url, pool_pre_ping=True)
        with _e.begin() as c:
            c.execute(text("create table if not exists investigations(id varchar(40) primary key, ts double precision, scenario varchar(60), status varchar(20), root_cause varchar(60), confidence double precision, result text)"))
    return _e
def save(i, scenario, status, res=None):
    rep = (res or {}).get("report", {})
    with eng().begin() as c:
        c.execute(text("delete from investigations where id=:i"), {"i": i})
        c.execute(text("insert into investigations values(:i,:t,:s,:st,:rc,:cf,:r)"),
                  {"i": i, "t": time.time(), "s": scenario, "st": status, "rc": rep.get("root_cause"), "cf": rep.get("confidence"), "r": json.dumps(res) if res else None})
def get(i):
    with eng().connect() as c: r = c.execute(text("select id,ts,scenario,status,result from investigations where id=:i"), {"i": i}).fetchone()
    return {"id": r[0], "ts": r[1], "scenario": r[2], "status": r[3], "result": json.loads(r[4]) if r[4] else None} if r else None
def recent(n=10):
    with eng().connect() as c: rows = c.execute(text("select id,ts,scenario,status,root_cause,confidence from investigations order by ts desc limit :n"), {"n": n}).fetchall()
    return [{"id": r[0], "ts": r[1], "scenario": r[2], "status": r[3], "root_cause": r[4], "confidence": r[5]} for r in rows]
