"""
Zigbee Topology Collector - EZSP 数据采集层
通过 bellows 连接 Coordinator，轮询邻居表/路由表/子节点表
"""

import asyncio
import bellows.ezsp
import bellows.types as t
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class ZigbeeTopologyCollector:
    """从 Coordinator EZSP 接口采集拓扑数据"""

    def __init__(self, serial_port: str, baudrate: int = 115200):
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.ezsp = None
        self.coordinator_ieee = None
        self.coordinator_nwk = None
        self._callbacks = []  # 数据更新回调列表

    def on_update(self, callback):
        """注册数据更新回调，callback(snapshot) 会被调用"""
        self._callbacks.append(callback)

    async def _notify(self, snapshot):
        for cb in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(snapshot)
                else:
                    cb(snapshot)
            except Exception as e:
                logger.error(f"回调异常: {e}")

    async def connect(self):
        """连接 Coordinator"""
        self.ezsp = bellows.ezsp.EZSP()
        await self.ezsp.connect(self.serial_port, self.baudrate)

        (status, ieee) = await self.ezsp.getEui64()
        (status, nwk) = await self.ezsp.getNetworkParameters()

        self.coordinator_ieee = str(ieee)
        self.coordinator_nwk = 0x0000  # Coordinator always 0x0000

        logger.info(f"已连接 Coordinator: IEEE={self.coordinator_ieee}")
        return self.coordinator_ieee, self.coordinator_nwk

    # ── 邻居表 ──────────────────────────────────────
    async def read_neighbor_table(self):
        neighbors = []
        idx = 0

        while True:
            try:
                (status, entries) = await self.ezsp.getNeighborTable(idx)
                if status != 0:
                    break

                for entry in entries:
                    neighbors.append({
                        "nwk": entry.nwk,
                        "ieee": str(entry.ieee),
                        "lqi": entry.lqi,
                        "depth": entry.depth,
                        "relationship": self._decode_relationship(entry.relationship),
                    })
                    idx += 1

                if len(entries) < 8:
                    break

            except Exception as e:
                logger.warning(f"邻居表读取异常 @ idx={idx}: {e}")
                break

        return neighbors

    # ── 路由表 ──────────────────────────────────────
    async def read_routing_table(self):
        routes = []
        idx = 0

        while True:
            try:
                (status, entries) = await self.ezsp.getRoutingTable(idx)
                if status != 0:
                    break

                for entry in entries:
                    routes.append({
                        "dest_nwk": entry.destNwk,
                        "next_hop": entry.nextHop,
                        "status": self._decode_route_status(entry.status),
                        "age": entry.age,
                    })
                    idx += 1

                if len(entries) < 8:
                    break

            except Exception as e:
                logger.warning(f"路由表读取异常 @ idx={idx}: {e}")
                break

        return routes

    # ── 子节点表 ────────────────────────────────────
    async def read_child_table(self):
        children = []
        idx = 0

        while True:
            try:
                (status, child_data) = await self.ezsp.getChildData(idx)
                if status != 0:
                    break

                children.append({
                    "nwk": child_data.nwk,
                    "ieee": str(child_data.ieee),
                    "type": self._decode_node_type(child_data.type),
                })
                idx += 1

            except Exception as e:
                logger.warning(f"子节点表读取异常 @ idx={idx}: {e}")
                break

        return children

    # ── 诊断分析 ────────────────────────────────────
    def analyze(self, neighbors, routes, children):
        """对当前快照进行诊断分析，返回 alerts"""
        alerts = []

        # 弱链路检测
        for nb in neighbors:
            if nb["lqi"] < 50:
                alerts.append({
                    "type": "weak_link",
                    "severity": "critical",
                    "message": f"0x{nb['nwk']:04X} LQI={nb['lqi']} (极弱)",
                    "node": f"0x{nb['nwk']:04X}",
                })
            elif nb["lqi"] < 100:
                alerts.append({
                    "type": "weak_link",
                    "severity": "warning",
                    "message": f"0x{nb['nwk']:04X} LQI={nb['lqi']} (偏弱)",
                    "node": f"0x{nb['nwk']:04X}",
                })

        # 路由失败检测
        for r in routes:
            if "Failed" in r["status"]:
                alerts.append({
                    "type": "route_failed",
                    "severity": "critical",
                    "message": f"路由到 0x{r['dest_nwk']:04X} 发现失败",
                    "node": f"0x{r['dest_nwk']:04X}",
                })
            elif "Underway" in r["status"]:
                alerts.append({
                    "type": "route_discovery",
                    "severity": "warning",
                    "message": f"路由到 0x{r['dest_nwk']:04X} 正在发现中",
                    "node": f"0x{r['dest_nwk']:04X}",
                })

        # SED 孤儿检测（child 但不在邻居表中或 LQI 极低）
        child_nwks = {c["nwk"] for c in children if "Sleepy" in c["type"]}
        neighbor_nwks = {nb["nwk"] for nb in neighbors}
        for nwk in child_nwks:
            if nwk not in neighbor_nwks:
                alerts.append({
                    "type": "orphan_sed",
                    "severity": "warning",
                    "message": f"SED 0x{nwk:04X} 是子节点但不在邻居表中",
                    "node": f"0x{nwk:04X}",
                })

        return alerts

    # ── 构建快照 ────────────────────────────────────
    def build_snapshot(self, neighbors, routes, children, alerts):
        """构建前端消费的 JSON 快照"""
        nodes = {}
        links = []

        # Coordinator 节点
        nodes[f"0x{self.coordinator_nwk:04X}"] = {
            "nwk": f"0x{self.coordinator_nwk:04X}",
            "ieee": self.coordinator_ieee,
            "type": "Coordinator",
            "status": "online",
        }

        # 从邻居表构建节点和连线
        for nb in neighbors:
            nwk_str = f"0x{nb['nwk']:04X}"
            # 推断节点类型
            if nb["relationship"] == "Child":
                node_type = "Router"  # Coordinator 的 Child 通常是 Router
            elif nb["relationship"] == "Parent":
                node_type = "Router"
            else:
                node_type = "Router"

            # 如果也在 children 中，检查是否是 SED
            for c in children:
                if c["nwk"] == nb["nwk"] and "Sleepy" in c["type"]:
                    node_type = "Sleepy_End_Device"
                    break

            nodes[nwk_str] = {
                "nwk": nwk_str,
                "ieee": nb["ieee"],
                "type": node_type,
                "depth": nb["depth"],
                "relationship": nb["relationship"],
                "lqi": nb["lqi"],
                "status": "online",
            }

            links.append({
                "source": f"0x{self.coordinator_nwk:04X}",
                "target": nwk_str,
                "lqi": nb["lqi"],
            })

        # SED 子节点（可能在邻居表中没有条目）
        for c in children:
            nwk_str = f"0x{c['nwk']:04X}"
            if nwk_str not in nodes:
                nodes[nwk_str] = {
                    "nwk": nwk_str,
                    "ieee": c["ieee"],
                    "type": c["type"],
                    "status": "online",
                }
                links.append({
                    "source": f"0x{self.coordinator_nwk:04X}",
                    "target": nwk_str,
                    "lqi": None,  # 未知
                })

        # 路由信息附加上到 links
        for r in routes:
            dest_str = f"0x{r['dest_nwk']:04X}"
            next_str = f"0x{r['next_hop']:04X}"
            # 检查是否已有这条 link
            existing = next(
                (l for l in links if l["source"] == next_str and l["target"] == dest_str),
                None,
            )
            if existing:
                existing["route_status"] = r["status"]
            else:
                links.append({
                    "source": next_str,
                    "target": dest_str,
                    "lqi": None,
                    "route_status": r["status"],
                })

        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "coordinator": {
                "ieee": self.coordinator_ieee,
                "nwk": f"0x{self.coordinator_nwk:04X}",
            },
            "nodes": list(nodes.values()),
            "links": links,
            "alerts": alerts,
        }

    # ── 主循环 ──────────────────────────────────────
    async def collect_once(self):
        neighbors = await self.read_neighbor_table()
        routes = await self.read_routing_table()
        children = await self.read_child_table()
        alerts = self.analyze(neighbors, routes, children)
        snapshot = self.build_snapshot(neighbors, routes, children, alerts)

        logger.info(
            f"采集完成: {len(snapshot['nodes'])} 节点, "
            f"{len(snapshot['links'])} 连线, "
            f"{len(alerts)} 告警"
        )
        return snapshot

    async def run(self, interval: int = 10):
        """主采集循环"""
        await self.connect()
        logger.info(f"开始采集，间隔 {interval}s")

        while True:
            try:
                snapshot = await self.collect_once()
                await self._notify(snapshot)
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                logger.info("采集已取消")
                break
            except Exception as e:
                logger.error(f"采集异常: {e}", exc_info=True)
                await asyncio.sleep(5)

    # ── 解码辅助 ────────────────────────────────────
    @staticmethod
    def _decode_relationship(rel):
        return {0: "Parent", 1: "Child", 2: "Sibling", 3: "None", 4: "Previous_Child"}.get(
            rel, f"Unknown({rel})"
        )

    @staticmethod
    def _decode_route_status(status):
        return {
            0: "Active", 1: "Discovery_Underway", 2: "Discovery_Failed", 3: "Inactive"
        }.get(status, f"Unknown({status})")

    @staticmethod
    def _decode_node_type(ntype):
        return {
            0: "Coordinator", 1: "Router", 2: "End_Device", 3: "Sleepy_End_Device"
        }.get(ntype, f"Unknown({ntype})")
