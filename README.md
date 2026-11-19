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
```

## 运行测试

```bash
uv run pytest
```
