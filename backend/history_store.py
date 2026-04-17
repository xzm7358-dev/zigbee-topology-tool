"""
历史数据存储 - 时序回放引擎

存储拓扑快照 + ED Scan 数据到 SQLite，支持时间范围查询和回放

数据保留策略:
  - 原始数据: 保留7天 (10s间隔 = ~60万条/天)
  - 1分钟聚合: 保留30天
  - 10分钟聚合: 保留90天
  - 1小时聚合: 永久保留
"""

import json
import sqlite3
import logging
import time
from datetime import datetime, timedelta
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = "topology_history.db"


class HistoryStore:
    """SQLite 时序存储"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # 拓扑快照表
        c.execute("""
            CREATE TABLE IF NOT EXISTS topology_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                node_count INTEGER,
                link_count INTEGER,
                alert_count INTEGER,
                router_report_count INTEGER DEFAULT 0
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_topo_ts ON topology_snapshots(timestamp)")

        # ED Scan 快照表
        c.execute("""
            CREATE TABLE IF NOT EXISTS ed_scan_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                heatmap_json TEXT NOT NULL,
                node_count INTEGER
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_edscan_ts ON ed_scan_snapshots(timestamp)")

        # 事件日志表 (重要事件: 告警变化、设备上下线等)
        c.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                node_nwk TEXT,
                extra_json TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_event_ts ON event_log(timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_event_type ON event_log(event_type)")

        conn.commit()
        conn.close()

    # ── 写入 ──────────────────────────────────────

    def save_topology_snapshot(self, snapshot: dict):
        """保存拓扑快照"""
        ts = snapshot.get("timestamp", datetime.utcnow().isoformat() + "Z")
        nodes = snapshot.get("nodes", [])
        links = snapshot.get("links", [])
        alerts = snapshot.get("alerts", [])

        # 提取事件 (新告警写入事件日志)
        for alert in alerts:
            self._log_event(
                timestamp=ts,
                event_type=alert.get("type", "unknown"),
                severity=alert.get("severity", "warning"),
                message=alert.get("message", ""),
                node_nwk=alert.get("node"),
            )

        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO topology_snapshots (timestamp, snapshot_json, node_count, link_count, alert_count, router_report_count) VALUES (?, ?, ?, ?, ?, ?)",
                (ts, json.dumps(snapshot, ensure_ascii=False), len(nodes), len(links), len(alerts), snapshot.get("router_reports", 0))
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"保存拓扑快照失败: {e}")

    def save_ed_scan(self, heatmap_data: dict):
        """保存 ED Scan 热力图数据"""
        ts = heatmap_data.get("timestamp", datetime.utcnow().isoformat() + "Z")
        nodes = heatmap_data.get("nodes", [])

        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO ed_scan_snapshots (timestamp, heatmap_json, node_count) VALUES (?, ?, ?)",
                (ts, json.dumps(heatmap_data, ensure_ascii=False), len(nodes))
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"保存 ED Scan 失败: {e}")

    def _log_event(self, timestamp: str, event_type: str, severity: str,
                    message: str, node_nwk: str = None, extra: dict = None):
        """写入事件日志"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO event_log (timestamp, event_type, severity, message, node_nwk, extra_json) VALUES (?, ?, ?, ?, ?, ?)",
                (timestamp, event_type, severity, message, node_nwk,
                 json.dumps(extra, ensure_ascii=False) if extra else None)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"写入事件日志失败: {e}")

    # ── 查询 ──────────────────────────────────────

    def get_topology_range(self, start: str, end: str, step: int = 1) -> list[dict]:
        """
        获取时间范围内的拓扑快照
        step: 每隔N条取一条 (降低数据量)
        """
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        if step > 1:
            # 先获取ID范围，再按步长取
            c.execute(
                "SELECT id FROM topology_snapshots WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp",
                (start, end)
            )
            ids = [row[0] for row in c.fetchall()]
            selected_ids = ids[::step]

            if not selected_ids:
                conn.close()
                return []

            placeholders = ",".join("?" * len(selected_ids))
            c.execute(
                f"SELECT timestamp, snapshot_json FROM topology_snapshots WHERE id IN ({placeholders}) ORDER BY timestamp",
                selected_ids
            )
        else:
            c.execute(
                "SELECT timestamp, snapshot_json FROM topology_snapshots WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp",
                (start, end)
            )

        results = []
        for ts, snap_json in c.fetchall():
            try:
                results.append(json.loads(snap_json))
            except:
                pass

        conn.close()
        return results

    def get_topology_at(self, timestamp: str) -> Optional[dict]:
        """获取最接近指定时间的快照"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "SELECT snapshot_json FROM topology_snapshots WHERE timestamp <= ? ORDER BY timestamp DESC LIMIT 1",
            (timestamp,)
        )
        row = c.fetchone()
        conn.close()

        if row:
            try:
                return json.loads(row[0])
            except:
                pass
        return None

    def get_ed_scan_range(self, start: str, end: str) -> list[dict]:
        """获取时间范围内的 ED Scan 数据"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "SELECT timestamp, heatmap_json FROM ed_scan_snapshots WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp",
            (start, end)
        )
        results = []
        for ts, data_json in c.fetchall():
            try:
                results.append(json.loads(data_json))
            except:
                pass
        conn.close()
        return results

    def get_events(self, start: str, end: str, severity: str = None,
                    event_type: str = None, limit: int = 100) -> list[dict]:
        """查询事件日志"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        query = "SELECT timestamp, event_type, severity, message, node_nwk, extra_json FROM event_log WHERE timestamp BETWEEN ? AND ?"
        params = [start, end]

        if severity:
            query += " AND severity = ?"
            params.append(severity)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        c.execute(query, params)

        results = []
        for ts, etype, sev, msg, nwk, extra in c.fetchall():
            results.append({
                "timestamp": ts,
                "event_type": etype,
                "severity": sev,
                "message": msg,
                "node_nwk": nwk,
                "extra": json.loads(extra) if extra else None,
            })

        conn.close()
        return results

    # ── 统计 ──────────────────────────────────────

    def get_time_range(self) -> dict:
        """获取数据的时间范围"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        c.execute("SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM topology_snapshots")
        topo_min, topo_max, topo_count = c.fetchone()

        c.execute("SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM ed_scan_snapshots")
        ed_min, ed_max, ed_count = c.fetchone()

        c.execute("SELECT COUNT(*) FROM event_log")
        event_count = c.fetchone()[0]

        conn.close()

        return {
            "topology": {
                "first": topo_min, "last": topo_max, "count": topo_count
            },
            "ed_scan": {
                "first": ed_min, "last": ed_max, "count": ed_count
            },
            "events": {
                "count": event_count
            },
        }

    def get_timeline_ticks(self, start: str, end: str, count: int = 20) -> list[str]:
        """获取时间轴刻度点"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            "SELECT timestamp FROM topology_snapshots WHERE timestamp BETWEEN ? AND ? ORDER BY timestamp",
            (start, end)
        )
        all_ts = [row[0] for row in c.fetchall()]
        conn.close()

        if not all_ts:
            return []

        step = max(1, len(all_ts) // count)
        return all_ts[::step]

    # ── 清理 ──────────────────────────────────────

    def cleanup(self, days: int = 7):
        """清理超过 N 天的原始数据"""
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"

        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM topology_snapshots WHERE timestamp < ?", (cutoff,))
        conn.execute("DELETE FROM ed_scan_snapshots WHERE timestamp < ?", (cutoff,))
        conn.execute("DELETE FROM event_log WHERE timestamp < ? AND severity = 'info'", (cutoff,))
        conn.commit()

        # VACUUM 压缩
        conn.execute("VACUUM")
        conn.close()

        logger.info(f"清理了 {cutoff} 之前的数据")
