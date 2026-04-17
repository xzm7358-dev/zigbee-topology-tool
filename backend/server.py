"""
FastAPI 服务 - WebSocket 推送拓扑数据 + REST API
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from collector import ZigbeeTopologyCollector

logger = logging.getLogger(__name__)

# ── 全局状态 ────────────────────────────────────────
class AppState:
    def __init__(self):
        self.collector: Optional[ZigbeeTopologyCollector] = None
        self.latest_snapshot: dict = {}
        self.ws_clients: list[WebSocket] = []
        self.collect_task: Optional[asyncio.Task] = None
        self.history: list[dict] = []  # 最近 N 个快照
        self.max_history = 360  # 10s * 360 = 1h

state = AppState()


async def on_snapshot(snapshot: dict):
    """采集回调：广播给所有 WebSocket 客户端"""
    state.latest_snapshot = snapshot

    # 存历史
    state.history.append(snapshot)
    if len(state.history) > state.max_history:
        state.history = state.history[-state.max_history:]

    # 广播
    data = json.dumps(snapshot, ensure_ascii=False)
    disconnected = []
    for ws in state.ws_clients:
        try:
            await ws.send_text(data)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        state.ws_clients.remove(ws)


# ── Lifespan ────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("服务启动")
    yield
    # 清理
    if state.collect_task:
        state.collect_task.cancel()
    logger.info("服务关闭")


app = FastAPI(title="Zigbee Topology Tool", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── REST API ────────────────────────────────────────
@app.post("/api/connect")
async def connect_coordinator(port: str = "/dev/ttyUSB0", baudrate: int = 115200):
    """连接 Coordinator 并启动采集"""
    if state.collect_task and not state.collect_task.done():
        return {"status": "already_running"}

    state.collector = ZigbeeTopologyCollector(port, baudrate)
    state.collector.on_update(on_snapshot)

    # 启动采集循环
    state.collect_task = asyncio.create_task(state.collector.run(interval=10))
    return {"status": "connecting", "port": port}


@app.post("/api/disconnect")
async def disconnect_coordinator():
    """停止采集"""
    if state.collect_task:
        state.collect_task.cancel()
        state.collect_task = None
    return {"status": "disconnected"}


@app.get("/api/snapshot")
async def get_snapshot():
    """获取最新快照"""
    return state.latest_snapshot or {"nodes": [], "links": [], "alerts": []}


@app.get("/api/history")
async def get_history(count: int = 20):
    """获取最近 N 个快照（时序回放用）"""
    return state.history[-count:]


@app.get("/api/status")
async def get_status():
    """服务状态"""
    return {
        "connected": state.collector is not None and state.collect_task and not state.collect_task.done(),
        "clients": len(state.ws_clients),
        "history_size": len(state.history),
        "latest_nodes": len(state.latest_snapshot.get("nodes", [])),
        "latest_alerts": len(state.latest_snapshot.get("alerts", [])),
    }


# ── WebSocket ───────────────────────────────────────
@app.websocket("/ws/topology")
async def ws_topology(ws: WebSocket):
    await ws.accept()
    state.ws_clients.append(ws)
    logger.info(f"WebSocket 客户端连接, 当前 {len(state.ws_clients)} 个")

    # 立即推送最新快照
    if state.latest_snapshot:
        await ws.send_text(json.dumps(state.latest_snapshot, ensure_ascii=False))

    try:
        while True:
            # 保持连接，等待客户端断开
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if ws in state.ws_clients:
            state.ws_clients.remove(ws)
        logger.info(f"WebSocket 客户端断开, 剩余 {len(state.ws_clients)} 个")


# ── 静态文件（前端）────────────────────────────────
@app.get("/")
async def index():
    return FileResponse("../frontend/index.html")
