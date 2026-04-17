"""
Zigbee 拓扑工具 - 单文件 MVP（模拟模式）
直接运行: python3 zigbee-topo-mock.py
浏览器打开: http://localhost:8000
"""

import asyncio
import json
import random
import logging
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════
#  模拟数据生成
# ══════════════════════════════════════════════════════

class MockCollector:
    def __init__(self, num_routers=8, num_seds=12):
        self.num_routers = num_routers
        self.num_seds = num_seds
        self._callbacks = []

    def on_update(self, cb):
        self._callbacks.append(cb)

    async def _notify(self, snapshot):
        for cb in self._callbacks:
            try:
                await cb(snapshot) if asyncio.iscoroutinefunction(cb) else cb(snapshot)
            except Exception as e:
                logger.error(f"callback error: {e}")

    def generate(self):
        nodes, links, alerts = [], [], []

        nodes.append({"nwk": "0x0000", "ieee": "00:12:4b:00:aa:00:00:00",
                       "type": "Coordinator", "status": "online"})

        router_nwks = []
        for i in range(1, self.num_routers + 1):
            nwk = f"0x{i:04X}"
            router_nwks.append(nwk)
            lqi = random.randint(150, 255)
            if random.random() < 0.12:
                lqi = random.randint(30, 80)
                alerts.append({"type": "weak_link", "severity": "critical" if lqi < 50 else "warning",
                                "message": f"{nwk} LQI={lqi} ({'极弱' if lqi<50 else '偏弱'})", "node": nwk})
            nodes.append({"nwk": nwk, "ieee": f"00:12:4b:00:bb:{i:02X}:00:00",
                           "type": "Router", "lqi": lqi, "depth": 1, "status": "online"})
            links.append({"source": "0x0000", "target": nwk, "lqi": lqi})

        for i, nwk in enumerate(router_nwks):
            if i + 1 < len(router_nwks):
                links.append({"source": nwk, "target": router_nwks[i+1],
                               "lqi": random.randint(100, 240)})
            if i + 2 < len(router_nwks) and random.random() < 0.5:
                links.append({"source": nwk, "target": router_nwks[i+2],
                               "lqi": random.randint(80, 200)})

        for i in range(self.num_seds):
            nwk = f"0x{self.num_routers+1+i:04X}"
            parent = random.choice(router_nwks)
            lqi = random.randint(80, 220)
            if random.random() < 0.06:
                alerts.append({"type": "orphan_sed", "severity": "warning",
                                "message": f"SED {nwk} 可能是孤儿节点", "node": nwk})
            nodes.append({"nwk": nwk, "ieee": f"00:12:4b:00:cc:{i:02X}:00:00",
                           "type": "Sleepy_End_Device", "lqi": lqi, "depth": 2, "status": "online"})
            links.append({"source": parent, "target": nwk, "lqi": lqi})

        return {"timestamp": datetime.utcnow().isoformat()+"Z",
                "coordinator": {"ieee": "00:12:4b:00:aa:00:00:00", "nwk": "0x0000"},
                "nodes": nodes, "links": links, "alerts": alerts}

    async def run(self, interval=10):
        while True:
            try:
                await self._notify(self.generate())
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break


# ══════════════════════════════════════════════════════
#  FastAPI
# ══════════════════════════════════════════════════════

class State:
    collector = None
    snapshot = {}
    clients: list = []
    task: Optional[asyncio.Task] = None
    history: list = []

state = State()

async def on_snapshot(snap):
    state.snapshot = snap
    state.history.append(snap)
    if len(state.history) > 360:
        state.history = state.history[-360:]
    data = json.dumps(snap, ensure_ascii=False)
    dead = []
    for ws in state.clients:
        try: await ws.send_text(data)
        except: dead.append(ws)
    for ws in dead: state.clients.remove(ws)

@asynccontextmanager
async def lifespan(app):
    state.collector = MockCollector(8, 12)
    state.collector.on_update(on_snapshot)
    state.task = asyncio.create_task(state.collector.run(10))
    logger.info("Mock mode started")
    yield
    if state.task: state.task.cancel()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/api/snapshot")
async def snap(): return state.snapshot or {"nodes":[],"links":[],"alerts":[]}

@app.get("/api/history")
async def hist(count:int=20): return state.history[-count:]

@app.websocket("/ws/topology")
async def ws_topo(ws: WebSocket):
    await ws.accept()
    state.clients.append(ws)
    if state.snapshot:
        await ws.send_text(json.dumps(state.snapshot, ensure_ascii=False))
    try:
        while True: await ws.receive_text()
    except: pass
    finally:
        if ws in state.clients: state.clients.remove(ws)

