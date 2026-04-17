"""
MG21 EZSP 连接验证脚本 (Phase 2 正式版)

完整验证流程:
  1. 串口连通性
  2. EZSP 版本协商
  3. Coordinator 身份确认
  4. 网络参数读取
  5. 邻居表读取
  6. 路由表读取
  7. 子节点表读取
  8. EZSP 配置值读取
  9. 与 ZigSight 工具对接验证

用法: python3 test_mg21_verification.py <串口> [波特率]
输出: 验证报告 (JSON + 控制台)
"""

import asyncio
import sys
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mg21-verify")


class VerifyResult:
    def __init__(self):
        self.items = []
        self.start_time = datetime.now(timezone.utc).isoformat()
        self.serial_port = ""
        self.baudrate = 0

    def add(self, step: str, status: str, detail: str = "", data=None):
        self.items.append({
            "step": step,
            "status": status,  # pass / fail / warn / skip
            "detail": detail,
            "data": data,
        })
        icon = {"pass": "✅", "fail": "❌", "warn": "⚠️", "skip": "⏭️"}[status]
        msg = f"  {icon} {step}: {detail}"
        if status == "fail":
            logger.error(msg)
        elif status == "warn":
            logger.warning(msg)
        else:
            logger.info(msg)

    def summary(self):
        passed = sum(1 for i in self.items if i["status"] == "pass")
        failed = sum(1 for i in self.items if i["status"] == "fail")
        warned = sum(1 for i in self.items if i["status"] == "warn")
        skipped = sum(1 for i in self.items if i["status"] == "skip")
        total = len(self.items)
        return {"total": total, "pass": passed, "fail": failed, "warn": warned, "skip": skipped}

    def to_dict(self):
        return {
            "tool": "ZigSight MG21 Verification",
            "version": "2.0",
            "timestamp": self.start_time,
            "serial_port": self.serial_port,
            "baudrate": self.baudrate,
            "results": self.items,
            "summary": self.summary(),
            "overall": "FAIL" if self.summary()["fail"] > 0 else "PASS",
        }

    def save(self, path="mg21_verify_report.json"):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        logger.info(f"验证报告已保存: {path}")


result = VerifyResult()


