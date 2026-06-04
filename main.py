import json, os, random, threading, time
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

app = FastAPI()

# ใช้ disk path ถ้ามี (บน Render), ไม่งั้นใช้ current folder (ตอน dev บนเครื่อง)
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
FILE = os.path.join(DATA_DIR, "teleport.json")

DEFAULT = {"teleport": 0, "jobids": [], "placeid": 0, "mush": 0, "limit": 15}

# ---------- helpers ----------
def ensure_file():
    if not os.path.exists(FILE):
        with open(FILE, "w") as f:
            json.dump(DEFAULT, f, indent=2)

def migrate(data: dict) -> dict:
    """Auto-upgrade old format: 'jobid' (string) -> 'jobids' (list)."""
    if "jobids" not in data:
        old = data.pop("jobid", "")
        data["jobids"] = [old] if isinstance(old, str) and old else []
    for k, v in DEFAULT.items():
        data.setdefault(k, v)
    return data

def load() -> dict:
    ensure_file()
    with open(FILE, "r") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            data = dict(DEFAULT)
    return migrate(data)

def save(data: dict):
    with open(FILE, "w") as f:
        json.dump(data, f, indent=2)

# ---------- rate limit (in-memory, per-minute fixed window) ----------
_rate_lock = threading.Lock()
_rate_window = 0
_rate_count  = 0

