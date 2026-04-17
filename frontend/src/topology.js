/**
 * Zigbee 拓扑可视化 - D3.js Force-Directed Graph
 */

const WS_URL = `ws://${location.host}/ws/topology`;
const API_BASE = `http://${location.host}/api`;

let ws = null;
let simulation = null;
let currentData = { nodes: [], links: [], alerts: [] };

// ── 初始化 SVG ──────────────────────────────────────
const svg = d3.select("#topology-svg");
const width = svg.node().parentElement.clientWidth;
const height = svg.node().parentElement.clientHeight;
svg.attr("viewBox", [0, 0, width, height]);

// 缩放
const g = svg.append("g");
svg.call(
    d3.zoom()
        .scaleExtent([0.3, 5])
        .on("zoom", (event) => g.attr("transform", event.transform))
);

// Arrow marker for links
svg.append("defs").append("marker")
    .attr("id", "arrow")
    .attr("viewBox", "0 -5 10 10")
    .attr("refX", 25)
    .attr("refY", 0)
    .attr("markerWidth", 6)
    .attr("markerHeight", 6)
    .attr("orient", "auto")
    .append("path")
    .attr("d", "M0,-5L10,0L0,5")
    .attr("fill", "#555");

// ── 颜色映射 ────────────────────────────────────────
function nodeColor(d) {
    if (d.status === "offline") return "var(--alert-color)";
    switch (d.type) {
        case "Coordinator": return "var(--coord-color)";
        case "Router": return "var(--router-color)";
        case "Sleepy_End_Device": return "var(--sed-color)";
        default: return "var(--text-muted)";
    }
}

function nodeRadius(d) {
    switch (d.type) {
        case "Coordinator": return 16;
        case "Router": return 10;
        case "Sleepy_End_Device": return 7;
        default: return 8;
    }
}

function lqiColor(lqi) {
    if (lqi === null || lqi === undefined) return "#555";
    if (lqi >= 200) return "#3fb950";  // green
    if (lqi >= 100) return "#d29922";  // yellow
    if (lqi >= 50)  return "#db6d28";  // orange
    return "#f85149";                    // red
}

function lqiWidth(lqi) {
    if (lqi === null || lqi === undefined) return 1;
    return Math.max(1, lqi / 80);
}

// ── 渲染拓扑 ────────────────────────────────────────
function render(data) {
    currentData = data;
    const nodes = data.nodes.map(d => ({ ...d }));
    const links = data.links.map(d => ({ ...d }));

    // 构建 ID 索引
    const nodeMap = {};
    nodes.forEach(n => nodeMap[n.nwk] = n);

    // 清理无效 link
    const validLinks = links.filter(l => nodeMap[l.source] && nodeMap[l.target]);

    // D3 需要的格式
    const d3Links = validLinks.map(l => ({
        source: l.source,
        target: l.target,
        lqi: l.lqi,
        route_status: l.route_status,
    }));

    // 更新统计
    updateSummary(nodes, d3Links, data.alerts || []);
    updateAlerts(data.alerts || []);

    // Force simulation
    if (simulation) simulation.stop();

    simulation = d3.forceSimulation(nodes)
        .force("link", d3.forceLink(d3Links).id(d => d.nwk).distance(80))
        .force("charge", d3.forceManyBody().strength(-200))
        .force("center", d3.forceCenter(width / 2, height / 2))
        .force("collision", d3.forceCollide().radius(d => nodeRadius(d) + 10));

    // Links
    const link = g.selectAll(".link-group")
        .data(d3Links, d => `${d.source}-${d.target}`);

    link.exit().remove();

    const linkEnter = link.enter()
        .append("g")
        .attr("class", "link-group");

    linkEnter.append("line")
        .attr("class", "link")
        .attr("stroke", d => lqiColor(d.lqi))
        .attr("stroke-width", d => lqiWidth(d.lqi))
        .attr("marker-end", "url(#arrow)");

    // LQI 标签
    linkEnter.append("text")
        .attr("class", "lqi-label")
        .attr("font-size", "9px")
        .attr("fill", "#8b949e")
        .attr("text-anchor", "middle")
        .text(d => d.lqi !== null && d.lqi !== undefined ? d.lqi : "");

    const linkAll = linkEnter.merge(link);

    // Nodes
    const node = g.selectAll(".node-group")
        .data(nodes, d => d.nwk);

    node.exit().remove();

    const nodeEnter = node.enter()
        .append("g")
        .attr("class", "node-group")
        .call(d3.drag()
            .on("start", dragStarted)
            .on("drag", dragged)
            .on("end", dragEnded));

    // 光晕效果
    nodeEnter.append("circle")
        .attr("class", "node-glow")
        .attr("r", d => nodeRadius(d) + 4)
        .attr("fill", d => nodeColor(d))
        .attr("opacity", 0.15);

    // 节点圆
    nodeEnter.append("circle")
        .attr("class", "node-circle")
        .attr("r", d => nodeRadius(d))
        .attr("fill", d => nodeColor(d))
        .attr("stroke", "#0d1117")
        .attr("stroke-width", 2);

    // 节点标签
    nodeEnter.append("text")
        .attr("class", "node-label")
        .attr("dy", d => nodeRadius(d) + 14)
        .text(d => d.nwk);

    // 点击事件
    nodeEnter.on("click", (event, d) => showNodeDetail(d));
    nodeEnter.on("contextmenu", (event, d) => {
        event.preventDefault();
        // 右键操作：将来扩展
    });

    const nodeAll = nodeEnter.merge(node);

    // Tick
    simulation.on("tick", () => {
        linkAll.select("line")
            .attr("x1", d => d.source.x)
            .attr("y1", d => d.source.y)
            .attr("x2", d => d.target.x)
            .attr("y2", d => d.target.y);

        linkAll.select("text")
            .attr("x", d => (d.source.x + d.target.x) / 2)
            .attr("y", d => (d.source.y + d.target.y) / 2 - 5);

        nodeAll.attr("transform", d => `translate(${d.x},${d.y})`);
    });
}

