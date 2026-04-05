from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

# สร้างรูปแบบข้อมูล
class ScoreData(BaseModel):
    userId: int
    score: int

# test route
@app.get("/")
def home():
    return {"message": "API is running"}

# API endpoint
@app.post("/score")
def receive_score(data: ScoreData):
    print("User:", data.userId, "Score:", data.score)

    return {
        "status": "ok",
        "received": data
    }