# ══════════════════════════════════════════════════════
#  前端 HTML/CSS/JS (内嵌)
# ══════════════════════════════════════════════════════

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Zigbee 网络拓扑</title>
<style>
:root{--bg:#0d1117;--panel:#161b22;--card:#1c2333;--border:#30363d;--text:#e6edf3;--muted:#8b949e;--green:#3fb950;--blue:#58a6ff;--yellow:#d29922;--orange:#db6d28;--red:#f85149}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);height:100vh;overflow:hidden}
.toolbar{display:flex;align-items:center;justify-content:space-between;padding:10px 20px;background:var(--panel);border-bottom:1px solid var(--border);height:52px}
.toolbar h1{font-size:17px;font-weight:600}
.status{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--muted)}
.status .dot{width:8px;height:8px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.main{display:flex;height:calc(100vh - 52px)}
.topo{flex:1;display:flex;flex-direction:column;border-right:1px solid var(--border)}
.topo-header{display:flex;justify-content:space-between;align-items:center;padding:8px 16px;background:var(--panel);border-bottom:1px solid var(--border);font-size:13px}
.legend{display:flex;gap:14px;font-size:12px;color:var(--muted)}
.legend span{display:flex;align-items:center;gap:4px}
.legend i{width:8px;height:8px;border-radius:50%;display:inline-block}
#svg{flex:1;width:100%}
.info{width:320px;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:12px}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px}
.card h3{font-size:14px;margin-bottom:10px;font-weight:600}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.stat{text-align:center;padding:8px;background:var(--panel);border-radius:6px}
.stat b{display:block;font-size:22px}
.stat small{font-size:11px;color:var(--muted)}
.alerts{max-height:300px;overflow-y:auto}
.alert{padding:8px 10px;margin-bottom:6px;border-radius:6px;font-size:12px;border-left:3px solid}
.alert.critical{background:rgba(248,81,73,.1);border-color:var(--red);color:var(--red)}
.alert.warning{background:rgba(210,153,34,.1);border-color:var(--yellow);color:var(--yellow)}
.empty{color:var(--muted);font-size:13px;text-align:center;padding:20px}
.detail-row{display:flex;justify-content:space-between;padding:4px 0;font-size:13px;border-bottom:1px solid var(--border)}
.detail-row .l{color:var(--muted)}.detail-row .v{font-weight:500}
.node-label{font-size:10px;fill:var(--text);pointer-events:none;text-anchor:middle}
</style>
</head>
<body>
<div class="toolbar">
  <h1>🏗️ Zigbee 网络拓扑</h1>
  <div class="status"><span class="dot"></span><span>实时监控中</span></div>
</div>
<div class="main">
  <div class="topo">
    <div class="topo-header">
      <span>拓扑视图</span>
      <div class="legend">
        <span><i style="background:var(--green)"></i>Coordinator</span>
        <span><i style="background:var(--blue)"></i>Router</span>
        <span><i style="background:var(--yellow)"></i>SED</span>
        <span><i style="background:var(--red)"></i>异常</span>
        <span style="margin-left:12px">LQI:</span>
        <span><i style="background:var(--green)"></i>&gt;200</span>
        <span><i style="background:var(--yellow)"></i>&gt;100</span>
        <span><i style="background:var(--orange)"></i>&gt;50</span>
        <span><i style="background:var(--red)"></i>&lt;50</span>
      </div>
    </div>
    <svg id="svg"></svg>
  </div>
  <div class="info">
    <div class="card"><h3>📊 网络摘要</h3>
      <div class="stats">
        <div class="stat"><b id="s-n">0</b><small>节点</small></div>
        <div class="stat"><b id="s-l">0</b><small>连线</small></div>
        <div class="stat"><b id="s-a">0</b><small>告警</small></div>
        <div class="stat"><b id="s-r">0</b><small>Router</small></div>
      </div>
    </div>
    <div class="card"><h3>⚠️ 告警</h3><div id="alerts" class="alerts"><p class="empty">暂无告警</p></div></div>
    <div class="card" id="detail-card" style="display:none"><h3>📌 节点详情</h3><div id="detail"></div></div>
  </div>
</div>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
const WS_URL=`ws://${location.host}/ws/topology`;
let sim=null;

const svg=d3.select("#svg");
let W=svg.node().parentElement.clientWidth;
let H=svg.node().parentElement.clientHeight;
svg.attr("viewBox",[0,0,W,H]);

const g=svg.append("g");
svg.call(d3.zoom().scaleExtent([.3,5]).on("zoom",e=>g.attr("transform",e.transform)));

function nColor(d){
  if(d.status==="offline")return"var(--red)";
  switch(d.type){
    case"Coordinator":return"var(--green)";
    case"Router":return"var(--blue)";
    case"Sleepy_End_Device":return"var(--yellow)";
    default:return"var(--muted)";
  }
}
function nR(d){return d.type==="Coordinator"?16:d.type==="Router"?10:7}
function lColor(l){return l==null?"#555":l>=200?"#3fb950":l>=100?"#d29922":l>=50?"#db6d28":"#f85149"}
function lW(l){return l==null?1:Math.max(1,l/80)}

