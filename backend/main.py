"""
FastAPI 服务 - 支持真实 EZSP 和模拟两种模式

启动:
  模拟模式:   python main.py --mock
  真实模式:   python main.py --port /dev/ttyUSB0
"""

import argparse
import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pathlib import Path

# 模块
from channel_analyzer import ChannelAnalyzer
from history_store import HistoryStore
import channel_api
import history_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


class AppState:
    def __init__(self):
        self.collector = None
        self.latest_snapshot: dict = {}
        self.ws_clients: list[WebSocket] = []
        self.collect_task: Optional[asyncio.Task] = None
        self.history: list[dict] = []
        self.max_history = 360
        self._mock_collector = None
        self._mock_interval = 10
        self.channel_analyzer = ChannelAnalyzer()
        self.history_store = HistoryStore(str(Path(__file__).parent / "topology_history.db"))
        self._history_save_counter = 0
        self._history_save_every = 6  # 每6个快照(60s)存一次到SQLite

state = AppState()


async def on_snapshot(snapshot: dict):
    state.latest_snapshot = snapshot

    # 内存历史 (WebSocket 推送用)
    state.history.append(snapshot)
    if len(state.history) > state.max_history:
        state.history = state.history[-state.max_history:]

    # 持久化到 SQLite (降低频率，避免IO过频)
    state._history_save_counter += 1
    if state._history_save_counter >= state._history_save_every:
        state._history_save_counter = 0
        try:
            state.history_store.save_topology_snapshot(snapshot)
        except Exception as e:
            logger.error(f"保存历史快照失败: {e}")

    # WebSocket 推送
    data = json.dumps(snapshot, ensure_ascii=False)
    disconnected = []
    for ws in state.ws_clients:
        try:
            await ws.send_text(data)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        state.ws_clients.remove(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("服务启动")
    if state._mock_collector:
        state.collector = state._mock_collector
        state.collector.on_update(on_snapshot)
        state.collect_task = asyncio.create_task(state.collector.run(interval=state._mock_interval))
        logger.info("模拟模式已启动")
    yield
    if state.collect_task:
        state.collect_task.cancel()
    logger.info("服务关闭")


app = FastAPI(title="Zigbee Topology Tool", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# 注册路由
app.include_router(channel_api.router)
app.include_router(history_api.router)


# ── REST API ──
@app.post("/api/connect")
async def connect_coordinator(port: str = "/dev/ttyUSB0", baudrate: int = 115200):
    if state.collect_task and not state.collect_task.done():
        return {"status": "already_running"}
    try:
        from collector import ZigbeeTopologyCollector
        state.collector = ZigbeeTopologyCollector(port, baudrate)
    except Exception as e:
        return {"status": "error", "message": str(e)}
    state.collector.on_update(on_snapshot)
    state.collect_task = asyncio.create_task(state.collector.run(interval=10))
    return {"status": "connecting", "port": port}


@app.post("/api/disconnect")
async def disconnect_coordinator():
    if state.collect_task:
        state.collect_task.cancel()
        state.collect_task = None
    return {"status": "disconnected"}


@app.get("/api/snapshot")
async def get_snapshot():
    return state.latest_snapshot or {"nodes": [], "links": [], "alerts": []}


@app.get("/api/history")
async def get_history(count: int = 20):
    return state.history[-count:]


@app.get("/api/status")
async def get_status():
    db_range = state.history_store.get_time_range()
    return {
        "connected": state.collector is not None and state.collect_task and not state.collect_task.done(),
        "clients": len(state.ws_clients),
        "memory_history": len(state.history),
        "db_topology_count": db_range.get("topology", {}).get("count", 0),
        "db_events_count": db_range.get("events", {}).get("count", 0),
    }


# ── WebSocket ──
@app.websocket("/ws/topology")
async def ws_topology(ws: WebSocket):
    await ws.accept()
    state.ws_clients.append(ws)
    if state.latest_snapshot:
        await ws.send_text(json.dumps(state.latest_snapshot, ensure_ascii=False))
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if ws in state.ws_clients:
            state.ws_clients.remove(ws)


# ── 静态文件 ──
@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/css/{filename}")
async def css(filename: str):
    return FileResponse(FRONTEND_DIR / "css" / filename)


@app.get("/channel")
async def channel_page():
    return FileResponse(FRONTEND_DIR / "channel.html")


@app.get("/replay")
async def replay_page():
    return FileResponse(FRONTEND_DIR / "replay.html")


@app.get("/src/{filename}")
async def src(filename: str):
    fpath = FRONTEND_DIR / "src" / filename
    if fpath.exists():
        return FileResponse(fpath)
    return FileResponse(fpath)


# ── 启动入口 ──
def main():
    parser = argparse.ArgumentParser(description="Zigbee Topology Tool")
    parser.add_argument("--mock", action="store_true", help="使用模拟数据")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="串口设备")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--interval", type=int, default=10, help="采集间隔(秒)")
    args = parser.parse_args()

    import uvicorn

    if args.mock:
        from mock_collector import MockCollector
        state._mock_collector = MockCollector(num_routers=8, num_seds=12)
        state._mock_interval = args.interval

    # 注入共享实例到 API 模块
    channel_api.analyzer = state.channel_analyzer
    history_api.history_store = state.history_store
    history_api.channel_analyzer = state.channel_analyzer

    uvicorn.run(app, host=args.host, port=8000)


if __name__ == "__main__":
    main()
