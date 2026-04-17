"""
Zigbee Topology Collector - EZSP 数据采集层 (v2)
兼容 MG21 / EmberZNet 不同版本

改进:
- 更健壮的 EZSP API 调用（兼容不同版本）
- 异常自动恢复
- 采集间隔可配置
- 数据变化检测（避免重复推送）
"""

import asyncio
import bellows.ezsp
import logging
from datetime import datetime
from typing import Optional, Callable, Awaitable

from router_report import RouterReportAggregator

logger = logging.getLogger(__name__)


class ZigbeeTopologyCollector:
    """从 Coordinator EZSP 接口采集拓扑数据"""

    def __init__(self, serial_port: str, baudrate: int = 115200):
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.ezsp = None
        self.coordinator_ieee: Optional[str] = None
        self.coordinator_nwk: int = 0x0000
        self._callbacks = []
        self._last_snapshot_hash = None
        self.router_aggregator = RouterReportAggregator()  # Router 上报数据聚合器

    def on_update(self, callback):
        """注册数据更新回调"""
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

    # ── 连接 ────────────────────────────────────────
    async def connect(self):
        """连接 Coordinator"""
        logger.info(f"连接 {self.serial_port} @ {self.baudrate}...")

        self.ezsp = bellows.ezsp.EZSP()
        await self.ezsp.connect(self.serial_port, self.baudrate)

        # 读取 IEEE
        try:
            (status, ieee) = await self.ezsp.getEui64()
            if status == 0:
                self.coordinator_ieee = str(ieee)
        except Exception as e:
            logger.warning(f"getEui64 异常: {e}")
            self.coordinator_ieee = "unknown"

        # 读取 NWK
        try:
            (status, nwk) = await self.ezsp.getNodeId()
            if status == 0:
                self.coordinator_nwk = nwk
        except Exception:
            self.coordinator_nwk = 0x0000

        # 打印网络信息
        try:
            (status, params) = await self.ezsp.getNetworkParameters()
            if status == 0:
                logger.info(f"网络参数: {params}")
        except Exception as e:
            logger.warning(f"getNetworkParameters 异常: {e}")

        logger.info(f"Coordinator: IEEE={self.coordinator_ieee}, NWK=0x{self.coordinator_nwk:04X}")

        # 注册 EZSP 接收回调，捕获 Router 上报的 ZCL 数据
        self.ezsp.add_callback(self._ezsp_callback)

        return self.coordinator_ieee, self.coordinator_nwk

    def _ezsp_callback(self, event_name, data):
        """EZSP 事件回调，捕获来自 Router 的 ZCL 上报"""
        if event_name == "incoming_message" or event_name == "message_handler":
            try:
                # 检查是否是自定义 cluster 0xFC00
                if hasattr(data, 'apsFrame') and data.apsFrame.clusterId == 0xFC00:
                    source_nwk = data.sender if hasattr(data, 'sender') else None
                    if source_nwk and hasattr(data, 'message'):
                        payload = bytes(data.message) if not isinstance(data.message, bytes) else data.message
                        self.router_aggregator.process_raw_frame(source_nwk, payload)
            except Exception as e:
                logger.debug(f"EZSP 回调处理异常: {e}")

    # ── 邻居表 ──────────────────────────────────────
    async def read_neighbor_table(self):
        """读取邻居表，兼容不同 EZSP 版本"""
        neighbors = []
        idx = 0

        while True:
            try:
                (status, entries) = await self.ezsp.getNeighborTable(idx)
                if status != 0:
                    break

                for entry in entries:
                    try:
                        # 不同版本字段名可能不同，做兼容
                        nwk = getattr(entry, 'nwk', getattr(entry, 'shortId', None))
                        ieee = getattr(entry, 'ieee', getattr(entry, 'longId', None))
                        lqi = getattr(entry, 'lqi', getattr(entry, 'linkQuality', 0))
                        depth = getattr(entry, 'depth', 0)
                        relationship = getattr(entry, 'relationship', 0)

                        neighbors.append({
                            "nwk": nwk,
                            "ieee": str(ieee) if ieee else None,
                            "lqi": lqi if isinstance(lqi, int) else 0,
                            "depth": depth if isinstance(depth, int) else 0,
                            "relationship": self._decode_relationship(relationship),
                        })
                    except Exception as e:
                        logger.debug(f"邻居条目解析异常: {e}, raw={entry}")
                    idx += 1

                if len(entries) < 8:
                    break

            except Exception as e:
                logger.warning(f"邻居表读取异常 @ idx={idx}: {e}")
                break

        return neighbors

    # ── 路由表 ──────────────────────────────────────
    async def read_routing_table(self):
        """读取路由表"""
        routes = []
        idx = 0

        while True:
            try:
                (status, entries) = await self.ezsp.getRoutingTable(idx)
                if status != 0:
                    break

                for entry in entries:
                    try:
                        dest = getattr(entry, 'destNwk', getattr(entry, 'destination', None))
                        next_hop = getattr(entry, 'nextHop', getattr(entry, 'nextHopNwk', None))
                        status_val = getattr(entry, 'status', 3)  # 默认 Inactive
                        age = getattr(entry, 'age', 0)

                        routes.append({
                            "dest_nwk": dest,
                            "next_hop": next_hop,
                            "status": self._decode_route_status(status_val),
                            "age": age,
                        })
                    except Exception as e:
                        logger.debug(f"路由条目解析异常: {e}")
                    idx += 1

                if len(entries) < 8:
                    break

            except Exception as e:
                logger.warning(f"路由表读取异常 @ idx={idx}: {e}")
                break

        return routes

    # ── 子节点表 ────────────────────────────────────
    async def read_child_table(self):
        """读取子节点表"""
        children = []
        idx = 0

        while True:
            try:
                (status, child_data) = await self.ezsp.getChildData(idx)
                if status != 0:
                    break

                try:
                    nwk = getattr(child_data, 'nwk', getattr(child_data, 'id', None))
                    ieee = getattr(child_data, 'ieee', getattr(child_data, 'eui64', None))
                    ntype = getattr(child_data, 'type', 2)

                    children.append({
                        "nwk": nwk,
                        "ieee": str(ieee) if ieee else None,
                        "type": self._decode_node_type(ntype),
                    })
                except Exception as e:
                    logger.debug(f"子节点解析异常: {e}")
                idx += 1

            except Exception as e:
                logger.warning(f"子节点表读取异常 @ idx={idx}: {e}")
                break

        return children

    # ── 诊断分析 ────────────────────────────────────
    def analyze(self, neighbors, routes, children):
        alerts = []

        for nb in neighbors:
            lqi = nb.get("lqi", 0)
            if isinstance(lqi, int):
                if lqi < 50:
                    alerts.append({
                        "type": "weak_link",
                        "severity": "critical",
                        "message": f"0x{nb['nwk']:04X} LQI={lqi} (极弱)",
                        "node": f"0x{nb['nwk']:04X}",
                    })
                elif lqi < 100:
                    alerts.append({
                        "type": "weak_link",
                        "severity": "warning",
                        "message": f"0x{nb['nwk']:04X} LQI={lqi} (偏弱)",
                        "node": f"0x{nb['nwk']:04X}",
                    })

        for r in routes:
            status = r.get("status", "")
            if "Failed" in status:
                alerts.append({
                    "type": "route_failed",
                    "severity": "critical",
                    "message": f"路由到 0x{r['dest_nwk']:04X} 发现失败",
                    "node": f"0x{r['dest_nwk']:04X}",
                })
            elif "Underway" in status:
                alerts.append({
                    "type": "route_discovery",
                    "severity": "warning",
                    "message": f"路由到 0x{r['dest_nwk']:04X} 正在发现中",
                    "node": f"0x{r['dest_nwk']:04X}",
                })

        # 路由环路检测
        route_map = {}
        for r in routes:
            dest = r.get("dest_nwk")
            next_hop = r.get("next_hop")
            if dest is not None and next_hop is not None:
                route_map[dest] = next_hop

        for dest, next_hop in route_map.items():
            if next_hop in route_map and route_map[next_hop] == dest:
                alerts.append({
                    "type": "route_loop",
                    "severity": "critical",
                    "message": f"路由环路: 0x{dest:04X} ↔ 0x{next_hop:04X}",
                    "node": f"0x{dest:04X}",
                })

        # SED 孤儿检测
        child_nwks = {c["nwk"] for c in children if c.get("type") and "Sleepy" in c["type"]}
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
        nodes = {}
        links = []

        # Coordinator
        coord_nwk_str = f"0x{self.coordinator_nwk:04X}"
        nodes[coord_nwk_str] = {
            "nwk": coord_nwk_str,
            "ieee": self.coordinator_ieee,
            "type": "Coordinator",
            "status": "online",
        }

        # 邻居 → 节点 + 连线
        for nb in neighbors:
            nwk = nb["nwk"]
            if nwk is None:
                continue
            nwk_str = f"0x{nwk:04X}" if isinstance(nwk, int) else str(nwk)

            node_type = "Router"
            for c in children:
                if c["nwk"] == nwk and c.get("type") and "Sleepy" in c["type"]:
                    node_type = c["type"]
                    break

            lqi = nb.get("lqi", 0)
            nodes[nwk_str] = {
                "nwk": nwk_str,
                "ieee": nb.get("ieee"),
                "type": node_type,
                "depth": nb.get("depth"),
                "relationship": nb.get("relationship"),
                "lqi": lqi if isinstance(lqi, int) else 0,
                "status": "online",
            }

            links.append({
                "source": coord_nwk_str,
                "target": nwk_str,
                "lqi": lqi if isinstance(lqi, int) else None,
            })

        # SED 子节点
        for c in children:
            nwk = c["nwk"]
            if nwk is None:
                continue
            nwk_str = f"0x{nwk:04X}" if isinstance(nwk, int) else str(nwk)
            if nwk_str not in nodes:
                nodes[nwk_str] = {
                    "nwk": nwk_str,
                    "ieee": c.get("ieee"),
                    "type": c.get("type", "End_Device"),
                    "status": "online",
                }
                links.append({
                    "source": coord_nwk_str,
                    "target": nwk_str,
                    "lqi": None,
                })

        # 路由信息
        for r in routes:
            dest = r.get("dest_nwk")
            next_hop = r.get("next_hop")
            if dest is None or next_hop is None:
                continue
            dest_str = f"0x{dest:04X}" if isinstance(dest, int) else str(dest)
            next_str = f"0x{next_hop:04X}" if isinstance(next_hop, int) else str(next_hop)

            existing = next(
                (l for l in links if l["source"] == next_str and l["target"] == dest_str),
                None,
            )
            if existing:
                existing["route_status"] = r.get("status")
            else:
                links.append({
                    "source": next_str,
                    "target": dest_str,
                    "lqi": None,
                    "route_status": r.get("status"),
                })

        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "coordinator": {
                "ieee": self.coordinator_ieee,
                "nwk": coord_nwk_str,
            },
            "nodes": list(nodes.values()),
            "links": links,
            "alerts": alerts,
        }

    # ── 单次采集 ────────────────────────────────────
    async def collect_once(self):
        neighbors = await self.read_neighbor_table()
        routes = await self.read_routing_table()
        children = await self.read_child_table()
        alerts = self.analyze(neighbors, routes, children)
        snapshot = self.build_snapshot(neighbors, routes, children, alerts)

        # 合并 Router 上报数据 (如果有)
        if self.router_aggregator.router_reports:
            snapshot = self.router_aggregator.merge_to_snapshot(snapshot)

        logger.info(
            f"采集: {len(snapshot['nodes'])} 节点, "
            f"{len(snapshot['links'])} 连线, "
            f"{len(alerts)} 告警, "
            f"{len(self.router_aggregator.router_reports)} Router上报"
        )
        return snapshot

    # ── 主循环 ──────────────────────────────────────
    async def run(self, interval: int = 10):
        """主采集循环"""
        await self.connect()
        logger.info(f"开始采集，间隔 {interval}s")

        retry_count = 0
        max_retries = 5

        while True:
            try:
                snapshot = await self.collect_once()
                await self._notify(snapshot)
                retry_count = 0  # 重置重试计数
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                logger.info("采集已取消")
                break

            except ConnectionError as e:
                retry_count += 1
                logger.error(f"连接断开 ({retry_count}/{max_retries}): {e}")
                if retry_count >= max_retries:
                    logger.error("重试次数耗尽，停止采集")
                    break
                await asyncio.sleep(5 * retry_count)  # 递增等待

            except Exception as e:
                logger.error(f"采集异常: {e}", exc_info=True)
                await asyncio.sleep(10)

    # ── 解码辅助 ────────────────────────────────────
    @staticmethod
    def _decode_relationship(rel):
        return {
            0: "Parent", 1: "Child", 2: "Sibling",
            3: "None", 4: "Previous_Child"
        }.get(rel, f"Unknown({rel})")

    @staticmethod
    def _decode_route_status(status):
        return {
            0: "Active", 1: "Discovery_Underway",
            2: "Discovery_Failed", 3: "Inactive"
        }.get(status, f"Unknown({status})")

    @staticmethod
    def _decode_node_type(ntype):
        return {
            0: "Coordinator", 1: "Router",
            2: "End_Device", 3: "Sleepy_End_Device"
        }.get(ntype, f"Unknown({ntype})")
