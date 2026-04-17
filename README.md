# Zigbee 网络拓扑分析工具

实时可视化 Zigbee 网络拓扑，诊断链路质量和网络问题。

## 快速启动

```bash
cd backend
pip install -r requirements.txt

# 模拟模式（无需硬件，直接看效果）
python main.py --mock

# 真实模式（连接 MG21 Coordinator）
python main.py --port /dev/ttyUSB0
```

浏览器打开 http://localhost:8000

## 项目结构

```
zigbee-topology-tool/
├── backend/
│   ├── main.py              # FastAPI 入口 + 路由
│   ├── collector.py          # EZSP 数据采集（真实硬件）
│   ├── mock_collector.py     # 模拟数据生成（开发调试）
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── src/
│       └── topology.js       # D3.js 拓扑渲染 + 诊断
```

## 功能

- ✅ 实时拓扑图（D3.js force-directed）
- ✅ LQI 颜色编码（绿/黄/橙/红）
- ✅ 节点类型区分（Coordinator/Router/SED）
- ✅ 弱链路检测 + 告警
- ✅ 路由失败检测
- ✅ SED 孤儿节点检测
- ✅ WebSocket 实时推送
- ✅ 历史快照 API（时序回放预留）
- ✅ 模拟模式（无硬件开发）

## 待扩展

- [ ] 路由环路检测
- [ ] 重传率统计
- [ ] 时序回放（时间轴滑块）
- [ ] 信道干扰分析
- [ ] 固件端自定义 ZCL cluster 上报
- [ ] 嗅探数据融合
- [ ] 右键操作（强制重新入网/LED闪烁定位）

## 依赖

- Python 3.9+
- bellows (EZSP 通信)
- FastAPI + uvicorn
- D3.js v7 (前端 CDN)
