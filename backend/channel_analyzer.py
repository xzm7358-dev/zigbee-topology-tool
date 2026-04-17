"""
信道干扰分析器 - ED Scan 数据采集、聚合、诊断

Zigbee 2.4GHz 信道 (11-26) 与 WiFi 信道重叠关系:
  WiFi Ch 1 (2412MHz) → Zigbee Ch 11-14
  WiFi Ch 6 (2437MHz) → Zigbee Ch 16-19  
  WiFi Ch 11 (2462MHz) → Zigbee Ch 21-24
  
  推荐信道: 25 (远离 WiFi), 15, 20, 25

ED Scan RSSI 解读:
  -90 ~ -70 dBm: 安静，无干扰
  -70 ~ -60 dBm: 轻度噪声
  -60 ~ -50 dBm: 明显干扰
  -50 ~ -30 dBm: 严重干扰
  > -30 dBm: 极强干扰，几乎不可用
"""

import logging
from datetime import datetime
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Zigbee 2.4GHz 信道频率映射
ZIGBEE_CHANNELS = {
    11: 2405, 12: 2410, 13: 2415, 14: 2420, 15: 2425,
    16: 2430, 17: 2435, 18: 2440, 19: 2445, 20: 2450,
    21: 2455, 22: 2460, 23: 2465, 24: 2470, 25: 2475,
    26: 2480,
}

# WiFi 信道与 Zigbee 重叠
WIFI_OVERLAP = {
    "WiFi 1 (2412)": [11, 12, 13, 14],
    "WiFi 2 (2417)": [12, 13, 14, 15],
    "WiFi 3 (2422)": [13, 14, 15, 16],
    "WiFi 4 (2427)": [14, 15, 16, 17],
    "WiFi 5 (2432)": [15, 16, 17, 18],
    "WiFi 6 (2437)": [16, 17, 18, 19],
    "WiFi 7 (2442)": [17, 18, 19, 20],
    "WiFi 8 (2447)": [18, 19, 20, 21],
    "WiFi 9 (2452)": [19, 20, 21, 22],
    "WiFi 10 (2457)": [20, 21, 22, 23],
    "WiFi 11 (2462)": [21, 22, 23, 24],
    "WiFi 12 (2467)": [22, 23, 24, 25],
    "WiFi 13 (2472)": [23, 24, 25, 26],
}

# 反向映射: Zigbee channel → 可能干扰的 WiFi 信道
ZIGBEE_WIFI_MAP = {}
for wifi_ch, zigbee_chs in WIFI_OVERLAP.items():
    for zch in zigbee_chs:
        if zch not in ZIGBEE_WIFI_MAP:
            ZIGBEE_WIFI_MAP[zch] = []
        ZIGBEE_WIFI_MAP[zch].append(wifi_ch)


@dataclass
class EdScanReading:
    """单次 ED Scan 读数"""
    channel: int
    rssi: int  # dBm, 负值
    timestamp: str = ""


@dataclass
class NodeEdScan:
    """单个节点的 ED Scan 结果"""
    node_nwk: str
    node_type: str
    readings: list[EdScanReading] = field(default_factory=list)
    timestamp: str = ""


