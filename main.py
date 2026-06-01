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
    # ensure all default keys exist
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
_rate_window = 0   # epoch minute we're tracking
_rate_count  = 0   # how many teleport=1 responses sent this minute

def _current_minute() -> int:
    return int(time.time() // 60)

def try_consume(limit: int):
    """Returns (allowed: bool, count_after: int).
    If allowed, count is incremented. If not, count stays the same."""
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
        # if minute rolled over, report a fresh window
        used = 0 if now_min != _rate_window else _rate_count
    return {
        "limit": limit,
        "used":  used,
        "remaining": max(0, limit - used),
        "window_minute": now_min,
        "seconds_until_reset": 60 - int(time.time()) % 60,
    }

# ---------- models ----------
class ConfigUpdate(BaseModel):
    teleport: Optional[int] = None
    jobid: Optional[str] = None          # legacy: sets pool to [this]
    jobids: Optional[List[str]] = None   # full replace of pool
    placeid: Optional[int] = None
    mush: Optional[int] = None
    limit: Optional[int] = None

class JobIdBody(BaseModel):
    jobid: str

# ---------- routes ----------
@app.get("/")
def home():
    return {"message": "API is running"}

# Roblox + CLI อ่านค่าปัจจุบัน
# Roblox ต้องส่ง ?shoom=<value> มาด้วย เพื่อให้ API นับ rate-limit เฉพาะคนที่ teleport ได้จริง
# - teleport=0 in config         -> return 0 (no count)
# - shoom < mush                 -> return 0 (no count)
# - rate-limit reached            -> return 0 (no count)
# - all pass                      -> return 1 (count +1)
@app.get("/teleport")
def teleport(shoom: Optional[int] = Query(None)):
    data = load()
    pool = data.get("jobids") or []
    chosen = random.choice(pool) if pool else ""

    tp_flag = int(data.get("teleport", 0))
    mush    = int(data.get("mush", 0))
    limit   = int(data.get("limit", 15))

    if tp_flag == 1:
        # strict: ถ้าไม่ส่ง shoom มา ถือว่าเป็น 0
        client_shoom = int(shoom) if shoom is not None else 0
        if client_shoom < mush:
            tp_flag = 0  # ไม่ผ่านเงื่อนไข mush -> ไม่นับ slot
        else:
            allowed, _ = try_consume(limit)
            if not allowed:
                tp_flag = 0  # rate-limit เต็ม -> ไม่นับ (ไม่ได้ teleport)

    return {
        "teleport": tp_flag,
        "placeid":  data.get("placeid", 0),
        "mush":     mush,
        "jobid":    chosen,
    }

# สำหรับ CLI / debug — เห็น pool ทั้งหมด
@app.get("/teleport/all")
def teleport_all():
    return load()

# ดูสถานะ rate limit ปัจจุบัน
@app.get("/ratelimit")
def ratelimit():
    data = load()
    return rate_status(int(data.get("limit", 15)))

# CLI ส่งค่าใหม่มาอัปเดต (เฉพาะ field ที่ส่งมาจะถูกแทนที่)
@app.post("/config")
def update_config(update: ConfigUpdate):
    data = load()
    payload = update.dict(exclude_none=True)

    # legacy: ถ้าส่ง jobid เดี่ยวมา ให้แทนที่ pool ทั้งก้อน
    if "jobid" in payload:
        single = payload.pop("jobid")
        payload["jobids"] = [single] if single else []

    # กัน limit ติดลบ
    if "limit" in payload:
        payload["limit"] = max(0, int(payload["limit"]))

    data.update(payload)
    save(data)
    return {"status": "ok", "data": data}

# เพิ่ม jobid เข้า pool
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

# ลบ jobid ออกจาก pool
# ถ้าลบจนหมด จะแทนที่ด้วย [""] เพื่อให้ client ตัวที่ได้ "" ไป join server แบบสุ่ม
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
    }
