import json, os
from typing import Optional
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

# ใช้ disk path ถ้ามี (บน Render), ไม่งั้นใช้ current folder (ตอน dev บนเครื่อง)
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
FILE = os.path.join(DATA_DIR, "teleport.json")

DEFAULT = {"teleport": 0, "jobid": "", "placeid": 0, "mush": 0}

# ---------- helpers ----------
def ensure_file():
    if not os.path.exists(FILE):
        with open(FILE, "w") as f:
            json.dump(DEFAULT, f, indent=2)

def load():
    ensure_file()
    with open(FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return dict(DEFAULT)

def save(data):
    with open(FILE, "w") as f:
        json.dump(data, f, indent=2)

# ---------- models ----------
class ConfigUpdate(BaseModel):
    teleport: Optional[int] = None
    jobid: Optional[str] = None
    placeid: Optional[int] = None
    mush: Optional[int] = None

# ---------- routes ----------
@app.get("/")
def home():
    return {"message": "API is running"}

# Roblox + CLI อ่านค่าปัจจุบัน
@app.get("/teleport")
def teleport():
    return load()

# CLI ส่งค่าใหม่มาอัปเดต (เฉพาะ field ที่ส่งมาจะถูกแทนที่)
@app.post("/config")
def update_config(update: ConfigUpdate):
    data = load()
    new_values = update.dict(exclude_none=True)
    data.update(new_values)
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