function render(data){
  const nodes=data.nodes.map(d=>({...d}));
  const nMap={};nodes.forEach(n=>nMap[n.nwk]=n);
  const links=data.links.filter(l=>nMap[l.source]&&nMap[l.target])
    .map(l=>({source:l.source,target:l.target,lqi:l.lqi,route_status:l.route_status}));

  document.getElementById("s-n").textContent=nodes.length;
  document.getElementById("s-l").textContent=links.length;
  document.getElementById("s-a").textContent=(data.alerts||[]).length;
  document.getElementById("s-r").textContent=nodes.filter(n=>n.type==="Router").length;

  const ac=document.getElementById("alerts");
  const alerts=data.alerts||[];
  ac.innerHTML=alerts.length?alerts.map(a=>`<div class="alert ${a.severity}">${a.message}</div>`).join("")
    :'<p class="empty">暂无告警</p>';

  if(sim)sim.stop();
  sim=d3.forceSimulation(nodes)
    .force("link",d3.forceLink(links).id(d=>d.nwk).distance(80))
    .force("charge",d3.forceManyBody().strength(-200))
    .force("center",d3.forceCenter(W/2,H/2))
    .force("collision",d3.forceCollide().radius(d=>nR(d)+10));

  const link=g.selectAll(".lg").data(links,d=>d.source.nwk+"-"+d.target.nwk);
  link.exit().remove();
  const linkE=link.enter().append("g").attr("class","lg");
  linkE.append("line").attr("class","lk")
    .attr("stroke",d=>lColor(d.lqi)).attr("stroke-width",d=>lW(d.lqi))
    .attr("marker-end","url(#arrow)");
  linkE.append("text").attr("font-size","9px").attr("fill","#8b949e")
    .attr("text-anchor","middle").text(d=>d.lqi!=null?d.lqi:"");
  const linkA=linkE.merge(link);

  const node=g.selectAll(".ng").data(nodes,d=>d.nwk);
  node.exit().remove();
  const nodeE=node.enter().append("g").attr("class","ng")
    .call(d3.drag().on("start",ds).on("drag",dd).on("end",de));
  nodeE.append("circle").attr("r",d=>nR(d)+4).attr("fill",d=>nColor(d)).attr("opacity",.15);
  nodeE.append("circle").attr("class","nc").attr("r",d=>nR(d))
    .attr("fill",d=>nColor(d)).attr("stroke","#0d1117").attr("stroke-width",2);
  nodeE.append("text").attr("class","node-label").attr("dy",d=>nR(d)+14).text(d=>d.nwk);
  nodeE.on("click",(e,d)=>{
    document.getElementById("detail-card").style.display="block";
    document.getElementById("detail").innerHTML=
      `<div class="detail-row"><span class="l">NWK</span><span class="v">${d.nwk}</span></div>`
      +`<div class="detail-row"><span class="l">IEEE</span><span class="v">${d.ieee||"-"}</span></div>`
      +`<div class="detail-row"><span class="l">类型</span><span class="v">${d.type}</span></div>`
      +`<div class="detail-row"><span class="l">LQI</span><span class="v">${d.lqi!=null?d.lqi:"-"}</span></div>`
      +`<div class="detail-row"><span class="l">深度</span><span class="v">${d.depth!=null?d.depth:"-"}</span></div>`;
  });
  const nodeA=nodeE.merge(node);

  sim.on("tick",()=>{
    linkA.select("line").attr("x1",d=>d.source.x).attr("y1",d=>d.source.y).attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
    linkA.select("text").attr("x",d=>(d.source.x+d.target.x)/2).attr("y",d=>(d.source.y+d.target.y)/2-5);
    nodeA.attr("transform",d=>`translate(${d.x},${d.y})`);
  });
}
function ds(e,d){if(!e.active)sim.alphaTarget(.3).restart();d.fx=d.x;d.fy=d.y}
function dd(e,d){d.fx=e.x;d.fy=e.y}
function de(e,d){if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null}

// Arrow
svg.append("defs").append("marker").attr("id","arrow").attr("viewBox","0 -5 10 10")
  .attr("refX",25).attr("refY",0).attr("markerWidth",6).attr("markerHeight",6).attr("orient","auto")
  .append("path").attr("d","M0,-5L10,0L0,5").attr("fill","#555");

// WS
let ws=new WebSocket(WS_URL);
ws.onmessage=e=>render(JSON.parse(e.data));
ws.onclose=()=>setTimeout(()=>{ws=new WebSocket(WS_URL);ws.onmessage=e=>render(JSON.parse(e.data))},5000);

// Resize
window.addEventListener("resize",()=>{
  W=svg.node().parentElement.clientWidth;
  H=svg.node().parentElement.clientHeight;
  svg.attr("viewBox",[0,0,W,H]);
});
</script>
</body>
</html>
"""

@app.get("/")
async def index():
    return HTMLResponse(HTML)

# ══════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
