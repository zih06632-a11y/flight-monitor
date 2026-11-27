"""One monitoring cycle: collect -> normalize -> filter -> store -> evaluate -> report -> notify.

Works for any route:  python run_cycle.py --from 上海 --to 海口 --date 2026-12-16
or for everything listed in config.json route(s):
  python run_cycle.py
"""
import argparse
import importlib.util
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cities  # noqa: E402
import store  # noqa: E402
from collectors import base as cbase  # noqa: E402

COL_DIR = os.path.join(HERE, "collectors")
REPORT_DIR = os.path.join(HERE, "report")


def load_config():
    with open(os.path.join(HERE, "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def discover_collectors(disabled=()):
    mods = []
    for fname in sorted(os.listdir(COL_DIR)):
        if not fname.endswith(".py") or fname in ("__init__.py", "base.py") or fname.startswith("_"):
            continue
        path = os.path.join(COL_DIR, fname)
        spec = importlib.util.spec_from_file_location("flight_collectors." + fname[:-3], path)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:
            print("[load-fail] %s: %s" % (fname, exc))
            continue
        platform = getattr(mod, "PLATFORM", None)
        if hasattr(mod, "fetch") and platform:
            if platform in disabled:
                print("[skip] %s: disabled in config.json (disabled_platforms)" % platform)
                continue
            mods.append((platform, mod))
    return mods


def run_with_timeout(fn, args, timeout):
    box = queue.Queue()

    def target():
        try:
            box.put(("ok", fn(*args)))
        except Exception:
            box.put(("err", traceback.format_exc(limit=3)))

    threading.Thread(target=target, daemon=True).start()
    try:
        kind, payload = box.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError("collector exceeded %ss" % timeout)
    if kind == "err":
        raise RuntimeError(payload)
    return payload


def hhmm(text):
    try:
        parts = str(text).strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return None


def build_routes(cfg, args):
    if args.frm or args.to:
        if not (args.frm and args.to and args.date):
            raise SystemExit("--from/--to require --date as well")
        raw = [{"from": args.frm, "to": args.to, "date": args.date,
                "from_code": args.from_code, "to_code": args.to_code}]
    else:
        raw = list(cfg.get("routes") or ([cfg["route"]] if "route" in cfg else []))
        if args.date:
            for r in raw:
                r["date"] = args.date
    if not raw:
        raise SystemExit("no route configured: pass --from/--to/--date or add routes[] to config.json")

    routes = []
    for r in raw:
        try:
            from_codes = cities.resolve(r.get("from", ""), r.get("from_code"))
            to_codes = cities.resolve(r.get("to", ""), r.get("to_code"))
        except KeyError as exc:
            raise SystemExit(str(exc).strip('"'))
        routes.append({
            "from": r.get("from") or from_codes[0],
            "to": r.get("to") or to_codes[0],
            "date": str(r["date"]),
            "from_codes": from_codes,
            "to_codes": to_codes,
            "key": "%s-%s" % (from_codes[0], to_codes[0]),
        })
    return routes


def reject_reasons(offer, cfg):
    """Empty list = bookable under the stated preferences."""
    reasons = []
    f = cfg["filters"]
    if f.get("require_direct", True) and int(offer["stops"]) != 0:
        reasons.append("经停%s次" % offer["stops"])
    lo, hi = f["dep_time_window"]
    t = hhmm(offer.get("dep_time", ""))
    if t is None:
        reasons.append("起飞时间无法解析")
    elif not (hhmm(lo) <= t <= hhmm(hi)):
        reasons.append("起飞 %s 不在可接受时段 %s-%s" % (offer["dep_time"], lo, hi))
    if f.get("drop_flights_without_checked_baggage") and "无免费" in (offer.get("baggage") or ""):
        reasons.append("无免费托运行李")
    dep, arr = hhmm(offer.get("dep_time", "")), hhmm(offer.get("arr_time", ""))
    if dep is not None and arr is not None and arr < dep:
        offer["cross_midnight"] = True
        if f.get("drop_cross_midnight"):
            reasons.append("跨天到达（%s）" % offer["arr_time"])
    return reasons


def fake_offers():
    return [
        {"platform": "selftest", "flight_no": "MU5305", "airline": "东方航空", "dep_time": "07:35",
         "arr_time": "10:50", "from_airport": "虹桥T2", "to_airport": "美兰T1",
         "price": 620, "price_base": 550, "fees_included": True, "baggage": "含20kg托运",
         "stops": 0, "url": "https://example.invalid/a", "note": "selftest"},
        {"platform": "selftest", "flight_no": "HU7602", "airline": "海南航空", "dep_time": "12:20",
         "arr_time": "15:40", "from_airport": "浦东T1", "to_airport": "美兰T1",
         "price": 545, "price_base": 545, "fees_included": False, "baggage": "含20kg托运",
         "stops": 0, "url": "https://example.invalid/b", "note": "selftest 标价不含税"},
        {"platform": "selftest", "flight_no": "9C8801", "airline": "春秋航空", "dep_time": "05:40",
         "arr_time": "09:10", "from_airport": "浦东T2", "to_airport": "美兰T1",
         "price": 399, "baggage": "无免费托运",
         "stops": 0, "url": "https://example.invalid/c", "note": "selftest 阴间时段"},
        {"platform": "selftest", "flight_no": "CZ3510", "airline": "南方航空", "dep_time": "09:10",
         "arr_time": "14:20", "from_airport": "虹桥T2", "to_airport": "美兰T1",
         "price": 480, "stops": 1, "url": "https://example.invalid/d", "note": "selftest 经停"},
    ]


def derive_fee(offers):
    """Observed all-in minus fare, from collectors that report real taxes.

    Airport construction (50) and fuel surcharge are uniform per segment in mainland
    China, so one platform's real taxes are the correct estimate for the whole route.
    """
    seen = []
    for o in offers:
        pb = o.get("price_base")
        if o.get("fees_included", True) and isinstance(pb, int) and pb > 0:
            delta = int(o["price"]) - pb
            if 0 < delta < 500:
                seen.append(delta)
    if not seen:
        return None
    seen.sort()
    return seen[len(seen) // 2]


def evaluate(rows, base, cfg):
    if not rows:
        return None, []
    offer, eff = min(rows, key=lambda r: r[1])
    rule = cfg["rule"]
    reasons = []
    if base["count"] >= 12:
        if base["min"] is not None and eff <= base["min"] - rule["new_low_margin"]:
            reasons.append("新低（近%s天最低 ¥%s）" % (rule["baseline_days"], base["min"]))
        elif base["p10"] is not None and eff <= base["p10"]:
            reasons.append("低于历史 p10（¥%s）" % base["p10"])
    cap = rule.get("hard_cap_cny")
    if cap and eff <= cap:
        reasons.append("达到硬上限 ¥%s" % cap)
    return (offer, eff), reasons


def route_section(route, rows, excluded, base, best, reasons, failures, cfg, links=None):
    lo, hi = cfg["filters"]["dep_time_window"]
    lines = [
        "## %s → %s  %s  `%s`" % (route["from"], route["to"], route["date"], route["key"]),
        "",
        "机场三字码：%s → %s" % ("/".join(route["from_codes"]), "/".join(route["to_codes"])),
        "筛选：直飞 + 起飞 %s–%s；口径：含机建+燃油，不含保险/行李/选座" % (lo, hi),
        "历史基线（%s 样本）：最低 ¥%s / p10 ¥%s / 中位 ¥%s" % (
            base["count"], base["min"], base["p10"], base["median"]),
        "",
    ]
    if best:
        offer, eff = best
        lines.append("**最低可订：¥%s（含税） %s %s-%s %s→%s**" % (
            eff, offer["flight_no"], offer["dep_time"], offer["arr_time"],
            offer["from_airport"], offer["to_airport"]))
        if offer.get("price_base") and int(offer["price_base"]) != eff:
            lines.append("（页面标价 ¥%s，税费已补算）" % offer["price_base"])
        if reasons:
            lines.append("触发：" + "；".join(reasons))
    elif rows:
        lines.append("**本轮没有符合筛选条件的航班。**")
    if failures:
        lines += ["", "采集失败："] + ["- %s: %s" % (p, e.strip().splitlines()[-1][:150]) for p, e in failures]
    if rows:
        lines += ["", "| 平台 | 航班 | 起飞-到达 | 机场 | 标价 | 含税总价 | 行李 | 备注 |",
                  "|---|---|---|---|---|---|---|---|"]
        for offer, eff in sorted(rows, key=lambda r: r[1]):
            pb = offer.get("price_base")
            base_txt = "—" if (pb is None or int(pb) == eff) else "¥%s" % pb
            lines.append("| %s | %s | %s-%s | %s→%s | %s | ¥%s | %s | %s |" % (
                offer["platform"], offer["flight_no"], offer["dep_time"], offer["arr_time"],
                offer["from_airport"], offer["to_airport"], base_txt, eff,
                offer.get("baggage") or "—", offer.get("note", "")))
    if excluded:
        lines += ["", "已剔除：", "", "| 平台 | 航班 | 起飞 | 含税总价 | 原因 |", "|---|---|---|---|---|"]
        for offer, eff, reason in sorted(excluded, key=lambda r: r[1]):
            lines.append("| %s | %s | %s | ¥%s | %s |" % (
                offer["platform"], offer["flight_no"], offer["dep_time"], eff, reason))
    if links:
        lines += ["", "### 人工核价链接（自动化抓不到的渠道）", ""]
        for name, reason, url in links:
            lines.append("- **%s** — %s（人类浏览器可正常打开）: %s" % (name, reason, url))
    lines.append("")
    return lines


def notify(title, message):
    script = os.path.join(HERE, "notify.ps1")
    if os.name != "nt" or not os.path.exists(script):
        print("[alert] %s | %s" % (title, message))
        return
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script,
                        "-Title", title, "-Message", message], timeout=30, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        print("[notify-fail] %s" % exc)


def main():
    ap = argparse.ArgumentParser(description="Flight price monitor (all-in price, direct only)")
    ap.add_argument("--from", dest="frm", help="departure city, e.g. 上海")
    ap.add_argument("--to", dest="to", help="arrival city, e.g. 海口")
    ap.add_argument("--date", help="departure date YYYY-MM-DD (applies to every route)")
    ap.add_argument("--from-code", dest="from_code", help="IATA code if the city is unknown")
    ap.add_argument("--to-code", dest="to_code", help="IATA code if the city is unknown")
    ap.add_argument("--fake", action="store_true", help="selftest with built-in offers")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--timeout", type=int, default=110)
    args = ap.parse_args()

    cfg = load_config()
    routes = build_routes(cfg, args)
    fees_cfg = cfg["fees"]
    ts = int(time.time())

    if args.fake:
        collectors = []
    else:
        collectors = discover_collectors(set(cfg.get("disabled_platforms") or []))
        print("collectors: %s" % [p for p, _ in collectors])

    sections, alerts, summary = [], [], []

    for route in routes:
        base = store.baseline(route["key"], route["date"], cfg["rule"]["baseline_days"], now=ts)
        offers, failures = [], []

        if args.fake:
            # selftest must never touch the production database: it would poison the baseline
            offers = [cbase.validate(o) for o in fake_offers()]
        else:
            payload = {"from_city": route["from"], "to_city": route["to"],
                       "from_codes": route["from_codes"], "to_codes": route["to_codes"],
                       "date": route["date"]}
            for platform, mod in collectors:
                try:
                    result = run_with_timeout(mod.fetch, (route["date"], payload), args.timeout)
                    good = [cbase.validate(dict(o, platform=o.get("platform") or platform))
                            for o in result]
                    offers.extend(good)
                    store.record_run(ts, route["key"], platform, True, len(good))
                    print("[ok]   %s %s: %d offers" % (route["key"], platform, len(good)))
                except Exception as exc:
                    failures.append((platform, str(exc)))
                    store.record_run(ts, route["key"], platform, False, 0, str(exc))
                    print("[fail] %s %s: %s" % (route["key"], platform, str(exc).splitlines()[0][:130]))

        observed_fee = derive_fee(offers)
        eff_fees = dict(fees_cfg)
        if observed_fee:
            eff_fees["fallback_total_cny"] = observed_fee
            print("[fee]    %s: 实测税费 ¥%s（用于补算只给票面价的平台）" % (route["key"], observed_fee))

        rows, excluded = [], []
        for offer in offers:
            eff = cbase.total_price(offer, eff_fees)
            reasons = reject_reasons(offer, cfg)
            tags = []
            if not offer.get("fees_included", True):
                tags.append("税费按%s补算 (¥%s)" % ("实测" if observed_fee else "默认估算", eff_fees["fallback_total_cny"]))
            if offer.get("cross_midnight"):
                tags.append("跨天到达")
            if tags:
                offer["note"] = (offer.get("note", "") + "｜" + "｜".join(tags)).lstrip("｜")
            if reasons:
                excluded.append((offer, eff, "；".join(reasons)))
            else:
                rows.append((offer, eff))
        if rows and not args.fake:
            store.add_offers(ts, route["key"], route["date"], rows)

        links = []
        for item in cfg.get("manual_links", []):
            try:
                url = item["url"].format(from_code=route["from_codes"][0],
                                          to_code=route["to_codes"][0],
                                          from_code_lc=route["from_codes"][0].lower(),
                                          to_code_lc=route["to_codes"][0].lower(),
                                          date=route["date"],
                                          from_name=route["from"], to_name=route["to"])
            except Exception:
                continue
            links.append((item.get("name", "?"), item.get("reason", ""), url))

        best, reasons = evaluate(rows, base, cfg)
        sections += route_section(route, rows, excluded, base, best, reasons, failures, cfg, links)
        summary.append("%s %s→%s offers=%d eligible=%d excluded=%d failures=%d best=%s" % (
            route["key"], route["from"], route["to"], len(offers), len(rows), len(excluded),
            len(failures), ("¥%s" % best[1]) if best else "-"))

        if best and reasons and not args.no_notify and not args.fake:
            offer, eff = best
            key = "%s:%s:%s:%s" % (route["key"], route["date"], offer["flight_no"], eff)
            if store.alerted_recently(key, cfg["rule"]["notify_cooldown_hours"], ts):
                print("alert suppressed by cooldown")
            else:
                message = "¥%s 含税 %s %s-%s %s→%s｜%s｜不含保险与托运行李" % (
                    eff, offer["flight_no"], offer["dep_time"], offer["arr_time"],
                    offer["from_airport"], offer["to_airport"], "；".join(reasons))
                title = "机票提醒 %s→%s %s" % (route["from"], route["to"], route["date"])
                notify(title, message)
                store.mark_alert(ts, key, eff, message)
                alerts.append(message)

    os.makedirs(REPORT_DIR, exist_ok=True)
    head = ["# 机票监测报告", "", "生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            "监测航线数：%s" % len(routes), ""]
    path = os.path.join(REPORT_DIR, "latest.md")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(head + sections) + "\n")
    print("report: %s" % path)
    for line in summary:
        print(line)
    for a in alerts:
        print("alert sent: " + a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