class ChannelAnalyzer:
    """
    信道干扰分析器
    
    功能:
    1. 收集多节点 ED Scan 数据
    2. 生成信道干扰热力图数据
    3. WiFi 干扰诊断
    4. 信道推荐
    """

    def __init__(self):
        # node_nwk → list of NodeEdScan (历史)
        self.scan_history: dict[str, list[NodeEdScan]] = {}
        self.max_history_per_node = 60  # 保留最近60次

    def add_scan(self, scan: NodeEdScan):
        """添加一次 ED Scan 结果"""
        nwk = scan.node_nwk
        if nwk not in self.scan_history:
            self.scan_history[nwk] = []
        self.scan_history[nwk].append(scan)
        if len(self.scan_history[nwk]) > self.max_history_per_node:
            self.scan_history[nwk] = self.scan_history[nwk][-self.max_history_per_node:]

    def get_latest_scans(self) -> dict[str, NodeEdScan]:
        """获取每个节点最新一次 ED Scan"""
        result = {}
        for nwk, history in self.scan_history.items():
            if history:
                result[nwk] = history[-1]
        return result

    def generate_heatmap_data(self) -> dict:
        """
        生成热力图数据 (供前端渲染)
        
        返回格式:
        {
            "channels": [11, 12, ..., 26],
            "nodes": ["0x0001", "0x0002", ...],
            "matrix": [[rssi, rssi, ...], ...],  // nodes × channels
            "wifi_overlap": {...},
            "recommendations": [...],
            "current_channel": 25,
            "timestamp": "..."
        }
        """
        latest_scans = self.get_latest_scans()
        if not latest_scans:
            return {"channels": list(ZIGBEE_CHANNELS.keys()), "nodes": [], "matrix": [],
                    "wifi_overlap": WIFI_OVERLAP, "recommendations": [], "alerts": []}

        channels = list(ZIGBEE_CHANNELS.keys())
        nodes = sorted(latest_scans.keys())
        matrix = []

        for nwk in nodes:
            scan = latest_scans[nwk]
            reading_map = {r.channel: r.rssi for r in scan.readings}
            row = [reading_map.get(ch, -100) for ch in channels]  # 未扫描的信道用-100
            matrix.append(row)

        # 诊断
        alerts = self._diagnose(latest_scans)
        recommendations = self._recommend_channel(latest_scans)

        return {
            "channels": channels,
            "channel_frequencies": {ch: freq for ch, freq in ZIGBEE_CHANNELS.items()},
            "nodes": nodes,
            "node_types": {nwk: latest_scans[nwk].node_type for nwk in nodes},
            "matrix": matrix,
            "wifi_overlap": WIFI_OVERLAP,
            "zigbee_wifi_map": ZIGBEE_WIFI_MAP,
            "recommendations": recommendations,
            "alerts": alerts,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

    def _diagnose(self, scans: dict[str, NodeEdScan]) -> list[dict]:
        """
        信道干扰诊断
        
        检测规则:
        1. 单信道强干扰: 某信道 RSSI > -50
        2. WiFi 频段干扰: 连续4个信道都高 RSSI → 疑似 WiFi
        3. 全频段噪声: 所有信道平均 RSSI > -70
        4. 信道间差异大: 可能存在定向干扰源
        """
        alerts = []

        # 合并所有节点的读数，取每个信道的最大 RSSI
        channel_max_rssi = {}
        for nwk, scan in scans.items():
            for r in scan.readings:
                if r.channel not in channel_max_rssi or r.rssi > channel_max_rssi[r.channel]:
                    channel_max_rssi[r.channel] = r.rssi

        # 1. 单信道强干扰
        for ch, rssi in channel_max_rssi.items():
            if rssi > -30:
                alerts.append({
                    "type": "extreme_interference",
                    "severity": "critical",
                    "channel": ch,
                    "message": f"信道 {ch} 极强干扰 (RSSI={rssi}dBm)，几乎不可用",
                    "wifi_suspect": ZIGBEE_WIFI_MAP.get(ch, []),
                })
            elif rssi > -50:
                alerts.append({
                    "type": "strong_interference",
                    "severity": "warning",
                    "channel": ch,
                    "message": f"信道 {ch} 明显干扰 (RSSI={rssi}dBm)",
                    "wifi_suspect": ZIGBEE_WIFI_MAP.get(ch, []),
                })

        # 2. WiFi 频段干扰检测
        for wifi_ch, zigbee_chs in WIFI_OVERLAP.items():
            # 连续4个信道中有3个以上 RSSI > -60
            noisy_count = sum(
                1 for zch in zigbee_chs
                if channel_max_rssi.get(zch, -100) > -60
            )
            if noisy_count >= 3:
                alerts.append({
                    "type": "wifi_interference",
                    "severity": "critical" if noisy_count == 4 else "warning",
                    "channel": zigbee_chs,
                    "message": f"疑似 {wifi_ch} 干扰 (Zigbee {zigbee_chs} 中{noisy_count}个信道受影响)",
                    "wifi_channel": wifi_ch,
                })

        # 3. 全频段噪声
        if channel_max_rssi:
            avg_rssi = sum(channel_max_rssi.values()) / len(channel_max_rssi)
            if avg_rssi > -70:
                alerts.append({
                    "type": "broadband_noise",
                    "severity": "warning",
                    "message": f"全频段噪声偏高 (平均 RSSI={avg_rssi:.0f}dBm)，可能存在微波炉或蓝牙设备",
                })

        return alerts

    def _recommend_channel(self, scans: dict[str, NodeEdScan]) -> list[dict]:
        """
        信道推荐
        
        策略:
        1. 计算每个信道的干扰评分 (RSSI 越低越好)
        2. 优先推荐远离 WiFi 的信道 (15, 20, 25)
        3. 如果当前信道不是最优，建议迁移
        """
        # 合并所有节点的读数
        channel_rssi = {}
        for ch in ZIGBEE_CHANNELS:
            rssi_values = []
            for scan in scans.values():
                for r in scan.readings:
                    if r.channel == ch:
                        rssi_values.append(r.rssi)
            if rssi_values:
                channel_rssi[ch] = max(rssi_values)  # 取最差值
            else:
                channel_rssi[ch] = -100  # 未扫描=安静

        # 评分: RSSI 越低越好
        scored = []
        for ch, rssi in channel_rssi.items():
            # 基础分: RSSI 的负值 (越大越好)
            score = -rssi

            # WiFi 躲避加分
            wifi_neighbors = len(ZIGBEE_WIFI_MAP.get(ch, []))
            if wifi_neighbors == 0:
                score += 20  # 完全不与 WiFi 重叠 (信道25, 26)
            elif wifi_neighbors <= 2:
                score += 5

            # 边缘信道加分 (信道15, 20, 25 在 WiFi 间隙)
            if ch in [15, 20, 25]:
                score += 10

            scored.append({
                "channel": ch,
                "rssi": rssi,
                "score": round(score, 1),
                "wifi_overlap": ZIGBEE_WIFI_MAP.get(ch, []),
            })

        # 按分数排序
        scored.sort(key=lambda x: x["score"], reverse=True)

        # 标注推荐等级
        for i, rec in enumerate(scored):
            if i == 0:
                rec["rank"] = "🟢 最佳"
            elif i == 1:
                rec["rank"] = "🔵 次优"
            elif i == 2:
                rec["rank"] = "🟡 可用"
            elif rec["rssi"] > -50:
                rec["rank"] = "🔴 不推荐"
            else:
                rec["rank"] = "⚪ 一般"

        return scored

    def generate_trend_data(self, node_nwk: str, channel: int, count: int = 30) -> dict:
        """
        生成某节点某信道的 RSSI 趋势数据 (时序图用)
        """
        history = self.scan_history.get(node_nwk, [])
        recent = history[-count:]

        data_points = []
        for scan in recent:
            for r in scan.readings:
                if r.channel == channel:
                    data_points.append({
                        "timestamp": scan.timestamp,
                        "rssi": r.rssi,
                    })
                    break

        return {
            "node": node_nwk,
            "channel": channel,
            "data_points": data_points,
        }
