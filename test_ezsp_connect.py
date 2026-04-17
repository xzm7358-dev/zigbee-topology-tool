"""
MG21 EZSP 连接测试脚本
验证 bellows 能否正常与 MG21 Coordinator 通信

用法: python3 test_ezsp_connect.py /dev/ttyUSB0
"""

import asyncio
import sys
import struct


async def test_connection(port: str, baudrate: int = 115200):
    print(f"═══════════════════════════════════════════")
    print(f"  MG21 EZSP 连接测试")
    print(f"  串口: {port} @ {baudrate}")
    print(f"═══════════════════════════════════════════\n")

    # ── Step 1: 加载 bellows ──
    print("[1/6] 加载 bellows 库...")
    try:
        import bellows.ezsp
        import bellows.types as t
        print("      ✅ bellows 已加载")
    except ImportError as e:
        print(f"      ❌ bellows 未安装: {e}")
        print("      运行: pip install bellows zigpy")
        return False

    # ── Step 2: 串口连接 ──
    print(f"\n[2/6] 连接串口 {port}...")
    ezsp = bellows.ezsp.EZSP()
    try:
        await ezsp.connect(port, baudrate)
        print("      ✅ 串口连接成功")
    except Exception as e:
        print(f"      ❌ 串口连接失败: {e}")
        print("      检查:")
        print("        - 串口设备是否存在 (ls /dev/ttyUSB*)")
        print("        - 是否有权限 (sudo chmod 666 /dev/ttyUSB0)")
        print("        - 是否被其他程序占用 (Z2M/HA)")
        return False

    # ── Step 3: EZSP 版本协商 ──
    print("\n[3/6] EZSP 版本协商...")
    try:
        # bellows connect 内部会自动做版本协商
        # 我们读取协商后的版本信息
        (status, version) = await ezsp.getValue(0x0000)  # EZSP_VALUE_VERSION
        if status == 0:
            major = (version >> 8) & 0xFF
            minor = version & 0xFF
            print(f"      ✅ EZSP 版本: v{major}.{minor}")
        else:
            print(f"      ⚠️  无法读取版本 (status={status}), 继续测试...")
    except Exception as e:
        print(f"      ⚠️  版本查询异常: {e}, 继续测试...")

    # ── Step 4: 读取 Coordinator 信息 ──
    print("\n[4/6] 读取 Coordinator 信息...")
    try:
        (status, ieee) = await ezsp.getEui64()
        if status == 0:
            print(f"      ✅ IEEE 地址: {ieee}")
        else:
            print(f"      ⚠️  getEui64 返回 status={status}")
    except Exception as e:
        print(f"      ❌ 读取 IEEE 失败: {e}")

    try:
        (status, nwk) = await ezsp.getNetworkParameters()
        if status == 0:
            print(f"      ✅ NWK 地址: 0x{nwk:04X}")
        else:
            print(f"      ⚠️  getNetworkParameters 返回 status={status}")
    except Exception as e:
        # 有些版本 API 不同，试试另一个
        try:
            result = await ezsp.getNodeId()
            print(f"      ✅ Node ID: {result}")
        except:
            print(f"      ⚠️  读取 NWK 地址失败: {e}")

    # ── Step 5: 读取网络状态 ──
    print("\n[5/6] 读取网络状态...")
    try:
        # 尝试读取网络参数
        # EZSP_CMD_getNetworkParameters = 0x0028
        (status, params) = await ezsp.getNetworkParameters()
        if hasattr(params, 'nodeType'):
            node_type = params.nodeType
            type_names = {0: "Coordinator", 1: "Router", 2: "End Device"}
            print(f"      ✅ 节点类型: {type_names.get(node_type, f'Unknown({node_type})')}")
        if hasattr(params, 'panId'):
            print(f"      ✅ PAN ID: 0x{params.panId:04X}")
        if hasattr(params, 'channel'):
            print(f"      ✅ 信道: {params.channel}")
    except Exception as e:
        print(f"      ⚠️  网络参数读取异常: {e}")
        # 备用方案：直接读网络参数
        try:
            result = await ezsp.networkState()
            state_names = {0: "Offline", 1: "Joining", 2: "Joined", 3: "Joined_NoParent"}
            print(f"      网络状态: {state_names.get(result, result)}")
        except:
            print("      ⚠️  备用方案也失败，跳过")

    # ── Step 6: 读取邻居表 ──
    print("\n[6/6] 读取邻居表 (测试数据采集)...")
    try:
        (status, entries) = await ezsp.getNeighborTable(0)
        if status == 0:
            print(f"      ✅ 邻居表可读，首批 {len(entries)} 条")
            for i, entry in enumerate(entries[:5]):
                try:
                    nwk = entry.nwk
                    lqi = entry.lqi
                    depth = entry.depth
                    print(f"         [{i}] NWK=0x{nwk:04X} LQI={lqi} Depth={depth}")
                except:
                    print(f"         [{i}] {entry}")
            if len(entries) > 5:
                print(f"         ... 共 {len(entries)} 条")
        else:
            print(f"      ⚠️  邻居表返回 status={status}")
            if status == 0x84:
                print("         可能设备未组网或邻居表为空")
    except Exception as e:
        print(f"      ❌ 邻居表读取失败: {e}")

    # ── 读取路由表 ──
    print("\n   附带测试: 路由表...")
    try:
        (status, entries) = await ezsp.getRoutingTable(0)
        if status == 0:
            print(f"      ✅ 路由表可读，首批 {len(entries)} 条")
        else:
            print(f"      ⚠️  路由表 status={status}")
    except Exception as e:
        print(f"      ❌ 路由表读取失败: {e}")

    # ── 读取子节点表 ──
    print("   附带测试: 子节点表...")
    try:
        (status, child) = await ezsp.getChildData(0)
        if status == 0:
            print(f"      ✅ 子节点表可读")
        else:
            print(f"      ⚠️  子节点表 status={status}")
            if status == 0x84:
                print("         无子节点（Coordinator 可能还没有设备入网）")
    except Exception as e:
        print(f"      ❌ 子节点表读取失败: {e}")

    # ── 汇总 ──
    print(f"\n{'='*50}")
    print("  测试完成！如果以上步骤都 ✅，说明连接正常")
    print("  可以运行完整工具:")
    print(f"  python3 main.py --port {port}")
    print(f"{'='*50}")

    await ezsp.close()
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 test_ezsp_connect.py <串口> [波特率]")
        print("示例: python3 test_ezsp_connect.py /dev/ttyUSB0")
        print("      python3 test_ezsp_connect.py COM3 115200")
        sys.exit(1)

    port = sys.argv[1]
    baudrate = int(sys.argv[2]) if len(sys.argv) > 2 else 115200

    asyncio.run(test_connection(port, baudrate))
