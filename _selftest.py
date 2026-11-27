import importlib.util
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from collectors import base as cbase

slug = sys.argv[1]
date = sys.argv[2] if len(sys.argv) > 2 else "2026-12-16"
spec = importlib.util.spec_from_file_location("probe_" + slug, os.path.join(HERE, "collectors", slug + ".py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
    cfg = json.load(f)
route = dict(cfg.get("routes", [{}])[0])
route.setdefault("from_city", route.get("from"))
route.setdefault("to_city", route.get("to"))
import cities as _cities
route.setdefault("from_codes", _cities.resolve(route.get("from", ""), route.get("from_code")))
route.setdefault("to_codes", _cities.resolve(route.get("to", ""), route.get("to_code")))
offers = mod.fetch(date, route)
print("raw offers:", len(offers))
ok = []
for o in offers:
    try:
        ok.append(cbase.validate(dict(o, platform=o.get("platform") or mod.PLATFORM)))
    except Exception as exc:
        print("INVALID:", exc)
print("%-10s %-8s %s-%s %-9s->%-9s direct 标价   含税   行李" % ("platform", "flight", "dep", "arr", "from", "to"))
for o in sorted(ok, key=lambda x: x["price"]):
    eff = cbase.total_price(o, cfg["fees"])
    print("%-10s %-8s %s-%s %-9s->%-9s %-6s %-7s %-6d %s" % (
        o["platform"], o["flight_no"], o["dep_time"], o["arr_time"],
        o["from_airport"], o["to_airport"], "yes" if o["stops"] == 0 else "NO",
        o.get("price_base") or "-", eff, o.get("baggage") or ""))
print("valid:", len(ok), "direct:", sum(1 for o in ok if o["stops"] == 0))
