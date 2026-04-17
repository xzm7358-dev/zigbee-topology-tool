"""
Router 上报数据接收器 - 解析自定义 ZCL Cluster 0xFC00 的 TLV 数据
配合固件 zigbee_topology_report.c 使用

数据流:
  Router 固件 → ZCL Report (0xFC00) → Coordinator EZSP → bellows → 本模块
"""

import logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class NeighborEntry:
    """邻居表条目"""
    nwk: int
    ieee: str
    lqi: int


@dataclass
class RouteEntry:
    """路由表条目"""
    dest_nwk: int
    next_hop: int
    status: int


@dataclass
class EdScanResult:
    """ED Scan 结果"""
    channel: int
    rssi: int


@dataclass
class RouterReport:
    """单个 Router 的完整上报数据"""
    source_nwk: int  # 上报来源的 NWK 地址
    neighbors: list[NeighborEntry] = field(default_factory=list)
    routes: list[RouteEntry] = field(default_factory=list)
    parent_nwk: Optional[int] = None
    parent_ieee: Optional[str] = None
    uptime_seconds: Optional[int] = None
    ed_scan: list[EdScanResult] = field(default_factory=list)


class TopologyReportParser:
    """
    解析 Router 上报的 TLV 数据
    
    TLV 格式:
      [Tag:1B][Length:1B][Value:nB]
    
    Tags:
      0x01 - 邻居条目: [nwk:2B][ieee:8B][lqi:1B]
      0x02 - 路由条目: [destNwk:2B][nextHop:2B][status:1B]
      0x03 - ED Scan:  [channel:1B][rssi:1B] * N
      0x04 - 父节点:   [parentNwk:2B][parentIeee:8B]
      0x05 - 运行时间: [seconds:4B]
    """

    def parse(self, data: bytes, source_nwk: int = None) -> RouterReport:
        """解析一帧 TLV 数据"""
        report = RouterReport(source_nwk=source_nwk)
        offset = 0

        while offset + 2 <= len(data):
            tag = data[offset]
            length = data[offset + 1]
            offset += 2

            if offset + length > len(data):
                logger.warning(f"TLV 数据截断: tag=0x{tag:02X} len={length} remaining={len(data)-offset}")
                break

            value = data[offset:offset + length]
            offset += length

            try:
                if tag == 0x01:
                    entry = self._parse_neighbor(value)
                    if entry:
                        report.neighbors.append(entry)

                elif tag == 0x02:
                    entry = self._parse_route(value)
                    if entry:
                        report.routes.append(entry)

                elif tag == 0x03:
                    entries = self._parse_ed_scan(value)
                    report.ed_scan.extend(entries)

                elif tag == 0x04:
                    report.parent_nwk, report.parent_ieee = self._parse_parent(value)

                elif tag == 0x05:
                    report.uptime_seconds = self._parse_uptime(value)

                else:
                    logger.debug(f"未知 TLV tag: 0x{tag:02X}")

            except Exception as e:
                logger.warning(f"TLV tag=0x{tag:02X} 解析异常: {e}")

        return report

    def _parse_neighbor(self, value: bytes) -> Optional[NeighborEntry]:
        """解析邻居条目: [nwk:2B][ieee:8B][lqi:1B]"""
        if len(value) < 11:
            return None

        nwk = value[0] | (value[1] << 8)
        ieee_bytes = value[2:10]
        ieee = ":".join(f"{b:02X}" for b in ieee_bytes)
        lqi = value[10]

        return NeighborEntry(nwk=nwk, ieee=ieee, lqi=lqi)

    def _parse_route(self, value: bytes) -> Optional[RouteEntry]:
        """解析路由条目: [destNwk:2B][nextHop:2B][status:1B]"""
        if len(value) < 5:
            return None

        dest = value[0] | (value[1] << 8)
        next_hop = value[2] | (value[3] << 8)
        status = value[4]

        return RouteEntry(dest_nwk=dest, next_hop=next_hop, status=status)

    def _parse_ed_scan(self, value: bytes) -> list[EdScanResult]:
        """解析 ED Scan: [channel:1B][rssi:1B] * N"""
        results = []
        for i in range(0, len(value) - 1, 2):
            channel = value[i]
            rssi = value[i + 1]
            if rssi > 127:  # signed → unsigned 转换
                rssi -= 256
            results.append(EdScanResult(channel=channel, rssi=rssi))
        return results

    def _parse_parent(self, value: bytes):
        """解析父节点: [parentNwk:2B][parentIeee:8B]"""
        if len(value) < 10:
            return None, None

        nwk = value[0] | (value[1] << 8)
        ieee = ":".join(f"{b:02X}" for b in value[2:10])
        return nwk, ieee

    def _parse_uptime(self, value: bytes) -> int:
        """解析运行时间: [seconds:4B]"""
        if len(value) < 4:
            return 0
        return value[0] | (value[1] << 8) | (value[2] << 16) | (value[3] << 24)


