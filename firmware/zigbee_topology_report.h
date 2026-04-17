/**
 * Zigbee Router 邻居表/路由表主动上报 - 自定义 ZCL Cluster
 * 
 * 平台: Silicon Labs EFR32MG21 + EmberZNet
 * 用途: 让 Router 定期上报邻居表和路由表到 Coordinator
 *       配合 zigbee-topology-tool 实现全网拓扑可视化
 * 
 * 集成方式: 将此文件加入 EmberZNet 项目，在端点初始化时注册 cluster
 */

#ifndef ZIGBEE_TOPOLOGY_REPORT_H
#define ZIGBEE_TOPOLOGY_REPORT_H

// ── Cluster 定义 ──────────────────────────────────
// 使用厂商自定义 cluster ID 范围: 0xFC00-0xFCFF
#define ZCL_CLUSTER_TOPOLOGY_REPORT    0xFC00

// ── Attribute IDs ─────────────────────────────────
#define ATTR_NEIGHBOR_TABLE_SUMMARY    0x0000  // 邻居表摘要
#define ATTR_ROUTING_TABLE_SUMMARY     0x0001  // 路由表摘要
#define ATTR_PARENT_EUI64              0x0002  // 父节点 EUI64
#define ATTR_ED_SCAN_RESULT            0x0003  // 信道能量检测值
#define ATTR_UPTIME_SECONDS           0x0004  // 运行时间(秒)
#define ATTR_FIRMWARE_VERSION         0x0005  // 固件版本
#define ATTR_REPORT_INTERVAL_SEC      0x0010  // 上报间隔(秒), 可写

// ── 命令 IDs ──────────────────────────────────────
#define CMD_REQUEST_FULL_REPORT       0x00    // 请求完整上报
#define CMD_TRIGGER_ED_SCAN           0x01    // 触发 ED Scan
#define CMD_SET_REPORT_INTERVAL      0x02    // 设置上报间隔

// ── 默认配置 ──────────────────────────────────────
#define DEFAULT_REPORT_INTERVAL_SEC   60      // 默认60秒上报一次
#define MAX_NEIGHBOR_ENTRIES          32      // 最大邻居条目
#define MAX_ROUTING_ENTRIES           16      // 最大路由条目
#define TOPOLOGY_ENDPOINT            2       // 使用端点2

#endif // ZIGBEE_TOPOLOGY_REPORT_H
