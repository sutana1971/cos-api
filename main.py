import json, os, random
from typing import Optional, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

# ใช้ disk path ถ้ามี (บน Render), ไม่งั้นใช้ current folder (ตอน dev บนเครื่อง)
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
FILE = os.path.join(DATA_DIR, "teleport.json")

DEFAULT = {"teleport": 0, "jobids": [], "placeid": 0, "mush": 0}

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

# ---------- models ----------
class ConfigUpdate(BaseModel):
    teleport: Optional[int] = None
    jobid: Optional[str] = None          # legacy: sets pool to [this]
    jobids: Optional[List[str]] = None   # full replace of pool
    placeid: Optional[int] = None
    mush: Optional[int] = None

class JobIdBody(BaseModel):
    jobid: str

# ---------- routes ----------
@app.get("/")
def home():
    return {"message": "API is running"}

# Roblox + CLI อ่านค่าปัจจุบัน
# คืน jobid เป็น string เดี่ยว (สุ่มมาจาก pool) เพื่อให้ Lua ใช้ data.jobid ได้เหมือนเดิม
@app.get("/teleport")
def teleport():
    data = load()
    pool = data.get("jobids") or []
    chosen = random.choice(pool) if pool else ""
    return {
        "teleport": data.get("teleport", 0),
        "placeid":  data.get("placeid", 0),
        "mush":     data.get("mush", 0),
        "jobid":    chosen,
    }

# สำหรับ CLI / debug — เห็น pool ทั้งหมด
@app.get("/teleport/all")
def teleport_all():
    return load()

# CLI ส่งค่าใหม่มาอัปเดต (เฉพาะ field ที่ส่งมาจะถูกแทนที่)
@app.post("/config")
def update_config(update: ConfigUpdate):
    data = load()
    payload = update.dict(exclude_none=True)

    # legacy: ถ้าส่ง jobid เดี่ยวมา ให้แทนที่ pool ทั้งก้อน
    if "jobid" in payload:
        single = payload.pop("jobid")
        payload["jobids"] = [single] if single else []

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

# ลบ jobid ออกจาก pool (ต้องเหลืออย่างน้อย 1)
@app.post("/jobids/remove")
def remove_jobid(body: JobIdBody):
    data = load()
    if body.jobid not in data["jobids"]:
        raise HTTPException(404, "jobid not found")
    if len(data["jobids"]) <= 1:
        raise HTTPException(400, "Must keep at least one jobid")
    data["jobids"].remove(body.jobid)
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
