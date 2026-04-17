"""
FastAPI 服务 - 信道干扰分析 API
"""

import logging
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from channel_analyzer import ChannelAnalyzer, NodeEdScan, EdScanReading
from datetime import datetime

logger = logging.getLogger(__name__)

# 全局分析器实例 (在 main.py 中注入)
analyzer: Optional[ChannelAnalyzer] = None

router = APIRouter(prefix="/api/channel", tags=["channel"])


class EdScanPayload(BaseModel):
    """ED Scan 上报数据"""
    node_nwk: str
    node_type: str = "Router"
    readings: list[dict]  # [{"channel": 11, "rssi": -65}, ...]
    timestamp: Optional[str] = None


@router.post("/scan")
async def submit_scan(payload: EdScanPayload):
    """接收节点 ED Scan 数据"""
    if analyzer is None:
        return {"error": "analyzer not initialized"}

    scan = NodeEdScan(
        node_nwk=payload.node_nwk,
        node_type=payload.node_type,
        readings=[
            EdScanReading(
                channel=r["channel"],
                rssi=r["rssi"],
                timestamp=payload.timestamp or datetime.utcnow().isoformat() + "Z",
            )
            for r in payload.readings
        ],
        timestamp=payload.timestamp or datetime.utcnow().isoformat() + "Z",
    )

    analyzer.add_scan(scan)
    return {"status": "ok", "node": payload.node_nwk, "channels": len(payload.readings)}


@router.get("/heatmap")
async def get_heatmap():
    """获取信道干扰热力图数据"""
    if analyzer is None:
        return {"error": "analyzer not initialized"}
    return analyzer.generate_heatmap_data()


@router.get("/trend")
async def get_trend(node_nwk: str, channel: int = Query(25), count: int = Query(30)):
    """获取某节点某信道的 RSSI 趋势"""
    if analyzer is None:
        return {"error": "analyzer not initialized"}
    return analyzer.generate_trend_data(node_nwk, channel, count)


@router.get("/recommendations")
async def get_recommendations():
    """获取信道推荐"""
    if analyzer is None:
        return {"error": "analyzer not initialized"}
    data = analyzer.generate_heatmap_data()
    return {"recommendations": data.get("recommendations", []), "alerts": data.get("alerts", [])}


@router.post("/mock-scan")
async def inject_mock_scan():
    """注入模拟 ED Scan 数据（开发调试用）"""
    import random

    if analyzer is None:
        return {"error": "analyzer not initialized"}

    # 模拟3个节点的 ED Scan
    nodes = [
        {"nwk": "0x0001", "type": "Router"},
        {"nwk": "0x0003", "type": "Router"},
        {"nwk": "0x0005", "type": "Router"},
    ]

    for node in nodes:
        readings = []
        for ch in range(11, 27):
            # 模拟 WiFi 干扰: 信道 16-19 (WiFi 6) 和 21-24 (WiFi 11) 有较强信号
            base_noise = random.randint(-90, -75)

            # WiFi 6 干扰 (信道 16-19)
            if 16 <= ch <= 19:
                base_noise = random.randint(-55, -35)
            # WiFi 11 干扰 (信道 21-24)
            elif 21 <= ch <= 24:
                base_noise = random.randint(-50, -30)
            # WiFi 1 干扰 (信道 11-14), 较弱
            elif 11 <= ch <= 14 and random.random() < 0.3:
                base_noise = random.randint(-65, -50)
            # 信道 25-26 较安静
            elif ch >= 25:
                base_noise = random.randint(-92, -78)

            # 加入随机波动
            base_noise += random.randint(-3, 3)

            readings.append({"channel": ch, "rssi": base_noise})

        scan = NodeEdScan(
            node_nwk=node["nwk"],
            node_type=node["type"],
            readings=[EdScanReading(channel=r["channel"], rssi=r["rssi"]) for r in readings],
            timestamp=datetime.utcnow().isoformat() + "Z",
        )
        analyzer.add_scan(scan)

    return {"status": "ok", "injected": len(nodes)}
