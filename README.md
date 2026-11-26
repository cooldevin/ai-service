# ai-service

基于 FastAPI 的最小 HTTP 服务示例，提供 `/health` 健康检查和 `/hello` 问候接口。

## 启动服务

```bash
uv run uvicorn ai_service.main:app --reload
```

## 验证接口

```bash
# 1. 健康检查
curl http://127.0.0.1:8000/health

# 2. 调用 /hello 接口
curl -X POST http://127.0.0.1:8000/hello \
  -H "Content-Type: application/json" \
  -d '{"name": "world"}'
  
# 3.通过user_id查询用户信息
curl http://127.0.0.1:8000/users/1
# 4.查询用户分页信息
curl "http://127.0.0.1:8000/users/pageList?page=1&size=10"

#4.新增用户
#正常情况下：
  curl -X POST http://127.0.0.1:8000/create \
  -H "Content-Type: application/json" \
  -d '{"name":"Carol","age":28,"email":"carol@example.com","role":"user","address":{"city":"杭州","street":"西湖区文一西路"}}'
#异常情况下：
  curl -X POST http://127.0.0.1:8000/create \
  -H "Content-Type: application/json" \
  -d '{"name":"C","age":-1,"email":"carol@example.com","role":"user","address":{"city":"杭州","street":"西湖区文一西路"}}'
```
```bash

测试：uv run pytest
LOG_LEVEL：默认INFO ,改 【.env】 即可
LLM_API_KEY：现在是空占位，调大模型时再填
仓库只保留 【.env.example】 真实【.env】不能提交
```
## 运行测试

```bash
uv run pytest
```

## 异步测试效果
    async-sleep 5并发 2.0秒
    async-blocked 5并发 10.0秒 (async里写time.sleep会堵死事件循环)
    sync-sleep 5并发 2.0秒 占五个线程
