"""One monitoring cycle: collect -> normalize -> filter -> store -> evaluate -> report -> notify."""
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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import store  # noqa: E402
from collectors import base as cbase  # noqa: E402

COL_DIR = os.path.join(HERE, "collectors")
REPORT_DIR = os.path.join(HERE, "report")


def load_config():
    with open(os.path.join(HERE, "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def discover_collectors():
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
        if hasattr(mod, "fetch") and getattr(mod, "PLATFORM", None):
            mods.append((mod.PLATFORM, mod))
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


def reject_reasons(offer, cfg):
    """Empty list = the offer is bookable under the user's stated preferences."""
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
    return reasons


def fake_offers(platform="selftest"):
    return [
        {"platform": platform, "flight_no": "MU5305", "airline": "东方航空", "dep_time": "07:35",
         "arr_time": "10:50", "from_airport": "虹桥T2", "to_airport": "美兰T1",
         "price": 620, "price_base": 550, "fees_included": True, "baggage": "含20kg托运",
         "stops": 0, "url": "https://example.invalid/a", "note": "selftest"},
        {"platform": platform, "flight_no": "HU7602", "airline": "海南航空", "dep_time": "12:20",
         "arr_time": "15:40", "from_airport": "浦东T1", "to_airport": "美兰T1",
         "price": 545, "price_base": 545, "fees_included": False, "baggage": "含20kg托运",
         "stops": 0, "url": "https://example.invalid/b", "note": "selftest 标价不含税"},
        {"platform": platform, "flight_no": "9C8801", "airline": "春秋航空", "dep_time": "05:40",
         "arr_time": "09:10", "from_airport": "浦东T2", "to_airport": "美兰T1",
         "price": 399, "baggage": "无免费托运",
         "stops": 0, "url": "https://example.invalid/c", "note": "selftest 阴间时段"},
        {"platform": platform, "flight_no": "CZ3510", "airline": "南方航空", "dep_time": "09:10",
         "arr_time": "14:20", "from_airport": "虹桥T2", "to_airport": "美兰T1",
         "price": 480, "stops": 1, "url": "https://example.invalid/d", "note": "selftest 经停"},
    ]


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


def write_report(day, route, rows, excluded, base, best, reasons, failures, cfg):
    os.makedirs(REPORT_DIR, exist_ok=True)
    lo, hi = cfg["filters"]["dep_time_window"]
    lines = [
        "# %s → %s  %s" % (route["from_city"], route["to_city"], day),
        "",
        "生成时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "价格口径：**含机建+燃油的实付总价**；默认不买保险、不买付费行李、不选座。",
        "筛选口径：直飞 + 起飞时间 %s–%s。" % (lo, hi),
        "历史基线（%s 个样本）：最低 ¥%s / p10 ¥%s / 中位 ¥%s" % (
            base["count"], base["min"], base["p10"], base["median"]),
        "",
    ]
    if best:
        offer, eff = best
        lines.append("**当前最低可订：¥%s（含税） %s %s-%s %s→%s**" % (
            eff, offer["flight_no"], offer["dep_time"], offer["arr_time"],
            offer["from_airport"], offer["to_airport"]))
        if offer.get("price_base") and int(offer["price_base"]) != eff:
            lines.append("（页面标价 ¥%s，税费差额已计入）" % offer["price_base"])
        if reasons:
            lines.append("触发原因：" + "；".join(reasons))
    elif rows:
        lines.append("**本轮没有符合筛选条件的航班。**")
    if failures:
        lines += ["", "## 采集失败", ""]
        lines += ["- %s: %s" % (p, e.strip().splitlines()[-1][:160]) for p, e in failures]
    lines += ["", "## 可订报价（含税总价升序）", "",
              "| 平台 | 航班 | 起飞-到达 | 机场 | 标价 | 含税总价 | 行李 | 备注 |",
              "|---|---|---|---|---|---|---|---|"]
    for offer, eff in sorted(rows, key=lambda r: r[1]):
        base_price = offer.get("price_base")
        if base_price is None or int(base_price) == eff:
            base_txt = "—"
        else:
            base_txt = "¥%s" % base_price
        lines.append("| %s | %s | %s-%s | %s→%s | %s | ¥%s | %s | %s |" % (
            offer["platform"], offer["flight_no"], offer["dep_time"], offer["arr_time"],
            offer["from_airport"], offer["to_airport"], base_txt, eff,
            offer.get("baggage") or "—", offer.get("note", "")))
    if excluded:
        lines += ["", "## 已剔除（不符合你的筛选口径）", "",
                  "| 平台 | 航班 | 起飞 | 含税总价 | 剔除原因 |", "|---|---|---|---|---|"]
        for offer, eff, reason in sorted(excluded, key=lambda r: r[1]):
            lines.append("| %s | %s | %s | ¥%s | %s |" % (
                offer["platform"], offer["flight_no"], offer["dep_time"], eff, reason))
    path = os.path.join(REPORT_DIR, "latest.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def notify(title, message):
    script = os.path.join(HERE, "notify.ps1")
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script,
                        "-Title", title, "-Message", message], timeout=30, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        print("[notify-fail] %s" % exc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--fake", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--timeout", type=int, default=110)
    args = ap.parse_args()

    cfg = load_config()
    route = dict(cfg["route"])
    day = args.date or route["date"]
    ts = int(time.time())
    fees_cfg = cfg["fees"]

    base = store.baseline(day, cfg["rule"]["baseline_days"], now=ts)

    offers, failures = [], []
    if args.fake:
        offers = [cbase.validate(o) for o in fake_offers()]
        store.record_run(ts, "selftest", True, len(offers))
    else:
        collectors = discover_collectors()
        print("collectors: %s" % [p for p, _ in collectors])
        for platform, mod in collectors:
            try:
                result = run_with_timeout(mod.fetch, (day, route), args.timeout)
                good = [cbase.validate(dict(o, platform=o.get("platform") or platform)) for o in result]
                offers.extend(good)
                store.record_run(ts, platform, True, len(good))
                print("[ok]   %s: %d offers" % (platform, len(good)))
            except Exception as exc:
                failures.append((platform, str(exc)))
                store.record_run(ts, platform, False, 0, str(exc))
                print("[fail] %s: %s" % (platform, str(exc).splitlines()[0][:140]))

    rows, excluded = [], []
    for offer in offers:
        eff = cbase.total_price(offer, fees_cfg)
        reasons = reject_reasons(offer, cfg)
        if reasons:
            excluded.append((offer, eff, "；".join(reasons)))
        else:
            rows.append((offer, eff))

    if rows:
        store.add_offers(ts, day, rows)

    best, reasons = evaluate(rows, base, cfg)
    report = write_report(day, route, rows, excluded, base, best, reasons, failures, cfg)
    print("report: %s" % report)

    if best and reasons and not args.no_notify:
        offer, eff = best
        key = "%s:%s:%s" % (day, offer["flight_no"], eff)
        if store.alerted_recently(key, cfg["rule"]["notify_cooldown_hours"], ts):
            print("alert suppressed by cooldown")
        else:
            message = "¥%s 含税 %s %s-%s %s→%s｜%s｜不含保险与托运行李" % (
                eff, offer["flight_no"], offer["dep_time"], offer["arr_time"],
                offer["from_airport"], offer["to_airport"], "；".join(reasons))
            notify("机票提醒 %s→%s %s" % (route["from_city"], route["to_city"], day), message)
            store.mark_alert(ts, key, eff, message)
            print("alert sent: " + message)

    print("offers=%d eligible=%d excluded=%d failures=%d base=%s"
          % (len(offers), len(rows), len(excluded), len(failures), base))
    return 0


if __name__ == "__main__":
    sys.exit(main())
