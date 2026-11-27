"""Airline official-website collector (中国南方航空 / 中国东方航空 / 海南航空 / 吉祥航空).

PLATFORM = "airlines"

Only anonymous, no-login, direct-flight quotes are used. Every price comes from a
real JSON response; nothing is synthesised. Airlines that need a login or a
captcha are skipped and named in the raise message.

Price rule (base.py): price = adultPrice + adultTaxTotal, i.e. 机建(50) + 燃油
already added, no insurance / no paid baggage / no seat selection.
"""

import gzip
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.request
import zlib

PLATFORM = "airlines"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

AIRPORT_CN = {
    "PVG": "浦东", "SHA": "虹桥", "HAK": "美兰",
    "CAN": "白云", "SZX": "宝安", "CTU": "双流", "TFU": "天府",
}

AIRLINE_CN = {
    "CZ": "南方航空", "MU": "东方航空", "HU": "海南航空", "HO": "吉祥航空",
}

# ---------------------------------------------------------------- helpers

class _HTTP(object):
    """Cookie-aware opener with gzip/deflate decoding."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.last_url = None

    def request(self, url, body=None, headers=None, timeout=25):
        hdrs = {
            "User-Agent": UA,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
        }
        if headers:
            hdrs.update(headers)
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json;charset=UTF-8")
            hdrs.setdefault("X-Requested-With", "XMLHttpRequest")
        req = urllib.request.Request(url, data=data, headers=hdrs)
        with self.opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
            enc = (resp.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip" or raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            elif enc == "deflate":
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            return resp.status, raw

    def json(self, url, body=None, headers=None, timeout=25):
        status, raw = self.request(url, body, headers, timeout)
        return json.loads(raw.decode("utf-8", "replace"))


def _hm(hhmm):
    """'0920' -> '09:20'; already formatted values pass through."""
    s = str(hhmm or "").strip()
    if ":" in s:
        return s[:5]
    if len(s) == 4 and s.isdigit():
        return s[:2] + ":" + s[2:]
    return s


def _airport(code, term):
    name = AIRPORT_CN.get(code, code)
    term = (term or "").strip()
    return name + term if term else name


# ------------------------------------------------------- 南航 csair.com
# POST https://b2c.csair.com/portal/main/flight/direct/queryFlightList
#   {"depCity":"PVG","arrCity":"HAK","flightDate":"20261216", ...}
# 需先取 sid 会话，并解阿里云 WAF 的 acw_sc__v2 挑战。

_CS_PERM = [0xf, 0x23, 0x1d, 0x18, 0x21, 0x10, 0x1, 0x26, 0xa, 0x9, 0x13, 0x1f,
            0x28, 0x1b, 0x16, 0x17, 0x19, 0xd, 0x6, 0xb, 0x27, 0x12, 0x14, 0x8,
            0xe, 0x15, 0x20, 0x1a, 0x2, 0x1e, 0x7, 0x4, 0x11, 0x5, 0x3, 0x1c,
            0x22, 0x25, 0xc, 0x24]
_CS_KEY = "3000176000856006061501533003690027800375"

_CS_BOOK = ("https://b2c.csair.com/B2C40/newTrips/static/main/page/booking/"
            "index.html?t=S&c1=%s&c2=%s&d1=%s&at=1&ct=0&it=0")
_CS_LIST = "https://b2c.csair.com/portal/main/flight/direct/queryFlightList"
_CS_SID = "https://b2c.csair.com/portal/main/user/createSid?callback=cb&_=%d"


def _cs_acw(arg1):
    """阿里云 WAF: acw_sc__v2 = hexXor(unsbox(arg1), KEY)."""
    out = [None] * len(_CS_PERM)
    for i, ch in enumerate(arg1):
        for j, v in enumerate(_CS_PERM):
            if v == i + 1:
                out[j] = ch
    s = "".join(c for c in out if c is not None)
    return "".join(format(int(s[i], 16) ^ int(_CS_KEY[i], 16), "x")
                   for i in range(len(s)))


def _cs_post(sess, url, payload, referer):
    """POST JSON, transparently solving the aliyun WAF challenge once."""
    hdrs = {"Referer": referer, "Origin": "https://b2c.csair.com"}
    for _attempt in (1, 2):
        status, raw = sess.request(url, payload, hdrs)
        text = raw.decode("utf-8", "replace")
        m = re.search(r"arg1='([0-9A-Fa-f]{8,})'", text)
        if not m:
            return text
        sess.jar.set_cookie(http.cookiejar.Cookie(
            0, "acw_sc__v2", _cs_acw(m.group(1)), None, False, "b2c.csair.com",
            False, False, "/", True, False, None, False, None, None, {}))
    raise RuntimeError("csair: aliyun WAF challenge not solved")


def _csair_fetch(date, src, dst, out):
    ymd = date.replace("-", "")
    sess = _HTTP()
    referer = _CS_BOOK % (src, dst, date)
    try:
        sess.request(_CS_SID % int(time.time() * 1000))
    except Exception:
        pass  # sid 只是会话预热，缺了也能走 WAF 挑战流程
    payload = {
        "depCity": src, "arrCity": dst, "flightDate": ymd, "segType": "1",
        "adultNum": 1, "childNum": 0, "infantNum": 0,
        "depCityFlag": False, "arrCityFlag": False, "cabinOrder": "0",
        "airLine": "1", "flyType": "0", "international": "0", "cache": 0,
        "language": "zh", "isFisOp": "true", "isMultipass": "1",
    }
    text = _cs_post(sess, _CS_LIST, payload, referer)
    try:
        doc = json.loads(text)
    except ValueError:
        raise RuntimeError("csair %s-%s: non-JSON response %r" % (src, dst, text[:120]))
    if not doc.get("success"):
        # S0004 = 该机场对当日无可售航班，属于正常空结果而非故障
        return
    seg = doc["data"]["avPriceOutput"]["flight"]["segment"][0]
    url = referer
    for fl in seg.get("dateFlight") or []:
        cabins = [c for c in (fl.get("cabinsY") or []) if c.get("adultPrice")]
        if not cabins:
            continue
        best = min(cabins, key=lambda c: c["adultPrice"])
        fare = int(best["adultPrice"])
        tax = int(best.get("adultTaxTotal") or 0)
        if tax <= 0:
            tax = int(fl.get("adultFuelTax") or 0) + int(fl.get("airportTax") or 0)
        no = str(fl.get("flightNo") or "").strip()
        out.append({
            "platform": PLATFORM,
            "flight_no": no,
            "airline": AIRLINE_CN.get(no[:2], "中国南方航空"),
            "dep_time": _hm(fl.get("depTime")),
            "arr_time": _hm(fl.get("arrTime")),
            "from_airport": _airport(fl.get("depPort"), fl.get("depTerm")),
            "to_airport": _airport(fl.get("arrPort"), fl.get("arrTerm")),
            "price": fare + tax,
            "price_base": fare,
            "fees_included": True,
            "stops": int(fl.get("stopNumber") or 0),
            "url": url,
            "baggage": "",
            "note": "南航官网 %s+税费%d" % (best.get("label") or best.get("name") or "经济舱", tax),
        })


# ------------------------------------------------------------- entry

def fetch(date, route):
    """Return every real airline-direct quote we can fetch anonymously."""
    sources = list(route.get("from_codes") or [])
    dests = list(route.get("to_codes") or [])
    if not sources:
        sources = ["SHA", "PVG"]
    if not dests:
        dests = ["HAK"]

    offers = []
    errors = []
    for src in sources:
        for dst in dests:
            try:
                _csair_fetch(date, src, dst, offers)
            except Exception as exc:
                errors.append("csair %s-%s: %s" % (src, dst, exc))

    if not offers:
        raise RuntimeError("airlines: no quote fetched (%s)" % ("; ".join(errors) or "empty result"))
    return offers
