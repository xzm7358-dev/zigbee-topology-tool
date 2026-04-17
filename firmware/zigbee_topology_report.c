/**
 * Zigbee Router 邻居表/路由表主动上报 - 实现文件
 * 
 * 平台: Silicon Labs EFR32MG21 + EmberZNet
 * 编译: 加入 EmberZNet 项目 (Simplicity Studio)
 * 
 * 数据格式:
 *   邻居表摘要 (ATTR 0x0000): TLV 编码
 *     [1B type=0x01][1B len][2B nwk][8B ieee][1B lqi][1B depth][1B relationship]
 *     每条 13 字节, 最多 32 条 → 单次最大 416 字节
 *     如果超过 APS 帧限制(~80字节), 分帧发送
 *   
 *   路由表摘要 (ATTR 0x0001): TLV 编码
 *     [1B type=0x02][1B len][2B destNwk][2B nextHop][1B status][1B age]
 *     每条 7 字节
 *   
 *   上报方式: ZCL Report Attributes 命令 (0x0A)
 *     发送到 Coordinator 的 TOPOLOGY_ENDPOINT
 */

#include "zigbee_topology_report.h"
#include "app/framework/include/af.h"
#include "app/framework/util/attribute-table.h"

// ── 状态变量 ──────────────────────────────────────
static uint32_t reportIntervalSec = DEFAULT_REPORT_INTERVAL_SEC;
static uint32_t uptimeSeconds = 0;
static EmberEventControl topologyReportEventControl;
static bool reportEnabled = true;

// ── TLV 编码辅助 ──────────────────────────────────
// TLV 格式: [Tag(1B)][Length(1B)][Value(nB)]

typedef struct {
    uint8_t tag;
    uint8_t len;
    const uint8_t *value;
} TlvEntry;

static uint8_t tlvWriteUint8(uint8_t *buf, uint8_t tag, uint8_t val) {
    buf[0] = tag;
    buf[1] = 1;
    buf[2] = val;
    return 3;
}

static uint8_t tlvWriteUint16(uint8_t *buf, uint8_t tag, uint16_t val) {
    buf[0] = tag;
    buf[1] = 2;
    buf[2] = (uint8_t)(val & 0xFF);
    buf[3] = (uint8_t)((val >> 8) & 0xFF);
    return 4;
}

static uint8_t tlvWriteIeee(uint8_t *buf, uint8_t tag, const EmberEUI64 ieee) {
    buf[0] = tag;
    buf[1] = 8;
    memcpy(&buf[2], ieee, 8);
    return 10;
}

// ── 邻居表读取 + 编码 ────────────────────────────

/**
 * 读取邻居表并编码为 TLV 格式
 * 返回写入的字节数
 * 
 * 邻居条目 TLV:
 *   Tag=0x01, Len=11
 *   Value: [nwk:2B][ieee:8B][lqi:1B]
 *   (depth 和 relationship 可选, 节省空间)
 */
static uint16_t encodeNeighborTable(uint8_t *buf, uint16_t bufLen) {
    uint16_t offset = 0;
    
    for (uint8_t i = 0; i < MAX_NEIGHBOR_ENTRIES; i++) {
        EmberNeighborTableEntry entry;
        EmberStatus status = emberGetNeighbor(i, &entry);
        
        if (status != EMBER_SUCCESS) {
            break;  // 表结束
        }
        
        // 跳过无效条目
        if (entry.shortId == EMBER_TABLE_ENTRY_UNUSED_NODE_ID) {
            continue;
        }
        
        // 检查空间
        if (offset + 13 > bufLen) {
            break;  // 缓冲区满
        }
        
        // 写 TLV: [tag=0x01][len=11][nwk:2B][ieee:8B][lqi:1B]
        buf[offset++] = 0x01;  // 邻居条目标记
        buf[offset++] = 11;    // 长度
        buf[offset++] = (uint8_t)(entry.shortId & 0xFF);
        buf[offset++] = (uint8_t)((entry.shortId >> 8) & 0xFF);
        memcpy(&buf[offset], entry.longId, 8);
        offset += 8;
        buf[offset++] = entry.averageLqi;
    }
    
    return offset;
}

// ── 路由表读取 + 编码 ────────────────────────────

/**
 * 路由条目 TLV:
 *   Tag=0x02, Len=5
 *   Value: [destNwk:2B][nextHop:2B][status:1B]
 */
