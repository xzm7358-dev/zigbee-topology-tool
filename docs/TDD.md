# ZigSight 技术设计文档 (TDD) v1.0

**日期：** 2026-04-17  
**版本：** v1.0  
**状态：** 评审中

---

## 一、项目结构

```
zigbee-topology-tool/
├── backend/
│   ├── main.py                 # FastAPI 入口，路由注册，生命周期
│   ├── collector.py             # EZSP 数据采集 (Coordinator 视角)
│   ├── router_report.py         # Router 上报数据解析与聚合
│   ├── channel_analyzer.py      # 信道干扰分析引擎
│   ├── channel_api.py           # 信道分析 REST API
│   ├── history_store.py         # SQLite 时序存储
│   ├── history_api.py           # 历史回放 REST API
│   ├── mock_collector.py        # 模拟数据源 (开发调试)
│   ├── requirements.txt
│   └── topology_history.db      # SQLite 数据库 (运行时生成)
│
├── firmware/
│   ├── zigbee_topology_report.h  # 自定义 ZCL Cluster 定义
│   └── zigbee_topology_report.c  # EmberZNet C 实现 (Router 上报)
│
├── frontend/
│   ├── index.html               # 实时拓扑页
│   ├── channel.html             # 信道干扰热力图页
│   ├── replay.html              # 时序回放页
│   ├── css/style.css
│   └── src/topology.js
│
├── test_ezsp_connect.py          # EZSP 连接测试 (基础)
├── test_ezsp_connect_v2.py       # EZSP 连接测试 (增强)
├── zigbee-topo-mock.py           # 单文件 MVP (零配置)
├── docs/
│   ├── PRD.md                    # 产品需求文档
│   └── TDD.md                    # 本文档
└── README.md
```

---

## 二、核心数据流

### 2.1 实时采集流

```
MG21 Coordinator ──UART──→ bellows EZSP ──→ collector.py
                                              │
                              ┌────────────────┤
                              │                │
                              ▼                ▼
                         诊断引擎          构建 JSON 快照
                              │                │
                              ▼                ▼
                          生成告警        WebSocket 推送
                              │                │
                              └───────┬────────┘
                                      ▼
                               history_store.py
                               (SQLite 持久化)
```

### 2.2 Router 上报流

```
Router 固件 ──ZCL 0xFC00──→ Coordinator ──EZSP──→ collector.py
  (TLV 编码)                (透传)                │
                                                   ▼
                                          router_report.py
                                          (TLV 解析 + 分帧重组)
                                                   │
                                                   ▼
                                          合并到全网拓扑快照
```

### 2.3 回放查询流

```
前端 replay.html ──REST──→ history_api.py ──→ history_store.py
  (时间轴拖拽)              (参数校验)          (SQLite 查询)
                                                   │
                                                   ▼
                                              返回历史快照
                                                   │
                                                   ▼
                                          前端渲染历史拓扑
```

---

## 三、API 设计

### 3.1 拓扑 API

| Method | Path | 描述 |
|---|---|---|
| POST | `/api/connect?port=&baudrate=` | 连接 Coordinator |
| POST | `/api/disconnect` | 断开连接 |
| GET | `/api/snapshot` | 获取最新快照 |
| GET | `/api/status` | 服务状态 |
| WS | `/ws/topology` | WebSocket 实时推送 |

### 3.2 信道分析 API

| Method | Path | 描述 |
|---|---|---|
| POST | `/api/channel/scan` | 提交 ED Scan 数据 |
| GET | `/api/channel/heatmap` | 获取热力图数据 |
| GET | `/api/channel/trend?node_nwk=&channel=&count=` | RSSI 趋势 |
| GET | `/api/channel/recommendations` | 信道推荐 |
| POST | `/api/channel/mock-scan` | 注入模拟数据 |

### 3.3 历史回放 API

| Method | Path | 描述 |
|---|---|---|
| GET | `/api/history/range` | 数据时间范围 |
| GET | `/api/history/topology?start=&end=&step=` | 拓扑快照列表 |
| GET | `/api/history/topology/at?timestamp=` | 指定时间点快照 |
| GET | `/api/history/edscan?start=&end=` | ED Scan 历史 |
| GET | `/api/history/events?start=&end=&severity=&event_type=` | 事件日志 |
| GET | `/api/history/timeline?start=&end=&ticks=` | 时间轴刻度 |
| GET | `/api/history/cleanup?days=` | 清理历史数据 |

---

## 四、数据库 Schema

### 4.1 topology_snapshots

```sql
CREATE TABLE topology_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,          -- ISO8601
    snapshot_json TEXT NOT NULL,       -- 完整 JSON
    node_count INTEGER,
    link_count INTEGER,
    alert_count INTEGER,
    router_report_count INTEGER DEFAULT 0
);
CREATE INDEX idx_topo_ts ON topology_snapshots(timestamp);
```

### 4.2 ed_scan_snapshots

```sql
CREATE TABLE ed_scan_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    heatmap_json TEXT NOT NULL,
    node_count INTEGER
);
CREATE INDEX idx_edscan_ts ON ed_scan_snapshots(timestamp);
```

### 4.3 event_log

```sql
CREATE TABLE event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,         -- weak_link / route_failed / ...
    severity TEXT NOT NULL,           -- critical / warning / info
    message TEXT NOT NULL,
    node_nwk TEXT,
    extra_json TEXT
);
CREATE INDEX idx_event_ts ON event_log(timestamp);
CREATE INDEX idx_event_type ON event_log(event_type);
```

