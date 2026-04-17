"""
时序回放 API
"""

import logging
from typing import Optional
from datetime import datetime, timedelta

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from pathlib import Path

from history_store import HistoryStore
from channel_analyzer import ChannelAnalyzer, NodeEdScan, EdScanReading

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/history", tags=["history"])

# 外部注入
history_store: Optional[HistoryStore] = None
channel_analyzer: Optional[ChannelAnalyzer] = None

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


@router.get("/range")
async def get_time_range():
    """获取数据时间范围"""
    if history_store is None:
        return {"error": "not initialized"}
    return history_store.get_time_range()


@router.get("/topology")
async def get_topology_history(
    start: str = Query(..., description="ISO8601 开始时间"),
    end: str = Query(..., description="ISO8601 结束时间"),
    step: int = Query(1, description="每隔N条取一条"),
):
    """获取时间范围内的拓扑快照列表"""
    if history_store is None:
        return {"error": "not initialized"}
    snapshots = history_store.get_topology_range(start, end, step)
    return {"count": len(snapshots), "snapshots": snapshots}


@router.get("/topology/at")
async def get_topology_at(timestamp: str = Query(...)):
    """获取指定时间点的拓扑快照"""
    if history_store is None:
        return {"error": "not initialized"}
    snapshot = history_store.get_topology_at(timestamp)
    return snapshot or {"nodes": [], "links": [], "alerts": []}


@router.get("/edscan")
async def get_edscan_history(
    start: str = Query(...),
    end: str = Query(...),
):
    """获取时间范围内的 ED Scan 数据"""
    if history_store is None:
        return {"error": "not initialized"}
    return history_store.get_ed_scan_range(start, end)


@router.get("/events")
async def get_events(
    start: str = Query(...),
    end: str = Query(...),
    severity: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = Query(100, le=500),
):
    """查询事件日志"""
    if history_store is None:
        return {"error": "not initialized"}
    return history_store.get_events(start, end, severity, event_type, limit)


@router.get("/timeline")
async def get_timeline(
    start: str = Query(...),
    end: str = Query(...),
    ticks: int = Query(20, le=100),
):
    """获取时间轴刻度点"""
    if history_store is None:
        return {"error": "not initialized"}
    return {"timestamps": history_store.get_timeline_ticks(start, end, ticks)}


@router.get("/cleanup")
async def cleanup(days: int = Query(7)):
    """清理历史数据"""
    if history_store is None:
        return {"error": "not initialized"}
    history_store.cleanup(days)
    return {"status": "ok", "cleaned_before_days": days}


@router.get("/replay-page")
async def replay_page():
    """时序回放页面"""
    return FileResponse(FRONTEND_DIR / "replay.html")
