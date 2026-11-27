# flight-monitor

上海 → 海口 机票价格监测 + 可复用订票 SOP。抓多个平台、按**含税总价**比价、在价格合适时弹 Windows 通知。

## 它解决什么

人工比价有三个坑：列表页标价不含机建燃油、便宜票常在凌晨起飞、各平台价格来回跳。这个工具把三件事固化成规则：

1. **只比含税总价**：票价 + 机场建设费 + 燃油附加费，保险 / 付费行李 / 选座一律不算（默认不买）。
2. **剔除阴间时段**：默认只保留 07:00–22:00 起飞的航班（可配置）。
3. **3 天基线后自动判定**：跌破近 3 天最低价、低于 p10、或命中你设的硬上限，才发通知——不会被常态高价骚扰。

## 快速开始

```bash
# 依赖：Python 3.9+，仅标准库（无需 pip install）
python run_cycle.py            # 跑一轮：采集 → 比价 → 生成 report/latest.md
python run_cycle.py --fake     # 用内置样例验证管线（不联网）
python _selftest.py <slug>     # 单独验证某个采集器
```

结果看 `report/latest.md`：可订报价表 + 已剔除表（写明剔除原因）+ 采集失败表。

Windows 每 2 小时自动跑一次：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File register_task.ps1
```

## 目录结构

```
flight-monitor/
├── run_cycle.py           # 主流程：采集 → 归一化 → 过滤 → 入库 → 判定 → 报告 → 通知
├── store.py               # SQLite 存储与基线统计（最低价 / p10 / 中位数）
├── notify.ps1             # Windows toast 通知 + data/alerts.log 落盘
├── config.json            # 航线、筛选口径、告警规则
├── collectors/
│   ├── base.py            # 采集器契约（含税总价 / 字段校验 / 总价补算）
│   └── <platform>.py      # 各平台采集器，框架自动发现
├── _selftest.py           # 单个采集器的验收脚本
├── 订票流程.md            # 可复用订票 SOP（含学生优惠认证路径）
├── data/                  # prices.db / alerts.log（已 gitignore）
└── report/                # latest.md（已 gitignore）
```

## 配置（`config.json`）

| 键 | 说明 |
|---|---|
| `route` | 出发/到达城市与三字码、出发日期 |
| `filters.dep_time_window` | 可接受的起飞时间窗，默认 `[\"07:00\", \"22:00\"]` |
| `filters.require_direct` | 是否只要直飞，默认 `true` |
| `fees.fallback_total_cny` | 采集器拿不到税费时的补算值（机建 50 + 燃油 20） |
| `rule.new_low_margin` | 比近 3 天最低价再低多少元才提醒 |
| `rule.hard_cap_cny` | 绝对上限，低于它就提醒（`null` 表示不启用） |
| `rule.notify_cooldown_hours` | 同一价格多久内不重复提醒 |

换航线只改 `route`；加平台只往 `collectors/` 丢一个实现契约的 `.py`。

## 采集器契约

见 `collectors/base.py`。要点：

- 只暴露 `PLATFORM` 与 `fetch(date, route) -> list[dict]`；
- `price` 必须是**含机建 + 燃油的实付总价**；页面只给标价时，填 `price_base` 并设 `fees_included=False`；
- 无人值守运行，不使用已登录的浏览器 profile，不依赖人工点击；
- 单次 90 秒内结束，失败直接 raise（框架会记录并继续跑其他平台）。

## 平台支持状态

> 见下方「已知限制」。抓不到的平台不会静默失败：`report/latest.md` 的「采集失败」小节会列出平台与错误尾部。

| 平台 | 采集器 | 状态 |
|---|---|---|
| 同程旅行 | `collectors/tongcheng.py` | 见 release notes |
| 去哪儿 | `collectors/qunar.py` | 见 release notes |
| 携程 | `collectors/ctrip.py` | 见 release notes |
| 航司官网 | `collectors/airlines.py` | 见 release notes |

## 已知限制

- OTA 有反爬（携程的 whaleguard 会直接拦截脚本请求），抓不到时请以深链人工核价；
- 列表页价格与结算页可能不一致，下单前务必在结算页复核含税总价；
- 价格数据仅用于个人比价，请遵守各平台服务条款，不要高频请求。

## 免责声明

本项目仅供个人学习与比价使用，不提供任何购票、代下单或支付能力。作者不对价格准确性与可用性作任何保证。

