"""模拟数据源 - 无需真实硬件即可测试前端"""

import asyncio
import json
import random
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class MockCollector:
    """生成模拟拓扑数据，用于开发调试前端"""

    def __init__(self, num_routers=8, num_seds=12):
        self.num_routers = num_routers
        self.num_seds = num_seds
        self._callbacks = []
        self.tick = 0

    def on_update(self, callback):
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

    def generate_snapshot(self):
        self.tick += 1
        nodes = []
        links = []
        alerts = []

        # Coordinator
        nodes.append({
            "nwk": "0x0000",
            "ieee": "00:12:4b:00:aa:00:00:00",
            "type": "Coordinator",
            "status": "online",
        })

        # Routers
        router_nwks = []
        for i in range(1, self.num_routers + 1):
            nwk = f"0x{i:04X}"
            router_nwks.append(nwk)
            lqi = random.randint(150, 255)
            # 偶尔模拟弱链路
            if random.random() < 0.1:
                lqi = random.randint(30, 80)
                alerts.append({
                    "type": "weak_link",
                    "severity": "warning" if lqi > 50 else "critical",
                    "message": f"{nwk} LQI={lqi} ({'偏弱' if lqi > 50 else '极弱'})",
                    "node": nwk,
                })

            nodes.append({
                "nwk": nwk,
                "ieee": f"00:12:4b:00:bb:{i:02X}:00:00",
                "type": "Router",
                "lqi": lqi,
                "depth": 1,
                "status": "online",
            })

            links.append({
                "source": "0x0000",
                "target": nwk,
                "lqi": lqi,
            })

        # Router-to-Router links (mesh)
        for i, nwk in enumerate(router_nwks):
            if i + 1 < len(router_nwks):
                peer_lqi = random.randint(100, 240)
                links.append({
                    "source": nwk,
                    "target": router_nwks[i + 1],
                    "lqi": peer_lqi,
                })

        # SEDs
        sed_nwks = []
        for i in range(self.num_seds):
            nwk = f"0x{self.num_routers + 1 + i:04X}"
            sed_nwks.append(nwk)
            parent = random.choice(router_nwks)
            lqi = random.randint(80, 220)

            # 偶尔模拟 SED 孤儿
            if random.random() < 0.05:
                alerts.append({
                    "type": "orphan_sed",
                    "severity": "warning",
                    "message": f"SED {nwk} 可能是孤儿节点",
                    "node": nwk,
                })

            nodes.append({
                "nwk": nwk,
                "ieee": f"00:12:4b:00:cc:{i:02X}:00:00",
                "type": "Sleepy_End_Device",
                "lqi": lqi,
                "depth": 2,
                "status": "online",
            })

            links.append({
                "source": parent,
                "target": nwk,
                "lqi": lqi,
            })

        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "coordinator": {"ieee": "00:12:4b:00:aa:00:00:00", "nwk": "0x0000"},
            "nodes": nodes,
            "links": links,
            "alerts": alerts,
        }

    async def run(self, interval=10):
        logger.info(f"模拟模式启动，{self.num_routers} Router + {self.num_seds} SED")
        while True:
            try:
                snapshot = self.generate_snapshot()
                await self._notify(snapshot)
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"模拟异常: {e}")
                await asyncio.sleep(2)
