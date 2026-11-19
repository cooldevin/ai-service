# main.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="ai-service")

class HelloReq(BaseModel):
    name: str = Field(min_length=1, description="姓名")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/hello")
def hello(req: HelloReq):
    # 这里先复刻你最熟的 Java 接口：把 req 当成 DTO，返回值当成 Response
    if req.name == "error":
        raise HTTPException(status_code=400, detail="name 不能是 error")
    return {"message": f"你好，{req.name}"}