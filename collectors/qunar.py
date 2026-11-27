"""去哪儿 (qunar) collector — anonymous H5/PC flight list.

Endpoints (all discovered from the live site, not guessed):
  1. POST https://touch.qunar.com/flight/api/touchInnerList
     body {"arrCity","depCity","depDate","firstRequest","startNum","sort","_v","underageOption","r","st"}
     -> the request the touch.qunar.com SPA itself sends (captured over CDP).  Payload also carries
        "Bella" (a JS-computed signature over the field names) and "__m__" (device token); they are
        omitted here because it is a signature, not a credential we can mint offline.
  2. GET https://flight.qunar.com/touch/api/domestic/wbdFlightList?depCity=&arrCity=&depDate=&startNum=
     -> legacy touch list API, envelope {"ret":true,"code":-1,"msg":"请求成功","data":{"flights":[..],"total":N}}

AS OF 2026-11-27 both answer 200/ret:true but deliver ZERO rows for every route/date tried, including
北京->上海 for tomorrow, and the SPA itself bounces to user.qunar.com/mobile/login.jsp before it ever
renders results.  So there is nothing to normalize: fetch() raises with the diagnostics instead of
inventing offers (see QunarUnavailable).
"""

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

PLATFORM = "qunar"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TOUCH = "https://touch.qunar.com/flight/api/touchInnerList"
WBD = "https://flight.qunar.com/touch/api/domestic/wbdFlightList"
DEEP = "https://m.flight.qunar.com/ncs/page/flightlist"

TIMEOUT = 12
DEADLINE = 80


class QunarUnavailable(RuntimeError):
    pass


def _post(url, payload, referer):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/javascript")
    req.add_header("User-Agent", UA)
    req.add_header("Referer", referer)
    req.add_header("Origin", "https://touch.qunar.com")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return -1, repr(e)


def _get(url, referer):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    req.add_header("Referer", referer)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return -1, repr(e)


def _hhmm(value):
    """"07:35" / "0735" / "2026-12-16 07:35" -> "07:35"."""
    if not value:
        return ""
    s = str(value).strip()
    if " " in s:
        s = s.split(" ")[-1]
    s = s.replace(":", "")
    if len(s) >= 4 and s[:4].isdigit():
        return s[:2] + ":" + s[2:4]
    return ""


def _int(value):
    try:
        n = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _airport(name, terminal):
    name = (name or "").strip()
    terminal = str(terminal or "").strip()
    if name and terminal and terminal.upper().startswith("T"):
        return name + terminal
    return name


def _offer(raw, date):
    """Map one row of either API onto the frozen contract; None when the row is incomplete."""
    fno = str(raw.get("flightNo") or raw.get("flight_no") or raw.get("code") or raw.get("flightNum") or "").strip()
    airline = str(raw.get("airlineName") or raw.get("airline") or raw.get("company") or "").strip()
    dep = _hhmm(raw.get("depTime") or raw.get("dptTime") or raw.get("deptTime"))
    arr = _hhmm(raw.get("arrTime") or raw.get("arriveTime") or raw.get("arrvTime"))
    price = _int(raw.get("price") or raw.get("adultPrice") or raw.get("lowestPrice") or raw.get("cabinPrice"))
    stops = raw.get("stops")
    if stops is None:
        stops = raw.get("stopCount")
    try:
        stops = int(stops)
    except (TypeError, ValueError):
        stops = None
    if not (fno and airline and dep and arr and price is not None and stops is not None):
        return None
    return {
        "platform": PLATFORM,
        "flight_no": fno,
        "airline": airline,
        "dep_time": dep,
        "arr_time": arr,
        "from_airport": _airport(raw.get("depAirport") or raw.get("fromAirport") or raw.get("depAirportShort"),
                                 raw.get("depTerminal") or raw.get("depTerm")),
        "to_airport": _airport(raw.get("arrAirport") or raw.get("toAirport") or raw.get("arrAirportShort"),
                               raw.get("arrTerminal") or raw.get("arrTerm")),
        "price": price,
        "price_base": price,
        "fees_included": bool(raw.get("feesIncluded")) if "feesIncluded" in raw else False,
        "stops": stops,
        "baggage": str(raw.get("baggage") or ""),
        "url": "%s?depCity=%s&arrCity=%s&goDate=%s&from=collector" % (
            DEEP, urllib.parse.quote(str(raw.get("_from") or "")), urllib.parse.quote(str(raw.get("_to") or "")), date),
        "note": str(raw.get("note") or ""),
    }


def _rows(obj):
    data = obj.get("data") if isinstance(obj, dict) else None
    if isinstance(data, dict):
        for key in ("flights", "flightList", "list", "flightInfos"):
            rows = data.get(key)
            if isinstance(rows, list):
                return rows
    return []


def fetch(date, route):
    started = time.time()
    from_city = route.get("from_city") or "上海"
    to_city = route.get("to_city") or "海口"
    referer = "%s?depCity=%s&arrCity=%s&goDate=%s&from=collector" % (
        DEEP, urllib.parse.quote(from_city), urllib.parse.quote(to_city), date)

    offers, trace = [], []

    # Plan A — the request the touch.qunar.com SPA itself issues.
    now = int(time.time() * 1000)
    body = {
        "arrCity": to_city, "depCity": from_city, "depDate": date,
        "firstRequest": True, "startNum": 0, "sort": 5,
        "r": now, "st": now, "_v": 2, "underageOption": "",
    }
    status, text = _post(TOUCH, body, referer)
    trace.append("touchInnerList http=%s body=%s" % (status, text[:220]))
    try:
        obj = json.loads(text)
    except ValueError:
        obj = None
    if obj:
        for raw in _rows(obj):
            row = dict(raw, _from=from_city, _to=to_city)
            o = _offer(row, date)
            if o:
                offers.append(o)

    # Plan B — legacy domestic list, both city-name and IATA-code spellings.
    if not offers and time.time() - started < DEADLINE:
        for dep, arr in ((from_city, to_city), ((route.get("from_codes") or [""])[0],
                                                (route.get("to_codes") or [""])[0])):
            if not dep or not arr:
                continue
            query = urllib.parse.urlencode({"depCity": dep, "arrCity": arr, "depDate": date,
                                            "startNum": 0, "sort": 5, "firstRequest": "true",
                                            "_v": 2}, encoding="utf-8")
            status, text = _get(WBD + "?" + query, "https://flight.qunar.com/touch/")
            trace.append("wbdFlightList(%s->%s) http=%s body=%s" % (dep, arr, status, text[:220]))
            try:
                obj = json.loads(text)
            except ValueError:
                continue
            rows = _rows(obj) if isinstance(obj, dict) else []
            if rows and not offers:
                for raw in rows:
                    row = dict(raw, _from=dep, _to=arr)
                    o = _offer(row, date)
                    if o:
                        offers.append(o)
            if offers:
                break

    if offers:
        return offers
    if time.time() - started > DEADLINE:
        raise QunarUnavailable("qunar over %ds budget" % DEADLINE)
    raise QunarUnavailable(
        "qunar returned no sellable flights for %s->%s %s (anonymous access is gated: the H5 SPA "
        "redirects to user.qunar.com/mobile/login.jsp?ret=...flightlist and every list API answers with "
        "an empty result set). trace: %s" % (from_city, to_city, date, " | ".join(trace)))
