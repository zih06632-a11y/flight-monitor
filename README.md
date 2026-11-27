# flight-monitor

任意航线、任意日期的机票价格监测：按**含税总价**比价，剔除阴间时段，价格合适时弹通知。

不是某个固定航线的脚本——换城市、换日期、同时盯多条线都只改一个参数。

## 30 秒上手

```bash
# 一条命令盯任意航线（无需 pip install，只用 Python 标准库）
python run_cycle.py --from 上海 --to 海口 --date 2026-12-16

# 跑 config.json 里配置的所有航线
python run_cycle.py

# 不联网自检管线
python run_cycle.py --fake
```

结果在 `report/latest.md`：每条航线一张可订报价表 + 已剔除明细（写明原因）+ 采集失败明细 + 人工核价深链。

## 三个硬口径（默认值，都可改）

| 口径 | 默认 | 为什么 |
|---|---|---|
| 只比含税总价 | 票价 + 机建(¥50) + 燃油 | 平台列表页标价不含税，光看标价会比出假便宜 |
| 剔除阴间时段 | 只保留 07:00–22:00 起飞 | 红眼航班省的钱不够补打车和酒店 |
| 默认不买保险/行李/选座 | 全部不计 | 这才是真实自付价；含行李的票价只在「行李」列标注，不参与比价 |

再加上**3 天基线**：样本够 12 个后，只有跌破近 3 天最低价、或低于 p10、或命中你设的硬上限才提醒，不会被常态高价骚扰。

## 城市怎么填

内置 46 个国内城市（见 `cities.py`），直接写城市名，自动展开多机场：

```bash
python run_cycle.py --from 上海 --to 海口 --date 2026-12-16   # 上海 → SHA/PVG
python run_cycle.py --from 北京 --to 三亚 --date 2027-01-20   # 北京 → PEK/PKX
```

不在表里的城市，直接给三字码：

```bash
python run_cycle.py --from 上海 --to 喀什 --from-code SHA --to-code KHG --date 2026-12-16
```

写错城市名会直接报错并列出已知城市，不会静默查错机场。

## 同时盯多条航线

```json
"routes": [
  { "from": "上海", "to": "海口", "date": "2026-12-16" },
  { "from": "上海", "to": "三亚", "date": "2026-12-18" }
]
```

数据库按航线隔离（`offers.route`），基线各算各的，互不污染。

## 配置说明（`config.json`）

| 键 | 作用 |
|---|---|
| `routes[]` | 要盯的航线列表 |
| `filters.dep_time_window` | 可接受起飞时段，默认 `["07:00","22:00"]` |
| `filters.require_direct` | 是否只要直飞，默认 `true` |
| `filters.drop_flights_without_checked_baggage` | 是否直接剔除无免费行李的低成本航班，默认 `false` |
| `fees.fallback_total_cny` | 兜底补算值。**运行期会自校准**：只要本轮有任何平台给出真实税费（如南航官网），就用它的中位数补算只给票面价的平台，保证同一航线跨平台可比 |
| `rule.new_low_margin` | 比近 3 天最低价再低多少元才提醒 |
| `rule.hard_cap_cny` | 绝对上限，低于它就提醒（`null` = 不启用） |
| `rule.notify_cooldown_hours` | 同价多久内不重复提醒 |
| `manual_links[]` | 抓不到的平台的深链模板，渲染进报告供人工核价 |

## 加一个采集平台

往 `collectors/` 放一个文件，实现契约（见 `collectors/base.py`），框架自动发现，不用注册：

```python
PLATFORM = "example"

def fetch(date, route):
    # route = {from_city, to_city, from_codes, to_codes, date}
    return [{
        "platform": PLATFORM, "flight_no": "MU5305", "airline": "东方航空",
        "dep_time": "07:35", "arr_time": "10:50",
        "from_airport": "虹桥T2", "to_airport": "美兰T1",
        "price": 620,            # 含机建+燃油的实付总价
        "price_base": 550,       # 页面标价（可选，用于展示）
        "stops": 0, "url": "https://...", "baggage": "含20kg托运",
    }]
```

单测一个采集器：`python _selftest.py example 2026-12-16`

## 平台支持状态

| 渠道 | 采集器 | 状态 |
|---|---|---|
| 同程旅行 | `collectors/tongcheng.py` | **可用**：匿名 JSON 接口，单次 ~0.8s，返回票面价（接口无税费字段，由运行期自校准补算） |
| 南方航空官网 | `collectors/airlines.py` | **可用**：过会话 + 阿里云 WAF 挑战，返回真实含税价（票价 + 机建 + 燃油） |
| 去哪儿 | `collectors/qunar.py` | **需登录**：搜索跳 `user.qunar.com` 登录墙，匿名一律空结果；默认在 `disabled_platforms` 中禁用，报告给深链 |
| 携程 | — | **反爬封死**：航班 API 全路径族 432/430（whaleguard），真实浏览器同样被拦；报告给深链 |
| 东航 / 吉祥 / 海航官网 | — | 未攻克：东航 CDN 风控 + 设备指纹，吉祥签名链路，海航老式 IBE 多步表单 |

`disabled_platforms` 是显式开关：某平台需要登录或被封时，代码留着、本轮跳过，不会每个周期刷一堆失败。

抓不到就是抓不到：框架不会用假数据凑数，失败会逐条列进报告。

## 自动跑（Windows）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File register_task.ps1
```

注册每 2 小时一次的计划任务，结果写 `report/latest.md`，命中规则弹 Windows 通知并记 `data/alerts.log`。

非 Windows：直接 cron 调 `python run_cycle.py`，通知会退化成控制台输出（`notify()` 里做了判断）。

## 目录结构

```
flight-monitor/
├── run_cycle.py        # 主流程：采集 → 归一化 → 过滤 → 入库 → 判定 → 报告 → 通知
├── cities.py           # 城市名 → 三字码（46 城，可自行扩充）
├── store.py            # SQLite，按航线+日期隔离的报价与基线
├── notify.ps1          # Windows toast + data/alerts.log
├── config.json         # 航线、筛选口径、告警规则、深链模板
├── collectors/
│   ├── base.py         # 契约：字段校验 + 含税总价补算
│   └── *.py            # 各平台采集器
├── _selftest.py        # 单采集器验收
├── 订票流程.md          # 订票 SOP（含学生优惠认证路径）
├── data/ report/       # 运行时产物，已 gitignore
```

## 已知限制

- OTA 反爬普遍（携程最狠），自动化抓不到时以深链人工核价为准；
- 列表价与结算价可能不一致，下单前必须在结算页复核含税总价；
- 价格数据仅供个人比价，请遵守各平台服务条款，别高频请求；
- 本项目不提供购票、代下单或支付能力。

## License

MIT