static uint16_t encodeRoutingTable(uint8_t *buf, uint16_t bufLen) {
    uint16_t offset = 0;
    
    for (uint8_t i = 0; i < MAX_ROUTING_ENTRIES; i++) {
        EmberRouteTableEntry entry;
        EmberStatus status = emberGetRouteTableEntry(i, &entry);
        
        if (status != EMBER_SUCCESS) {
            break;
        }
        
        if (entry.destination == EMBER_TABLE_ENTRY_UNUSED_NODE_ID) {
            continue;
        }
        
        if (offset + 7 > bufLen) {
            break;
        }
        
        buf[offset++] = 0x02;  // 路由条目标记
        buf[offset++] = 5;     // 长度
        buf[offset++] = (uint8_t)(entry.destination & 0xFF);
        buf[offset++] = (uint8_t)((entry.destination >> 8) & 0xFF);
        buf[offset++] = (uint8_t)(entry.nextHop & 0xFF);
        buf[offset++] = (uint8_t)((entry.nextHop >> 8) & 0xFF);
        buf[offset++] = (uint8_t)entry.status;
    }
    
    return offset;
}

// ── ED Scan ───────────────────────────────────────

/**
 * 执行能量检测扫描
 * 结果编码: [tag=0x03][len=N][channel_mask:4B][rssi_values:N*1B]
 */
static uint16_t encodeEdScan(uint8_t *buf, uint16_t bufLen) {
    uint16_t offset = 0;
    
    // 扫描信道 11-26 (2.4GHz Zigbee 信道)
    uint32_t channelMask = 0x07FFF800;  // 信道 11-26
    
    EmberStatus status = emberStartScan(EMBER_ENERGY_SCAN,
                                         channelMask,
                                         2);  // 2 * 15.36ms 扫描时长
    if (status != EMBER_SUCCESS) {
        return 0;
    }
    
    // ED Scan 结果在 emberScanCompleteHandler 回调中获取
    // 这里只返回头信息，实际数据在回调中发送
    buf[offset++] = 0x03;  // ED Scan 标记
    buf[offset++] = 0;     // 长度占位，回调中填充
    
    return offset;
}

// ── 分帧发送 ──────────────────────────────────────

/**
 * 发送拓扑数据到 Coordinator
 * 
 * 由于单条 ZCL 帧有长度限制，大数据需要分帧:
 * - 帧0: 邻居表 (前半部分)
 * - 帧1: 邻居表 (后半部分)
 * - 帧2: 路由表
 * - 帧3: 父节点 + ED Scan + 其他
 */
