# main.py
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from enum import Enum


app = FastAPI(title="ai-service")


class HelloReq(BaseModel):
    name: str = Field(min_length=1, description="姓名")


class UserOut(BaseModel):
    user_id: int
    name: str
    email: str

class Role(str,Enum):
    ADMIN = "admin"
    USER = "user"
    GUEST = "guest"

class Address(BaseModel):
    city: str = Field(min_length = 1)
    street:str = Field(min_length = 1)

class UserCreate(BaseModel):
    name: str = Field(min_length =2, max_length = 20,description = "姓名请输入2-20个字符")
    age: int = Field(ge=0,le=500,description="年龄，0-500")
    email: str
    role: Role = Role.USER
    address: Address |None = None
# 用户模拟数据
USERS = {
    1: {"user_id": 1, "name": "zhangsan", "email": "zhangsan@email.com"},
    2: {"user_id": 2, "name": "lisi", "email": "lisi@email.com"},
    3: {"user_id": 3, "name": "wangwu", "email": "wangwu@email.com"},
}

@app.post("/create",response_model = UserOut,status_code = 201)
def create_user(req: UserCreate):
    new_id = max(USERS) + 1 if USERS else 1
    user = {"user_id":new_id,"name":req.name, "email" : req.email}
    USERS[new_id] = user
    return user

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/hello")
def hello(req: HelloReq):
    # 这里先复刻你最熟的 Java 接口：把 req 当成 DTO，返回值当成 Response
    if req.name == "error":
        raise HTTPException(status_code=400, detail="name 不能是 error")
    return {"message": f"你好，{req.name}"}

@app.get("/users/pageList", response_model=list[UserOut])
def get_users(page: int = Query(1, ge=1), size: int = Query(10, ge=1, le=10)):
    user_list = list(USERS.values())
    return user_list[(page - 1) * size:page * size]

@app.get("/users/{user_id}", response_model=UserOut)
async def get_user(user_id: int):
    user = USERS.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail=f"user {user_id} not found")
    return user

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    code = {400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found"}.get(exc.status_code, "ERROR")
    return JSONResponse(status_code=exc.status_code, content={"code": code, "message": exc.detail})