async def run_verification(port: str, baudrate: int):
    result.serial_port = port
    result.baudrate = baudrate

    print("=" * 60)
    print("  ZigSight MG21 EZSP 连接验证 (Phase 2)")
    print(f"  串口: {port} @ {baudrate}")
    print(f"  时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 60)

    # ── Step 0: 环境检查 ──
    print("\n[Step 0] 环境检查")

    try:
        import bellows
        result.add("bellows 安装", "pass", f"版本 {bellows.__version__}")
    except ImportError:
        result.add("bellows 安装", "fail", "未安装, 运行: pip install bellows zigpy")
        result.save()
        return

    try:
        import serial.tools.list_ports
        ports = serial.tools.list_ports.comports()
        port_names = [p.device for p in ports]
        if port in port_names:
            result.add("串口存在", "pass", f"{port} 已检测到")
        else:
            result.add("串口存在", "fail", f"{port} 未找到, 可用: {port_names}")
            result.save()
            return
    except:
        result.add("串口检测", "warn", "pyserial 未安装, 跳过端口检测")

    # ── Step 1: 串口连接 ──
    print("\n[Step 1] 串口连接 + EZSP 版本协商")

    ezsp = None
    try:
        import bellows.ezsp
        ezsp = bellows.ezsp.EZSP()
        await ezsp.connect(port, baudrate)
        result.add("串口连接", "pass", f"{port} @ {baudrate} 连接成功")
    except PermissionError:
        result.add("串口连接", "fail", "权限不足, 运行: sudo chmod 666 {port} 或加入 dialout 组")
        result.save()
        return
    except Exception as e:
        # 尝试其他波特率
        for alt_baud in [57600, 230400, 460800]:
            try:
                ezsp = bellows.ezsp.EZSP()
                await ezsp.connect(port, alt_baud)
                result.add("串口连接", "warn", f"{baudrate} 失败, {alt_baud} 成功, 请更新配置")
                baudrate = alt_baud
                break
            except:
                continue
        else:
            result.add("串口连接", "fail", f"所有波特率均失败: {e}")
            result.save()
            return

    if ezsp is None:
        result.save()
        return

    # ── Step 2: Coordinator 身份 ──
    print("\n[Step 2] Coordinator 身份确认")

    coord_ieee = None
    try:
        (status, ieee) = await ezsp.getEui64()
        if status == 0:
            coord_ieee = str(ieee)
            result.add("IEEE 地址", "pass", f"{coord_ieee}")
        else:
            result.add("IEEE 地址", "fail", f"status=0x{status:02X}")
    except Exception as e:
        result.add("IEEE 地址", "fail", str(e))

    coord_nwk = None
    try:
        (status, nwk) = await ezsp.getNodeId()
        if status == 0:
            coord_nwk = nwk
            result.add("NWK 地址", "pass", f"0x{nwk:04X}")
        else:
            result.add("NWK 地址", "warn", f"status=0x{status:02X}, 假设 0x0000")
            coord_nwk = 0x0000
    except:
        result.add("NWK 地址", "warn", "读取失败, 假设 0x0000")
        coord_nwk = 0x0000

    # ── Step 3: 网络参数 ──
    print("\n[Step 3] 网络参数")

    network_formed = False
    try:
        (status, params) = await ezsp.getNetworkParameters()
        if status == 0:
            network_formed = True
            # 尝试提取各种可能字段
            info = {}
            for attr in ['nodeType', 'panId', 'channel', 'extendedPanId', 'nwkManagerId']:
                if hasattr(params, attr):
                    val = getattr(params, attr)
                    if isinstance(val, int):
                        info[attr] = f"0x{val:04X}" if attr in ['panId', 'nwkManagerId'] else str(val)
                    else:
                        info[attr] = str(val)

            # 如果 params 本身是 tuple，尝试解包
            if not info and isinstance(params, (tuple, list)):
                type_names = {0: "Coordinator", 1: "Router", 2: "End Device"}
                info["raw"] = str(params)
                if len(params) >= 1:
                    info["nodeType"] = type_names.get(params[0], f"Unknown({params[0]})")

            result.add("网络参数", "pass", json.dumps(info, ensure_ascii=False))
        else:
            result.add("网络参数", "fail", f"status=0x{status:02X}, 设备可能未组网")
    except Exception as e:
        result.add("网络参数", "warn", f"读取异常: {e}")

    if not network_formed:
        result.add("网络状态", "warn", "Coordinator 可能未组网, 后续数据可能为空")

    # ── Step 4: 邻居表 ──
    print("\n[Step 4] 邻居表")

    neighbor_count = 0
    try:
        idx = 0
        neighbors = []
        while True:
            (status, entries) = await ezsp.getNeighborTable(idx)
            if status != 0:
                break
            for entry in entries:
                neighbor_count += 1
                try:
                    nwk = getattr(entry, 'nwk', getattr(entry, 'shortId', None))
                    lqi = getattr(entry, 'lqi', getattr(entry, 'linkQuality', None))
                    depth = getattr(entry, 'depth', None)
                    ieee_val = getattr(entry, 'ieee', getattr(entry, 'longId', None))
                    rel = getattr(entry, 'relationship', None)

                    if neighbor_count <= 10:
                        nwk_str = f"0x{nwk:04X}" if isinstance(nwk, int) else str(nwk)
                        lqi_str = str(lqi) if isinstance(lqi, int) else "?"
                        print(f"    [{neighbor_count}] NWK={nwk_str} LQI={lqi_str}")
                except:
                    pass
                idx += 1
            if len(entries) < 8:
                break

        if neighbor_count > 0:
            result.add("邻居表读取", "pass", f"共 {neighbor_count} 条邻居")
        else:
            result.add("邻居表读取", "warn", "邻居表为空 (Coordinator 可能没有设备入网)")
    except AttributeError as e:
        result.add("邻居表读取", "fail", f"API 不兼容: {e}")
    except Exception as e:
        result.add("邻居表读取", "fail", str(e))

    # ── Step 5: 路由表 ──
    print("\n[Step 5] 路由表")

    route_count = 0
    failed_routes = 0
    try:
        idx = 0
        while True:
            (status, entries) = await ezsp.getRoutingTable(idx)
            if status != 0:
                break
            for entry in entries:
                route_count += 1
                try:
                    st = getattr(entry, 'status', 3)
                    if st == 2:  # Discovery_Failed
                        failed_routes += 1
                except:
                    pass
                idx += 1
            if len(entries) < 8:
                break

        status_msg = f"共 {route_count} 条路由"
        if failed_routes > 0:
            status_msg += f", {failed_routes} 条失败"
            result.add("路由表读取", "warn", status_msg)
        else:
            result.add("路由表读取", "pass", status_msg)
    except Exception as e:
        result.add("路由表读取", "fail", str(e))

    # ── Step 6: 子节点表 ──
    print("\n[Step 6] 子节点表")

    child_count = 0
    sed_count = 0
    try:
        idx = 0
        while True:
            (status, child) = await ezsp.getChildData(idx)
            if status != 0:
                break
            child_count += 1
            try:
                ntype = getattr(child, 'type', 2)
                if ntype == 3:  # Sleepy End Device
                    sed_count += 1
            except:
                pass
            idx += 1

        result.add("子节点表读取", "pass", f"共 {child_count} 个子节点 (SED: {sed_count})")
    except Exception as e:
        result.add("子节点表读取", "fail", str(e))

    # ── Step 7: EZSP 配置 ──
    print("\n[Step 7] EZSP 配置值")

    config_map = {
        0x0001: "ADDRESS_TABLE_SIZE",
        0x0002: "DEVICE_TABLE_SIZE",
        0x0003: "KEY_TABLE_SIZE",
        0x0019: "NEIGHBOR_TABLE_SIZE",
        0x001A: "APS_UNICAST_MESSAGE_COUNT",
        0x002D: "ROUTING_TABLE_SIZE",
        0x0033: "MAX_END_DEVICE_CHILDREN",
    }

    config_results = {}
    for vid, name in config_map.items():
        try:
            (status, val) = await ezsp.getValue(vid)
            if status == 0:
                config_results[name] = val
                print(f"    {name}: {val}")
        except:
            pass

    if config_results:
        result.add("EZSP 配置读取", "pass", f"读取到 {len(config_results)} 项配置", config_results)
    else:
        result.add("EZSP 配置读取", "warn", "无法读取配置值")

    # ── Step 8: 与 ZigSight 工具对接验证 ──
    print("\n[Step 8] ZigSight 工具对接验证")

    # 测试 collector.py 能否正常工作
    try:
        sys.path.insert(0, str(Path(__file__).parent / "backend"))
        from collector import ZigbeeTopologyCollector

        collector = ZigbeeTopologyCollector(port, baudrate)
        await collector.connect()

        neighbors = await collector.read_neighbor_table()
        routes = await collector.read_routing_table()
        children = await collector.read_child_table()
        alerts = collector.analyze(neighbors, routes, children)
        snapshot = collector.build_snapshot(neighbors, routes, children, alerts)

        result.add("ZigSight collector", "pass",
                    f"快照构建成功: {len(snapshot['nodes'])} 节点, {len(snapshot['links'])} 连线, {len(alerts)} 告警",
                    {"nodes": len(snapshot['nodes']), "links": len(snapshot['links']), "alerts": len(alerts)})

        # 验证快照格式
        required_keys = ["timestamp", "coordinator", "nodes", "links", "alerts"]
        missing = [k for k in required_keys if k not in snapshot]
        if missing:
            result.add("快照格式验证", "warn", f"缺少字段: {missing}")
        else:
            result.add("快照格式验证", "pass", "所有必需字段完整")

    except ImportError as e:
        result.add("ZigSight collector", "fail", f"模块导入失败: {e}")
    except Exception as e:
        result.add("ZigSight collector", "fail", str(e))

    # ── 清理 ──
    try:
        await ezsp.close()
    except:
        pass

    # ── 汇总 ──
    print("\n" + "=" * 60)
    s = result.summary()
    overall = result.to_dict()["overall"]
    icon = "✅" if overall == "PASS" else "❌"
    print(f"  {icon} 验证结果: {overall}")
    print(f"  通过: {s['pass']} | 失败: {s['fail']} | 警告: {s['warn']} | 跳过: {s['skip']}")
    print("=" * 60)

    result.save()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 test_mg21_verification.py <串口> [波特率]")
        print("示例: python3 test_mg21_verification.py /dev/ttyUSB0")
        print("      python3 test_mg21_verification.py COM3 115200")
        sys.exit(1)

    port = sys.argv[1]
    baudrate = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
    asyncio.run(run_verification(port, baudrate))
