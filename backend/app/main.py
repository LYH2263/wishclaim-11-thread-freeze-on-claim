from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines.claim_lock import claim_allowed, lock_payload, release_if_expired
from app.modules import wish_comment as wc

app = FastAPI(title="Wishclaim", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

def now(): return datetime.now(timezone.utc)

def ttl():
    c = connect(); row = c.execute("SELECT value FROM settings WHERE key='ttl_seconds'").fetchone(); c.close()
    return int(row["value"] if row else 86400)

def sweep(c):
    for r in c.execute("SELECT * FROM wishes WHERE status='claimed'"):
        rel = release_if_expired(r["status"], r["expires_at"], now())
        if rel:
            c.execute("UPDATE wishes SET status=?, claimer=?, claimed_at=?, expires_at=? WHERE id=?",
                      (rel["status"], None, None, None, r["id"]))

@app.get("/api/health")
def health(): return {"ok": True, "project": "wishclaim"}

@app.get("/api/wishes")
def list_wishes():
    c = connect(); sweep(c); c.commit()
    rows = [dict(r) for r in c.execute("SELECT * FROM wishes ORDER BY id DESC")]
    counts = wc.counts_by_wish(c, [r["id"] for r in rows])
    for r in rows:
        r["comment_count"] = counts.get(r["id"], {}).get("count", 0)
    c.close(); return rows

@app.get("/api/wishes/{wid}")
def get_wish(wid: int):
    c = connect(); sweep(c); c.commit()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone(); c.close()
    if not r: raise HTTPException(404, "not found")
    return dict(r)

class CommentIn(BaseModel):
    author: str
    content: str

def _load_wish(c, wid: int):
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    if not r: raise HTTPException(404, "not found")
    return r

@app.get("/api/wishes/{wid}/comments")
def list_comments(wid: int):
    c = connect(); sweep(c); c.commit()
    r = _load_wish(c, wid)
    t = wc.thread(c, wid, r["status"]); c.close(); return t

@app.post("/api/wishes/{wid}/comments/precheck")
def precheck_comment(wid: int, body: CommentIn):
    """Validate author/content only. Never writes, even on failure."""
    c = connect()
    _load_wish(c, wid); c.close()
    errors = wc.validate(body.author, body.content)
    return {"ok": not errors, "errors": errors}

@app.post("/api/wishes/{wid}/comments")
def add_comment(wid: int, body: CommentIn):
    c = connect(); sweep(c); c.commit()
    r = _load_wish(c, wid)
    reason = wc.block_reason(r["status"])
    if reason:
        c.close(); raise HTTPException(409, reason)
    errors = wc.validate(body.author, body.content)
    if errors:
        c.close(); raise HTTPException(422, {"errors": errors})
    out = wc.add_comment(c, wid, body.author.strip(), body.content.strip(), now().isoformat())
    c.commit(); c.close(); return out

@app.delete("/api/wishes/{wid}/comments/{cid}")
def delete_comment(wid: int, cid: int, author: str):
    c = connect()
    _load_wish(c, wid)
    row = c.execute(
        "SELECT author FROM comments WHERE id=? AND wish_id=?", (cid, wid)
    ).fetchone()
    if not row:
        c.close(); raise HTTPException(404, "not_found")
    if row["author"] != (author or "").strip():
        c.close(); raise HTTPException(403, "not_author")
    c.execute("DELETE FROM comments WHERE id=?", (cid,))
    c.commit(); c.close(); return {"ok": True}

class WishIn(BaseModel):
    title: str
    note: str = ""

@app.post("/api/wishes")
def create_wish(body: WishIn):
    c = connect()
    cur = c.execute("INSERT INTO wishes(title,note,status,data_quality) VALUES (?,?,?,?)",
                    (body.title, body.note, "open", "clean"))
    c.commit(); wid = cur.lastrowid; c.close(); return {"id": wid}

class ClaimIn(BaseModel):
    claimer: str

@app.post("/api/wishes/{wid}/claim")
def claim(wid: int, body: ClaimIn):
    c = connect(); sweep(c); c.commit()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    if not r: c.close(); raise HTTPException(404, "not found")
    allowed = claim_allowed(r["status"], r["claimer"], now(), r["expires_at"])
    if not allowed["ok"]:
        c.close(); raise HTTPException(409, allowed["reason"])
    p = lock_payload(body.claimer, now(), ttl())
    c.execute("UPDATE wishes SET status=?, claimer=?, claimed_at=?, expires_at=? WHERE id=?",
              (p["status"], p["claimer"], p["claimed_at"], p["expires_at"], wid))
    c.commit(); c.close(); return p

@app.post("/api/wishes/{wid}/release")
def release(wid: int):
    c = connect()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    if not r: c.close(); raise HTTPException(404, "not found")
    if r["status"] != "claimed":
        c.close(); raise HTTPException(400, "not_claimed")
    c.execute("UPDATE wishes SET status='released', claimer=NULL, claimed_at=NULL, expires_at=NULL WHERE id=?", (wid,))
    c.commit(); c.close(); return {"ok": True, "status": "released"}

@app.post("/api/wishes/{wid}/fulfill")
def fulfill(wid: int):
    c = connect()
    r = c.execute("SELECT * FROM wishes WHERE id=?", (wid,)).fetchone()
    if not r: c.close(); raise HTTPException(404, "not found")
    if r["status"] != "claimed":
        c.close(); raise HTTPException(400, "need_claim")
    c.execute("UPDATE wishes SET status='fulfilled' WHERE id=?", (wid,))
    c.commit(); c.close(); return {"ok": True, "status": "fulfilled"}

@app.get("/api/mine")
def mine(claimer: str):
    c = connect(); sweep(c); c.commit()
    rows = [dict(r) for r in c.execute("SELECT * FROM wishes WHERE claimer=?", (claimer,))]
    counts = wc.counts_by_wish(c, [r["id"] for r in rows])
    for r in rows:
        r["comment_count"] = counts.get(r["id"], {}).get("count", 0)
    c.close(); return rows

@app.get("/api/done")
def done():
    c = connect()
    rows = [dict(r) for r in c.execute("SELECT * FROM wishes WHERE status='fulfilled'")]; c.close(); return rows

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows

@app.get("/api/rules")
def rules():
    return {
        "mutex": "同一愿望同时只能被一人认领",
        "ttl": "认领超时未核销则自动释放",
        "fulfill": "核销后状态变为 fulfilled",
        "comment_freeze": "认领成功后留言串冻结，仅历史只读；手动释放或超时释放后解冻可续写",
        "comment_archive": "核销后留言串归档永久只读，归档优先于解冻，不得再追加",
        "comment_floor": "空内容拒写；楼层号单调不复用；作者可在任何状态删除自己的楼层",
    }