class RouterReportAggregator:
    """
    聚合多个 Router 的上报数据，合并到全网拓扑
    """

    def __init__(self):
        self.parser = TopologyReportParser()
        self.router_reports: dict[int, RouterReport] = {}  # nwk → latest report
        self._frame_buffers: dict[int, dict[int, bytes]] = {}  # nwk → {frameIdx: data}

    def process_raw_frame(self, source_nwk: int, payload: bytes):
        """
        处理从 EZSP 收到的原始 ZCL 帧
        
        帧格式 (固件端):
          [zcl_header:3B][frame_index:1B][total_frames:1B][tlv_data...]
        
        我们只关心 tlv_data 部分
        """
        if len(payload) < 5:
            return

        # 跳过 ZCL header (3 bytes) + frame index + total frames
        frame_index = payload[3]
        total_frames = payload[4]
        tlv_data = payload[5:]

        # 缓存分帧数据
        if source_nwk not in self._frame_buffers:
            self._frame_buffers[source_nwk] = {}

        self._frame_buffers[source_nwk][frame_index] = tlv_data

        # 检查是否所有帧都收到了
        if len(self._frame_buffers[source_nwk]) >= total_frames:
            # 合并所有帧的 TLV 数据
            combined = b""
            for idx in sorted(self._frame_buffers[source_nwk].keys()):
                combined += self._frame_buffers[source_nwk][idx]

            # 解析
            report = self.parser.parse(combined, source_nwk)
            self.router_reports[source_nwk] = report

            # 清理缓冲区
            del self._frame_buffers[source_nwk]

            logger.info(
                f"Router 0x{source_nwk:04X} 上报: "
                f"{len(report.neighbors)} 邻居, "
                f"{len(report.routes)} 路由, "
                f"parent=0x{report.parent_nwk:04X}" if report.parent_nwk else f"Router 0x{source_nwk:04X} 上报: {len(report.neighbors)} 邻居"
            )

    def merge_to_snapshot(self, coordinator_snapshot: dict) -> dict:
        """
        将 Router 上报数据合并到 Coordinator 快照中
        
        在 Coordinator 视角的基础上，补充 Router 视角的邻居关系
        这样拓扑图才能看到 Router-to-Router 的连线
        """
        nodes = {n["nwk"]: n for n in coordinator_snapshot.get("nodes", [])}
        links = list(coordinator_snapshot.get("links", []))
        alerts = list(coordinator_snapshot.get("alerts", []))

        # 已有连线索引，避免重复
        link_set = {(l["source"], l["target"]) for l in links}

        coord_nwk = coordinator_snapshot.get("coordinator", {}).get("nwk", "0x0000")

        for router_nwk, report in self.router_reports.items():
            router_nwk_str = f"0x{router_nwk:04X}"

            # 确保 Router 节点存在
            if router_nwk_str not in nodes:
                nodes[router_nwk_str] = {
                    "nwk": router_nwk_str,
                    "type": "Router",
                    "status": "online",
                    "source": "router_report",
                }

            # 添加邻居关系 (Router 视角)
            for nb in report.neighbors:
                nb_nwk_str = f"0x{nb.nwk:04X}"

                # 确保邻居节点存在
                if nb_nwk_str not in nodes:
                    # 推断类型
                    # Coordinator 的邻居通常是 Router
                    nb_type = "Router"
                    if nb_nwk_str == coord_nwk:
                        nb_type = "Coordinator"
                    nodes[nb_nwk_str] = {
                        "nwk": nb_nwk_str,
                        "ieee": nb.ieee,
                        "type": nb_type,
                        "status": "online",
                        "source": "router_report",
                    }
                else:
                    # 补充 IEEE 如果之前没有
                    if not nodes[nb_nwk_str].get("ieee") and nb.ieee:
                        nodes[nb_nwk_str]["ieee"] = nb.ieee

                # 添加连线 (双向都可能，但只存一次)
                link_key = (router_nwk_str, nb_nwk_str)
                link_key_rev = (nb_nwk_str, router_nwk_str)

                if link_key not in link_set and link_key_rev not in link_set:
                    links.append({
                        "source": router_nwk_str,
                        "target": nb_nwk_str,
                        "lqi": nb.lqi,
                        "source_type": "router_report",
                    })
                    link_set.add(link_key)

                # 弱链路告警
                if nb.lqi < 50:
                    alerts.append({
                        "type": "weak_link",
                        "severity": "critical",
                        "message": f"Router 0x{router_nwk:04X} → 0x{nb.nwk:04X} LQI={nb.lqi} (极弱)",
                        "node": nb_nwk_str,
                        "source": f"router:0x{router_nwk:04X}",
                    })

            # 添加路由关系
            for route in report.routes:
                dest_str = f"0x{route.dest_nwk:04X}"
                next_str = f"0x{route.next_hop:04X}"

                route_status_map = {
                    0: "Active", 1: "Discovery_Underway",
                    2: "Discovery_Failed", 3: "Inactive"
                }

                link_key = (next_str, dest_str)
                if link_key in link_set:
                    # 更新现有连线的路由状态
                    for l in links:
                        if l["source"] == next_str and l["target"] == dest_str:
                            l["route_status"] = route_status_map.get(route.status, f"Unknown({route.status})")
                            break
                else:
                    links.append({
                        "source": next_str,
                        "target": dest_str,
                        "lqi": None,
                        "route_status": route_status_map.get(route.status, f"Unknown({route.status})"),
                        "source_type": "router_report",
                    })
                    link_set.add(link_key)

            # ED Scan 结果
            if report.ed_scan:
                # 存储到节点信息中，前端可以渲染信道热力图
                nodes[router_nwk_str]["ed_scan"] = [
                    {"channel": e.channel, "rssi": e.rssi}
                    for e in report.ed_scan
                ]

        # 更新快照
        result = dict(coordinator_snapshot)
        result["nodes"] = list(nodes.values())
        result["links"] = links
        result["alerts"] = alerts
        result["router_reports"] = len(self.router_reports)

        return result