---

## 五、固件接口规范

### 5.1 ZCL Cluster 0xFC00 定义

| 项目 | 值 |
|---|---|
| Cluster ID | 0xFC00 (厂商自定义范围) |
| Profile | 0x0104 (HA) |
| Endpoint | 2 |

### 5.2 Attributes

| ID | 类型 | 描述 |
|---|---|---|
| 0x0000 | octet_string | 邻居表摘要 |
| 0x0001 | octet_string | 路由表摘要 |
| 0x0002 | IEEE_address | 父节点 EUI64 |
| 0x0003 | octet_string | ED Scan 结果 |
| 0x0004 | uint32 | 运行时间(秒) |
| 0x0010 | uint16 | 上报间隔(秒)，可写 |

### 5.3 Commands (Server → Client)

| ID | 描述 |
|---|---|
| 0x00 | 请求完整上报 |
| 0x01 | 触发 ED Scan |
| 0x02 | 设置上报间隔 |

### 5.4 TLV 编码格式

| Tag | Length | Value | 说明 |
|---|---|---|---|
| 0x01 | 11 | nwk:2B + ieee:8B + lqi:1B | 邻居条目 |
| 0x02 | 5 | dest:2B + nextHop:2B + status:1B | 路由条目 |
| 0x03 | N | ch:1B + rssi:1B × N | ED Scan |
| 0x04 | 10 | parentNwk:2B + parentIeee:8B | 父节点 |
| 0x05 | 4 | seconds:4B | 运行时间 |

### 5.5 分帧规则

- ZCL 帧有效载荷上限约 80 字节
- 大数据分帧发送，帧格式: `[ZCL_header:3B][frame_index:1B][total_frames:1B][TLV_data...]`
- 接收端按 source_nwk + frame_index 缓存，全部帧到齐后合并解析

---

## 六、诊断规则引擎

### 6.1 规则定义

```python
class DiagRule:
    name: str           # 规则名称
    severity: str       # critical / warning / info
    condition: callable # 判断条件
    message: str        # 告警消息模板
    suggestion: str     # 修复建议
```

### 6.2 已实现规则

| 规则 | 数据源 | 条件 | 建议 |
|---|---|---|---|
| weak_link | 邻居表 LQI | LQI < 50/100 | 在附近增加中继 Router |
| route_failed | 路由表 status | Discovery_Failed | 检查目标节点是否在线 |
| orphan_sed | 子节点+邻居表 | SED 不在邻居表 | 触发 SED 重新关联 |
| route_loop | 路由表 | A↔B 互为下一跳 | 重置路由表 |

### 6.3 待实现规则

| 规则 | 数据源 | 条件 | 建议 |
|---|---|---|---|
| route_storm | 嗅探/Router上报 | 1min内 Route Request > 阈值 | 检查频繁掉线的 Router |
| high_retry | 嗅探 | 单链路 APS 重传率 > 15% | 增加中继或调整位置 |
| device_offline | 快照 | 节点连续 N 次未出现 | 检查供电/距离 |
| topology_change | 快照对比 | 新增/移除节点 | - |

---

## 七、前端架构

### 7.1 页面结构

```
/ (index.html)        → 实时拓扑监控
  ├── 左侧: D3.js force-directed 拓扑图
  └── 右侧: 统计 + 告警 + 节点详情

/channel (channel.html) → 信道干扰分析
  ├── 左侧: 热力矩阵 + WiFi 重叠图 + RSSI 趋势
  └── 右侧: 告警 + 信道推荐 + 图例

/replay (replay.html)   → 时序回放
  ├── 上方: D3.js 拓扑图 (同 index)
  ├── 右侧: 统计 + 事件日志
  └── 下方: 时间轴 + 播放控制
```

### 7.2 统一设计语言 (待实现)

当前3个页面独立实现，Phase 2 需统一为：

```
统一导航栏: [拓扑] [信道] [回放] [告警]
统一主题: 暗色主题 (#0d1117)
统一组件: 卡片/按钮/表格/图表样式统一
路由: SPA (单页应用) 或顶部 Tab 切换
```

### 7.3 技术选型建议

| 方案 | 优点 | 缺点 | 建议 |
|---|---|---|---|
| 原生 HTML/JS (当前) | 零依赖，轻量 | 组件复用差，维护成本高 | MVP 够用 |
| React + Ant Design | 组件丰富，工程化好 | 打包体积大，开发门槛高 | Phase 3 |
| Vue + Element Plus | 中文生态好，学习曲线低 | 同上 | Phase 3 |
| Svelte | 轻量高性能 | 生态小 | 可选 |

---

## 八、性能优化策略

### 8.1 数据采集层

- 10s 轮询 Coordinator，60s 轮询 Router 上报
- 持久化降频：每 6 个快照 (60s) 写一次 SQLite
- 只在数据变化时推送 WebSocket

### 8.2 前端渲染

- D3.js 节点数 > 100 时启用虚拟渲染
- 时间轴只加载当前帧，不预加载全部历史
- 热力图用 CSS 渲染而非 Canvas (性能足够)

### 8.3 数据库

- 关键字段建索引 (timestamp, event_type)
- 定期 VACUUM 压缩
- 大范围查询自动降采样 (step 参数)

---

*本文档随代码迭代持续更新。*