// ── 拖拽 ────────────────────────────────────────────
function dragStarted(event, d) {
    if (!event.active) simulation.alphaTarget(0.3).restart();
    d.fx = d.x;
    d.fy = d.y;
}
function dragged(event, d) {
    d.fx = event.x;
    d.fy = event.y;
}
function dragEnded(event, d) {
    if (!event.active) simulation.alphaTarget(0);
    d.fx = null;
    d.fy = null;
}

// ── UI 更新 ─────────────────────────────────────────
function updateSummary(nodes, links, alerts) {
    document.getElementById("stat-nodes").textContent = nodes.length;
    document.getElementById("stat-links").textContent = links.length;
    document.getElementById("stat-alerts").textContent = alerts.length;
    document.getElementById("stat-routers").textContent =
        nodes.filter(n => n.type === "Router").length;
}

function updateAlerts(alerts) {
    const container = document.getElementById("alerts-list");
    if (!alerts.length) {
        container.innerHTML = '<p class="empty">暂无告警</p>';
        return;
    }
    container.innerHTML = alerts.map(a => `
        <div class="alert-item ${a.severity}">
            ${a.message}
        </div>
    `).join("");
}

function showNodeDetail(d) {
    const card = document.getElementById("node-detail-card");
    card.style.display = "block";
    document.getElementById("node-detail").innerHTML = `
        <div class="detail-row"><span class="label">NWK</span><span class="value">${d.nwk}</span></div>
        <div class="detail-row"><span class="label">IEEE</span><span class="value">${d.ieee || "-"}</span></div>
        <div class="detail-row"><span class="label">类型</span><span class="value">${d.type}</span></div>
        <div class="detail-row"><span class="label">LQI</span><span class="value">${d.lqi ?? "-"}</span></div>
        <div class="detail-row"><span class="label">深度</span><span class="value">${d.depth ?? "-"}</span></div>
        <div class="detail-row"><span class="label">状态</span><span class="value">${d.status}</span></div>
    `;
}

// ── WebSocket ───────────────────────────────────────
function connectWS() {
    ws = new WebSocket(WS_URL);
    ws.onopen = () => console.log("WS 已连接");
    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        render(data);
    };
    ws.onclose = () => {
        console.log("WS 断开，5s 后重连");
        setTimeout(connectWS, 5000);
    };
    ws.onerror = (err) => console.error("WS 错误", err);
}

// ── API 调用 ────────────────────────────────────────
async function connectCoordinator() {
    const port = document.getElementById("port-select").value;
    const resp = await fetch(`${API_BASE}/connect?port=${encodeURIComponent(port)}`, {
        method: "POST",
    });
    const result = await resp.json();
    if (result.status === "connecting" || result.status === "already_running") {
        updateConnectionStatus(true);
    }
}

async function disconnectCoordinator() {
    await fetch(`${API_BASE}/disconnect`, { method: "POST" });
    updateConnectionStatus(false);
}

async function refreshSnapshot() {
    const resp = await fetch(`${API_BASE}/snapshot`);
    const data = await resp.json();
    render(data);
}

function updateConnectionStatus(connected) {
    const indicator = document.getElementById("status");
    const btnConnect = document.getElementById("btn-connect");
    const btnDisconnect = document.getElementById("btn-disconnect");

    if (connected) {
        indicator.className = "status-indicator connected";
        indicator.querySelector(".text").textContent = "已连接";
        btnConnect.disabled = true;
        btnDisconnect.disabled = false;
    } else {
        indicator.className = "status-indicator";
        indicator.querySelector(".text").textContent = "未连接";
        btnConnect.disabled = false;
        btnDisconnect.disabled = true;
    }
}

// ── 启动 ────────────────────────────────────────────
connectWS();

// 加载 D3（如果没本地文件，用 CDN fallback）
if (typeof d3 === "undefined") {
    const script = document.createElement("script");
    script.src = "https://d3js.org/d3.v7.min.js";
    script.onload = () => connectWS();
    document.head.appendChild(script);
}
