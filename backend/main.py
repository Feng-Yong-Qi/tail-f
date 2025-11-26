from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
from sse_starlette.sse import EventSourceResponse
from backend.log_core import LogManager
import os

app = FastAPI(title="Tail-f Web Viewer")

# 启用 GZip 压缩中间件（减少带宽占用 70-90%）
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=6)

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory="static"), name="static")

log_manager = LogManager()

@app.get("/", response_class=HTMLResponse)
async def read_root():
    """渲染主页"""
    # 直接返回静态文件
    return FileResponse("static/index.html")

@app.get("/api/files")
async def get_files():
    """获取日志文件列表（包括远程服务器）"""
    files = await log_manager.get_file_list_async()
    return JSONResponse(content=files)

@app.get("/api/logs/stream")
async def stream_log(file: str = Query(..., description="Log file identifier")):
    """SSE 实时日志流接口"""
    # 使用 sse-starlette 处理流式响应
    return EventSourceResponse(
        log_manager.tail_file(file, {}),
        ping=2 # 心跳保持连接
    )

@app.post("/api/logs/clear")
async def clear_log(request: Request):
    """清空日志文件（本地或远程）"""
    data = await request.json()
    file_name = data.get("file")
    success = await log_manager.clear_log_async(file_name)
    if success:
        return {"status": "success", "message": f"日志 {file_name} 已清空"}
    return JSONResponse(status_code=400, content={"status": "error", "message": "清空日志失败"})

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    await log_manager.cleanup()

if __name__ == "__main__":
    import uvicorn
    # 获取配置文件中的端口
    config = log_manager.config.get("server", {})
    host = config.get("host", "0.0.0.0")
    port = config.get("port", 8000)
    
    print(f"🚀 Starting Tail-f Web on http://{host}:{port}")
    uvicorn.run("backend.main:app", host=host, port=port, reload=True)