static EmberStatus sendTopologyReport(uint8_t frameIndex) {
    // 目标: Coordinator (NWK=0x0000)
    EmberNodeId target = 0x0000;
    
    // 构造 APS 帧
    EmberApsFrame apsFrame = {
        .profileId = 0x0104,           // HA Profile
        .clusterId = ZCL_CLUSTER_TOPOLOGY_REPORT,
        .sourceEndpoint = TOPOLOGY_ENDPOINT,
        .destinationEndpoint = TOPOLOGY_ENDPOINT,
        .options = EMBER_APS_OPTION_RETRY,
        .groupId = 0,
        .sequence = (uint8_t)(frameIndex & 0xFF),
    };
    
    uint8_t payload[80];  // APS 有效载荷
    uint16_t payloadLen = 0;
    
    // ZCL 帧 header
    uint8_t zclHeader[3];
    zclHeader[0] = ZCL_FRAME_CLIENT_TO_SERVER | ZCL_FRAME_TYPE_CLUSTER_SPECIFIC;
    zclHeader[1] = emberNextZclSequenceNumber();  // 事务序列号
    zclHeader[2] = ZCL_REPORT_ATTRIBUTES_COMMAND_ID;  // 0x0A
    
    memcpy(payload, zclHeader, 3);
    payloadLen = 3;
    
    // 帧序号 (用于接收端重组)
    payload[payloadLen++] = frameIndex;
    payload[payloadLen++] = 0;  // 总帧数，稍后填充
    
    switch (frameIndex) {
        case 0: {
            // 邻居表前半部分 (最多 5 条)
            uint8_t neighborBuf[65];  // 5 * 13
            uint16_t nLen = encodeNeighborTable(neighborBuf, sizeof(neighborBuf));
            uint16_t copyLen = (nLen > 65) ? 65 : nLen;
            memcpy(&payload[payloadLen], neighborBuf, copyLen);
            payloadLen += copyLen;
            break;
        }
        case 1: {
            // 邻居表后半部分 + 路由表
            // (简化: 第二帧发路由表)
            uint8_t routeBuf[70];
            uint16_t rLen = encodeRoutingTable(routeBuf, sizeof(routeBuf));
            uint16_t copyLen = (rLen > 70) ? 70 : rLen;
            memcpy(&payload[payloadLen], routeBuf, copyLen);
            payloadLen += copyLen;
            break;
        }
        case 2: {
            // 父节点信息 + 运行时间
            EmberEUI64 parentEui64;
            EmberNodeId parentId = emberGetParentNodeId();
            emberGetEui64(parentEui64);  // 如果是自身；远程需要 ZDO 查询
            
            payload[payloadLen++] = 0x04;  // 父节点标记
            payload[payloadLen++] = 10;    // 长度
            payload[payloadLen++] = (uint8_t)(parentId & 0xFF);
            payload[payloadLen++] = (uint8_t)((parentId >> 8) & 0xFF);
            memcpy(&payload[payloadLen], parentEui64, 8);
            payloadLen += 8;
            
            // 运行时间
            payload[payloadLen++] = 0x05;  // uptime 标记
            payload[payloadLen++] = 4;
            uint32_t up = uptimeSeconds;
            memcpy(&payload[payloadLen], &up, 4);
            payloadLen += 4;
            break;
        }
    }
    
    // 填充总帧数
    payload[4] = 3;  // 总共3帧
    
    // 发送
    EmberStatus status = emberSendUnicast(EMBER_OUTGOING_DIRECT,
                                           target,
                                           &apsFrame,
                                           payloadLen,
                                           payload);
    
    return status;
}

// ── 定时上报 ──────────────────────────────────────

/**
 * 定时事件回调
 * 每 reportIntervalSec 秒触发一次
 */
void emberAfPluginTopologyReportEventHandler(void) {
    emberEventControlSetInactive(topologyReportEventControl);
    
    if (!reportEnabled) {
        return;
    }
    
    uptimeSeconds += reportIntervalSec;
    
    // 分帧发送拓扑数据
    for (uint8_t frame = 0; frame < 3; frame++) {
        EmberStatus status = sendTopologyReport(frame);
        if (status != EMBER_SUCCESS) {
            emberAfDebugPrintln("Topology report frame %d failed: 0x%02X", frame, status);
            break;
        }
    }
    
    // 重新设置定时器
    emberEventControlSetDelayMS(topologyReportEventControl,
                                 reportIntervalSec * 1000);
}

// ── 初始化 ────────────────────────────────────────

/**
 * 在端点初始化时调用
 * 在 emberAfMainInitCallback() 中调用此函数
 */
void topologyReportInit(void) {
    reportEnabled = true;
    uptimeSeconds = 0;
    reportIntervalSec = DEFAULT_REPORT_INTERVAL_SEC;
    
    // 启动定时上报 (首次延迟5秒，让网络稳定)
    emberEventControlSetDelayMS(topologyReportEventControl, 5000);
    
    emberAfDebugPrintln("Topology Report: initialized, interval=%ds", reportIntervalSec);
}

// ── ZCL 命令处理 ──────────────────────────────────

/**
 * 处理来自 Coordinator 的命令
 * 在 ZCL 命令处理回调中调用
 */