def _current_minute() -> int:
    return int(time.time() // 60)

def try_consume(limit: int):
    global _rate_window, _rate_count
    now_min = _current_minute()
    with _rate_lock:
        if now_min != _rate_window:
            _rate_window = now_min
            _rate_count = 0
        if _rate_count >= limit:
            return False, _rate_count
        _rate_count += 1
        return True, _rate_count

def rate_status(limit: int):
    global _rate_window, _rate_count
    now_min = _current_minute()
    with _rate_lock:
        used = 0 if now_min != _rate_window else _rate_count
    return {
        "limit": limit,
        "used":  used,
        "remaining": max(0, limit - used),
        "window_minute": now_min,
        "seconds_until_reset": 60 - int(time.time()) % 60,
    }

# ---------- receivers (in-memory, ephemeral) ----------
# {username: {"placeid": int, "jobid": str, "expires": float, "last_seen": float}}
_receivers: dict = {}
_receivers_lock = threading.Lock()
RECEIVER_TTL = 30  # seconds — 3x heartbeat interval (10s)

def _purge_expired():
    """Caller must hold _receivers_lock."""
    now = time.time()
    dead = [u for u, r in _receivers.items() if r["expires"] <= now]
    for u in dead:
        del _receivers[u]

def heartbeat_set(username: str, placeid: int, jobid: str):
    now = time.time()
    with _receivers_lock:
        _receivers[username] = {
            "placeid":   placeid,
            "jobid":     jobid,
            "last_seen": now,
            "expires":   now + RECEIVER_TTL,
        }
        _purge_expired()
        return len(_receivers)

def receivers_active():
    """Returns list of active receivers (after purge)."""
    with _receivers_lock:
        _purge_expired()
        now = time.time()
        return [
            {
                "username":   u,
                "placeid":    r["placeid"],
                "jobid":      r["jobid"],
                "expires_in": max(0, int(r["expires"] - now)),
            }
            for u, r in _receivers.items()
        ]

def pick_destination():
    """Return one random active receiver's (placeid, jobid), or None if none online."""
    with _receivers_lock:
        _purge_expired()
        if not _receivers:
            return None
        choice = random.choice(list(_receivers.values()))
        return choice["placeid"], choice["jobid"]

# ---------- models ----------
class ConfigUpdate(BaseModel):
    teleport: Optional[int] = None
    jobid: Optional[str] = None
    jobids: Optional[List[str]] = None
    placeid: Optional[int] = None
    mush: Optional[int] = None
    limit: Optional[int] = None

class JobIdBody(BaseModel):
    jobid: str

class Heartbeat(BaseModel):
    username: str
    placeid: int
    jobid: str

# ---------- routes ----------
@app.get("/")
def home():
    return {"message": "API is running"}

# Sender อ่านปลายทาง: ดึง (placeid, jobid) จาก Receiver ที่ active แบบสุ่ม
# ผลลัพธ์ตาม decision tree:
#  1) config teleport=0 -> teleport=0 (no count)
#  2) shoom < mush       -> teleport=0 (no count)
#  3) no active receiver -> teleport=0 (no count)
#  4) rate-limit เต็ม     -> teleport=0 (no count)
#  5) all pass            -> teleport=1, placeid+jobid จาก Receiver, count +1
@app.get("/teleport")
def teleport(shoom: Optional[int] = Query(None)):
    data = load()

    tp_flag = int(data.get("teleport", 0))
    mush    = int(data.get("mush", 0))
    limit   = int(data.get("limit", 15))

    placeid_out = 0
    jobid_out   = ""

    if tp_flag == 1:
        client_shoom = int(shoom) if shoom is not None else 0
        if client_shoom < mush:
            tp_flag = 0
        else:
            dest = pick_destination()
            if dest is None:
                tp_flag = 0   # ไม่มี Receiver online
            else:
                allowed, _ = try_consume(limit)
                if not allowed:
                    tp_flag = 0
                else:
                    placeid_out, jobid_out = dest

    return {
        "teleport": tp_flag,
        "placeid":  placeid_out,
        "mush":     mush,
        "jobid":    jobid_out,
    }

# CLI: ดูคอนฟิกเต็ม
@app.get("/teleport/all")
def teleport_all():
    return load()

# CLI: ดูสถานะ rate limit
@app.get("/ratelimit")
def ratelimit():
    data = load()
    return rate_status(int(data.get("limit", 15)))

# CLI: รายชื่อ Receiver ที่ active
@app.get("/receivers")
def receivers():
    return receivers_active()

# Receiver: ส่ง heartbeat
@app.post("/heartbeat")
def heartbeat(hb: Heartbeat):
    if not hb.username:
        raise HTTPException(400, "username is empty")
    if not hb.jobid:
        raise HTTPException(400, "jobid is empty")
    count = heartbeat_set(hb.username, int(hb.placeid), hb.jobid)
    return {"status": "ok", "active_receivers": count}

# CLI: อัปเดตคอนฟิก (เฉพาะ field ที่ส่งมา)
@app.post("/config")
def update_config(update: ConfigUpdate):
    data = load()
    payload = update.dict(exclude_none=True)

    # legacy support
    if "jobid" in payload:
        single = payload.pop("jobid")
        payload["jobids"] = [single] if single else []
    if "limit" in payload:
        payload["limit"] = max(0, int(payload["limit"]))

    data.update(payload)
    save(data)
    return {"status": "ok", "data": data}

# ----- legacy jobid endpoints (kept for backward-compat, no longer used by Sender) -----
@app.post("/jobids/add")
def add_jobid(body: JobIdBody):
    if not body.jobid:
        raise HTTPException(400, "jobid is empty")
    data = load()
    if body.jobid in data["jobids"]:
        return {"status": "exists", "data": data}
    data["jobids"].append(body.jobid)
    save(data)
    return {"status": "ok", "data": data}

@app.post("/jobids/remove")
def remove_jobid(body: JobIdBody):
    data = load()
    if body.jobid not in data["jobids"]:
        raise HTTPException(404, "jobid not found")
    data["jobids"].remove(body.jobid)
    if not data["jobids"]:
        data["jobids"] = [""]
    save(data)
    return {"status": "ok", "data": data}

@app.get("/debug")
def debug():
    return {
        "DATA_DIR": DATA_DIR,
        "FILE": FILE,
        "exists": os.path.exists(FILE),
        "size": os.path.getsize(FILE) if os.path.exists(FILE) else 0,
        "active_receivers": len(receivers_active()),
    }
