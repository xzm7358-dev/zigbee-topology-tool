"""
MG21 EZSP 连接测试 (增强版)
兼容不同 EmberZNet / EZSP 版本

用法: python3 test_ezsp_connect_v2.py /dev/ttyUSB0
"""

import asyncio
import sys
import logging

logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s %(message)s")
logger = logging.getLogger("ezsp-test")


async def test(port: str, baudrate: int = 115200):
    print("═" * 55)
    print("  MG21 EZSP 连接测试 v2")
    print(f"  串口: {port} @ {baudrate}")
    print("═" * 55)

    import bellows.ezsp
    import bellows.types as t

    # ── 1. 连接 + 版本协商 ──
    print("\n[1] 连接 Coordinator...")
    ezsp = bellows.ezsp.EZSP()

    try:
        await ezsp.connect(port, baudrate)
        print("    ✅ 串口连接成功，EZSP 版本协商完成")
    except Exception as e:
        print(f"    ❌ 连接失败: {e}")
        print("\n    排查:")
        print("    - 串口是否被占用? (停止 Z2M / HA / 串口助手)")
        print("    - 权限? (sudo chmod 666 /dev/ttyUSB0 或加 dialout 组)")
        print("    - 波特率? (MG21 默认 115200, 有些固件用 57600)")
        return

    # 打印 EZSP 协议信息
    print(f"    EZSP 配置版本: {ezsp.ezsp_version if hasattr(ezsp, 'ezsp_version') else '未知'}")

    # ── 2. 基础信息 ──
    print("\n[2] 读取基础信息...")

    async def safe_call(desc, func, *args):
        try:
            result = await func(*args)
            print(f"    ✅ {desc}: {result}")
            return result
        except Exception as e:
            print(f"    ⚠️  {desc} 失败: {e}")
            return None

    await safe_call("IEEE 地址", ezsp.getEui64)
    await safe_call("Node ID", ezsp.getNodeId)

    # ── 3. 网络状态 ──
    print("\n[3] 读取网络状态...")

    # 尝试多种 API 获取网络信息
    net_params = await safe_call("网络参数 (getNetworkParameters)", ezsp.getNetworkParameters)

    if net_params is None:
        # 有些版本用不同的 API
        await safe_call("网络状态 (networkState)", ezsp.networkState)

    # ── 4. 读取邻居表 ──
    print("\n[4] 读取邻居表...")
    total_neighbors = 0

    try:
        idx = 0
        while True:
            (status, entries) = await ezsp.getNeighborTable(idx)
            if status != 0:
                if idx == 0:
                    status_names = {
                        0x00: "SUCCESS",
                        0x84: "TABLE_EMPTY / NOT_FOUND",
                        0x85: "INVALID_PARAMETER",
                    }
                    print(f"    ⚠️  邻居表 status=0x{status:02X} ({status_names.get(status, 'UNKNOWN')})")
                break

            for entry in entries:
                total_neighbors += 1
                try:
                    # 不同版本的 bellows 字段名可能不同
                    # 尝试多种属性访问方式
                    nwk = getattr(entry, 'nwk', getattr(entry, 'shortId', '?'))
                    lqi = getattr(entry, 'lqi', getattr(entry, 'linkQuality', '?'))
                    depth = getattr(entry, 'depth', '?')
                    ieee = getattr(entry, 'ieee', getattr(entry, 'longId', '?'))

                    if total_neighbors <= 8:
                        nwk_str = f"0x{nwk:04X}" if isinstance(nwk, int) else str(nwk)
                        lqi_str = str(lqi) if isinstance(lqi, int) else str(lqi)
                        print(f"    [{total_neighbors}] NWK={nwk_str} LQI={lqi_str} Depth={depth}")
                except Exception as e:
                    print(f"    [{total_neighbors}] 解析异常: {e}, raw={entry}")

            idx += len(entries)
            if len(entries) < 8:  # 单次最多8条
                break

        print(f"    ✅ 邻居表共 {total_neighbors} 条")

    except AttributeError as e:
        print(f"    ❌ 邻居表 API 不兼容: {e}")
        print("    → bellows 版本与 EmberZNet 版本可能不匹配")
        print("    → 尝试: pip install --upgrade bellows zigpy")

    except Exception as e:
        print(f"    ❌ 邻居表读取异常: {e}")

    # ── 5. 读取路由表 ──
    print("\n[5] 读取路由表...")

    try:
        idx = 0
        total_routes = 0
        while True:
            (status, entries) = await ezsp.getRoutingTable(idx)
            if status != 0:
                break
            total_routes += len(entries)
            idx += len(entries)
            if len(entries) < 8:
                break
        print(f"    ✅ 路由表共 {total_routes} 条")
    except Exception as e:
        print(f"    ❌ 路由表读取异常: {e}")

    # ── 6. 读取子节点表 ──
    print("\n[6] 读取子节点表...")

    try:
        idx = 0
        total_children = 0
        while True:
            (status, child) = await ezsp.getChildData(idx)
            if status != 0:
                break
            total_children += 1
            idx += 1
        print(f"    ✅ 子节点共 {total_children} 个")
    except Exception as e:
        print(f"    ❌ 子节点表读取异常: {e}")

    # ── 7. 读取 EZSP 配置值 ──
    print("\n[7] 读取关键配置...")

    config_values = {
        0x0001: "CONFIG_ADDRESS_TABLE_SIZE",
        0x0002: "CONFIG_DEVICE_TABLE_SIZE",
        0x0003: "CONFIG_KEY_TABLE_SIZE",
        0x0019: "CONFIG_NEIGHBOR_TABLE_SIZE",
        0x001A: "CONFIG_APS_UNICAST_MESSAGE_COUNT",
        0x002D: "CONFIG_ROUTING_TABLE_SIZE",
        0x0031: "CONFIG_END_DEVICE_POLL_TIMEOUT",
        0x0033: "CONFIG_MAX_END_DEVICE_CHILDREN",
    }

    for vid, name in config_values.items():
        try:
            (status, val) = await ezsp.getValue(vid)
            if status == 0:
                print(f"    {name}: {val}")
        except:
            pass

    # ── 汇总 ──
    print(f"\n{'═'*55}")
    print(f"  结果: 邻居={total_neighbors} 路由=见上方 子节点=见上方")
    print(f"  如果 ✅ 为主，连接正常，可运行:")
    print(f"  python3 main.py --port {port}")
    print(f"  如果 ⚠️/❌ 较多，需要调整 bellows 版本或 EZSP 配置")
    print(f"{'═'*55}")

    await ezsp.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 test_ezsp_connect_v2.py <串口> [波特率]")
        print("示例: python3 test_ezsp_connect_v2.py /dev/ttyUSB0")
        sys.exit(1)

    port = sys.argv[1]
    baudrate = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
    asyncio.run(test(port, baudrate))