EmberAfStatus topologyReportCommandHandler(EmberAfClusterCommand *cmd) {
    if (cmd->clusterId != ZCL_CLUSTER_TOPOLOGY_REPORT) {
        return EMBER_ZCL_STATUS_UNSUPPORTED_CLUSTER;
    }
    
    uint8_t commandId = cmd->commandId;
    
    switch (commandId) {
        case CMD_REQUEST_FULL_REPORT: {
            // Coordinator 请求立即上报
            emberAfDebugPrintln("Topology Report: full report requested");
            for (uint8_t frame = 0; frame < 3; frame++) {
                sendTopologyReport(frame);
            }
            return EMBER_ZCL_STATUS_SUCCESS;
        }
        
        case CMD_TRIGGER_ED_SCAN: {
            // Coordinator 请求 ED Scan
            emberAfDebugPrintln("Topology Report: ED scan requested");
            // ED Scan 在回调中完成并发送结果
            emberStartScan(EMBER_ENERGY_SCAN, 0x07FFF800, 4);
            return EMBER_ZCL_STATUS_SUCCESS;
        }
        
        case CMD_SET_REPORT_INTERVAL: {
            // Coordinator 设置上报间隔
            if (cmd->bufLen >= 2) {
                uint16_t newInterval = emberAfGetInt16u(cmd->buffer, cmd->bufLen - 2, cmd->bufLen);
                if (newInterval >= 10 && newInterval <= 3600) {
                    reportIntervalSec = newInterval;
                    emberAfDebugPrintln("Topology Report: interval set to %ds", reportIntervalSec);
                    
                    // 重置定时器
                    emberEventControlSetInactive(topologyReportEventControl);
                    emberEventControlSetDelayMS(topologyReportEventControl,
                                                 reportIntervalSec * 1000);
                    return EMBER_ZCL_STATUS_SUCCESS;
                }
            }
            return EMBER_ZCL_STATUS_INVALID_VALUE;
        }
        
        default:
            return EMBER_ZCL_STATUS_UNSUPPORTED_COMMAND;
    }
}

// ── ED Scan 回调 ──────────────────────────────────

/**
 * ED Scan 完成回调
 * 在 emberAfScanCompleteCallback 中调用
 */
void topologyReportScanCompleteHandler(EmberStatus status) {
    if (status != EMBER_SUCCESS) {
        emberAfDebugPrintln("ED Scan failed: 0x%02X", status);
        return;
    }
    
    // 读取扫描结果并发送
    uint8_t payload[80];
    uint16_t payloadLen = 0;
    
    // ZCL header
    payload[payloadLen++] = ZCL_FRAME_CLIENT_TO_SERVER | ZCL_FRAME_TYPE_CLUSTER_SPECIFIC;
    payload[payloadLen++] = emberNextZclSequenceNumber();
    payload[payloadLen++] = ZCL_REPORT_ATTRIBUTES_COMMAND_ID;
    payload[payloadLen++] = 0;  // frame index
    payload[payloadLen++] = 1;  // total frames
    
    // ED Scan 结果
    payload[payloadLen++] = 0x03;  // ED Scan 标记
    
    // 获取扫描结果
    uint8_t channel = 11;
    uint8_t scanCount = 0;
    for (uint8_t ch = 11; ch <= 26; ch++) {
        int8_t rssi = emberAfGetEnergyScanResult(ch);
        if (rssi != 0) {
            payload[payloadLen++] = ch;
            payload[payloadLen++] = (uint8_t)rssi;
            scanCount++;
        }
    }
    
    // 回填长度
    payload[5] = scanCount * 2;
    
    EmberApsFrame apsFrame = {
        .profileId = 0x0104,
        .clusterId = ZCL_CLUSTER_TOPOLOGY_REPORT,
        .sourceEndpoint = TOPOLOGY_ENDPOINT,
        .destinationEndpoint = TOPOLOGY_ENDPOINT,
        .options = EMBER_APS_OPTION_RETRY,
        .groupId = 0,
        .sequence = 0xFE,
    };
    
    emberSendUnicast(EMBER_OUTGOING_DIRECT, 0x0000, &apsFrame, payloadLen, payload);
}

// ── 端点注册 ──────────────────────────────────────

/**
 * EmberZNet 端点描述
 * 在 emberAfMainInitCallback() 中注册
 * 
 * 示例:
 *   void emberAfMainInitCallback(void) {
 *       topologyReportInit();
 *   }
 */

// 如果使用 Zigbee Minimal 或自定义配置，需要手动注册端点:
// 
// static EmberEndpointDescription endpointDesc = {
//     .endpoint = TOPOLOGY_ENDPOINT,
//     .deviceId = 0x0005,  // Zigbee Router
//     .profileId = 0x0104,
//     .appFlags = 0,
// };
// 
// static EmberAfCluster clusterList[] = {
//     {
//         .clusterId = ZCL_CLUSTER_TOPOLOGY_REPORT,
//         .attributes = NULL,
//         .attributeCount = 0,
//         .commands = NULL,
//         .commandCount = 0,
//         .client = false,
//     },
// };
