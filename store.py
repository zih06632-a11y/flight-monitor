"""SQLite storage + baseline statistics for the flight monitor."""
import os
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "data", "prices.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS offers (
  ts INTEGER NOT NULL, day TEXT NOT NULL, platform TEXT NOT NULL,
  flight_no TEXT NOT NULL, airline TEXT, dep_time TEXT, arr_time TEXT,
  from_airport TEXT, to_airport TEXT, price INTEGER NOT NULL, stops INTEGER NOT NULL,
  url TEXT, note TEXT, price_base INTEGER, baggage TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  ts INTEGER NOT NULL, platform TEXT NOT NULL, ok INTEGER NOT NULL,
  count INTEGER NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS alerts (
  ts INTEGER NOT NULL, key TEXT NOT NULL, price INTEGER NOT NULL, message TEXT
);
CREATE INDEX IF NOT EXISTS idx_offers_ts ON offers(ts);
CREATE INDEX IF NOT EXISTS idx_offers_day ON offers(day, platform);
"""


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    cols = [r[1] for r in con.execute("PRAGMA table_info(offers)")]
    for col, decl in (("price_base", "INTEGER"), ("baggage", "TEXT")):
        if col not in cols:
            con.execute("ALTER TABLE offers ADD COLUMN %s %s" % (col, decl))
    con.commit()
    return con


def add_offers(ts, day, rows):
    """rows: [(offer, effective_price)]"""
    con = connect()
    con.executemany(
        "INSERT INTO offers (ts, day, platform, flight_no, airline, dep_time, arr_time,"
        " from_airport, to_airport, price, stops, url, note, price_base, baggage)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(ts, day, o["platform"], o["flight_no"], o.get("airline", ""), o.get("dep_time", ""),
          o.get("arr_time", ""), o.get("from_airport", ""), o.get("to_airport", ""),
          int(eff), int(o["stops"]), o.get("url", ""), o.get("note", ""),
          o.get("price_base"), o.get("baggage", "")) for o, eff in rows],
    )
    con.commit()
    con.close()


def record_run(ts, platform, ok, count, error=""):
    con = connect()
    con.execute("INSERT INTO runs (ts, platform, ok, count, error) VALUES (?,?,?,?,?)",
                (ts, platform, 1 if ok else 0, count, (error or "")[:400]))
    con.commit()
    con.close()


def baseline(day, days=3, now=None):
    """All-in direct-flight samples for this day, collected in the last N days."""
    con = connect()
    cutoff = (now or 0) - days * 86400
    rows = con.execute(
        "SELECT price FROM offers WHERE day=? AND stops=0 AND ts>=? ORDER BY price", (day, cutoff)
    ).fetchall()
    con.close()
    prices = [r[0] for r in rows]
    if not prices:
        return {"count": 0, "min": None, "p10": None, "median": None}
    return {
        "count": len(prices),
        "min": prices[0],
        "p10": prices[max(0, int(len(prices) * 0.10) - 1)],
        "median": prices[len(prices) // 2],
    }


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
