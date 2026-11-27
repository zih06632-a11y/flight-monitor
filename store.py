"""SQLite storage + baseline statistics. Every row is scoped by route."""
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "data", "prices.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
  ts INTEGER NOT NULL, route TEXT NOT NULL DEFAULT '', day TEXT NOT NULL, platform TEXT NOT NULL,
  flight_no TEXT NOT NULL, airline TEXT, dep_time TEXT, arr_time TEXT,
  from_airport TEXT, to_airport TEXT, price INTEGER NOT NULL, stops INTEGER NOT NULL,
  url TEXT, note TEXT, price_base INTEGER, baggage TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  ts INTEGER NOT NULL, route TEXT NOT NULL DEFAULT '', platform TEXT NOT NULL,
  ok INTEGER NOT NULL, count INTEGER NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
  ts INTEGER NOT NULL, key TEXT NOT NULL, price INTEGER NOT NULL, message TEXT
);
CREATE TABLE IF NOT EXISTS route_fees (
  route TEXT PRIMARY KEY, ts INTEGER NOT NULL, fee INTEGER NOT NULL
);
"""

MIGRATIONS = (
    ("offers", "route", "TEXT NOT NULL DEFAULT ''"),
    ("offers", "price_base", "INTEGER"),
    ("offers", "baggage", "TEXT"),
    ("runs", "route", "TEXT NOT NULL DEFAULT ''"),
)


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    for table, col, decl in MIGRATIONS:
        cols = [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]
        if col not in cols:
            con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))
    con.execute("CREATE INDEX IF NOT EXISTS idx_offers_lookup ON offers(route, day, ts)")
    con.commit()
    return con


def add_offers(ts, route_key, day, rows):
    """rows: [(offer, effective_all_in_price)]"""
    con = connect()
    con.executemany(
        "INSERT INTO offers (ts, route, day, platform, flight_no, airline, dep_time, arr_time,"
        " from_airport, to_airport, price, stops, url, note, price_base, baggage)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(ts, route_key, day, o["platform"], o["flight_no"], o.get("airline", ""),
          o.get("dep_time", ""), o.get("arr_time", ""), o.get("from_airport", ""),
          o.get("to_airport", ""), int(eff), int(o["stops"]), o.get("url", ""),
          o.get("note", ""), o.get("price_base"), o.get("baggage", "")) for o, eff in rows],
    )
    con.commit()
    con.close()


def record_run(ts, route_key, platform, ok, count, error=""):
    con = connect()
    con.execute("INSERT INTO runs (ts, route, platform, ok, count, error) VALUES (?,?,?,?,?,?)",
                (ts, route_key, platform, 1 if ok else 0, count, (error or "")[:400]))
    con.commit()
    con.close()


def baseline(route_key, day, days=3, now=None):
    """All-in direct-flight samples for this route+day over the last N days."""
    con = connect()
    cutoff = (now or 0) - days * 86400
    rows = con.execute(
        "SELECT price, ts FROM offers WHERE route=? AND day=? AND stops=0"
        " AND platform!='selftest' AND ts>=? ORDER BY price",
        (route_key, day, cutoff)).fetchall()
    con.close()
    prices = [r[0] for r in rows]
    if not prices:
        return {"count": 0, "min": None, "p10": None, "median": None, "span_hours": 0.0}
    stamps = [r[1] for r in rows]
    return {
        "count": len(prices),
        "min": prices[0],
        "p10": prices[max(0, int(len(prices) * 0.10) - 1)],
        "median": prices[len(prices) // 2],
        "span_hours": round((max(stamps) - min(stamps)) / 3600.0, 1),
    }


def recent_failure(route_key, platform, hours, now):
    """Last failed run for this platform, if it happened inside the window."""
    con = connect()
    row = con.execute(
        "SELECT ts, error FROM runs WHERE route=? AND platform=? AND ok=0 AND ts>=?"
        " ORDER BY ts DESC LIMIT 1", (route_key, platform, now - hours * 3600)).fetchone()
    con.close()
    return row


def save_route_fee(route_key, ts, fee):
    """Remember the last taxes actually observed on this route.

    Airport construction (50) and fuel surcharge are uniform per segment, so this value
    stays valid for every platform on the route until a newer observation replaces it.
    """
    con = connect()
    con.execute("INSERT INTO route_fees (route, ts, fee) VALUES (?,?,?)"
                " ON CONFLICT(route) DO UPDATE SET ts=excluded.ts, fee=excluded.fee",
                (route_key, int(ts), int(fee)))
    con.commit()
    con.close()


def observed_fee_history(route_key, max_age_days=14, now=None):
    """Recovers the real tax amount from stored offers.

    An offer that carries both price_base (fare) and price (all-in) proves the delta, so
    history is enough to price platforms that only expose a bare fare.
    """
    con = connect()
    cutoff = (now or 0) - max_age_days * 86400
    rows = con.execute(
        "SELECT price - price_base FROM offers WHERE route=? AND price_base IS NOT NULL"
        " AND price > price_base AND ts>=? ORDER BY ts DESC LIMIT 200",
        (route_key, cutoff)).fetchall()
    con.close()
    deltas = sorted(r[0] for r in rows if 0 < r[0] < 500)
    if not deltas:
        return None
    return deltas[len(deltas) // 2]


def get_route_fee(route_key, max_age_days=14, now=None):
    con = connect()
    row = con.execute("SELECT ts, fee FROM route_fees WHERE route=?", (route_key,)).fetchone()
    con.close()
    if row:
        ts, fee = row
        if not (now and ts < now - max_age_days * 86400):
            return (int(fee), int(ts))
    observed = observed_fee_history(route_key, max_age_days, now)
    return (int(observed), 0) if observed else None


def alerted_recently(key, hours, now):
    con = connect()
    row = con.execute("SELECT ts FROM alerts WHERE key=? AND ts>=? ORDER BY ts DESC LIMIT 1",
                      (key, now - hours * 3600)).fetchone()
    con.close()
    return row is not None


def mark_alert(ts, key, price, message):
    con = connect()
    con.execute("INSERT INTO alerts (ts, key, price, message) VALUES (?,?,?,?)",
                (ts, key, int(price), message[:1000]))
    con.commit()
    con.close()
