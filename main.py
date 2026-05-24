import json, os
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()
FILE = "teleport.json"

# ---------- helpers ----------
def ensure_file():
    if not os.path.exists(FILE):
        with open(FILE, "w") as f:
            json.dump({}, f)

def load():
    ensure_file()
    with open(FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save(data):
    with open(FILE, "w") as f:
        json.dump(data, f, indent=2)

# ---------- routes ----------
@app.get("/")
def home():
    return {"message": "API is running"}

# ดูข้อมูลทั้งหมด
@app.get("/teleport")
def teleport():
    return load()

