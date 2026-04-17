# MG21 硬件验证清单

## 前提条件

- [ ] MG21 Coordinator 已刷 EmberZNet 固件 (非 Zephyr/open-thread)
- [ ] Coordinator 已组网 (有设备入过网)
- [ ] USB 转串口连接正常
- [ ] 串口未被占用 (停止 Z2M / HA / 串口助手)
- [ ] Python 3.9+ 已安装

## 验证步骤

### Step 1: 环境准备

```bash
cd zigbee-topology-tool
pip3 install -r backend/requirements.txt

# 确认串口设备
# Linux:
ls /dev/ttyUSB* /dev/ttyACM*
# macOS:
ls /dev/cu.usbserial*
# Windows: 设备管理器 → 端口
```

### Step 2: 运行验证脚本

```bash
python3 test_mg21_verification.py /dev/ttyUSB0
```

脚本自动执行 8 个验证步骤，输出 JSON 报告。

### Step 3: 根据结果判断

| 结果 | 说明 | 下一步 |
|---|---|---|
| 全部 ✅ PASS | 连接正常，数据完整 | 运行 `python3 backend/main.py --port /dev/ttyUSB0` |
| 串口连接 ❌ | 权限/端口/波特率问题 | 见下方排障 |
| 版本协商 ⚠️ | bellows 和 EZSP 版本不匹配 | 调整 bellows 版本 |
| 邻居表空 ⚠️ | 未组网或没有邻居 | 先让设备入网 |
| API 不兼容 ❌ | bellows API 与固件版本差异 | 升级 bellows 或降级固件 |

### Step 4: 运行完整工具

验证通过后：

```bash
cd backend
python3 main.py --port /dev/ttyUSB0
# 浏览器打开 http://localhost:8000
```

## 常见问题排障

### 串口权限不足

```bash
# 临时解决
sudo chmod 666 /dev/ttyUSB0

# 永久解决 (加入 dialout 组)
sudo usermod -aG dialout $USER
# 重新登录生效
```

### 串口被占用

```bash
# 查看谁在用
lsof /dev/ttyUSB0
# 或
fuser /dev/ttyUSB0

# 停掉占用进程
kill <PID>
```

### EZSP 版本不匹配

```bash
# 查看当前 bellows 版本
pip3 show bellows

# 升级到最新
pip3 install --upgrade bellows zigpy

# 如果最新版不兼容，尝试特定版本
pip3 install bellows==0.36.0
```

### 波特率不对

MG21 默认 115200，但有些固件用 57600。验证脚本会自动尝试多个波特率。

### Coordinator 未组网

如果邻居表/路由表都为空：
1. 在 Coordinator 上执行 `permit join`
2. 让至少 1 个设备入网
3. 等待 30 秒后重新运行验证

### bellows API 报错

不同版本的 EmberZNet 对应不同的 EZSP 协议版本。如果 API 调用报 `AttributeError`：
1. 查看 Simplicity Studio 中 EmberZNet 版本号
2. 查看 EZSP 协议版本 (通常在固件配置中)
3. 对应调整 bellows 版本

## 验证报告

验证脚本会生成 `mg21_verify_report.json`，请把内容发给我分析。

关键信息：
- 总体结果 (PASS/FAIL)
- 失败步骤的 detail
- EZSP 配置值
- 邻居表/路由表条目数
