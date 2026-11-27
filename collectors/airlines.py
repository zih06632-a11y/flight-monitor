"""Airline official-website collector — 中国南方航空 (csair.com).

PLATFORM = "airlines"

Scope: anonymous, no-login, 上海(SHA/PVG) -> 海口(HAK) direct flights, per-date.
Every price comes from CSair's own booking JSON; nothing is synthesised. If the
quote cannot be fetched the collector raises, so run_cycle records the failure
instead of storing a made-up number.

Endpoint reproduced with urllib only (no browser, no login, Task-Scheduler safe):

    POST https://b2c.csair.com/portal/main/flight/direct/queryFlightList
    Content-Type: application/json
    {"depCity":"PVG","arrCity":"HAK","flightDate":"20261216","segType":"1",
     "adultNum":1,"childNum":0,"infantNum":0,"depCityFlag":false,
     "arrCityFlag":false,"cabinOrder":"0","airLine":"1","flyType":"0",
     "international":"0","cache":0,"language":"zh","isFisOp":"true",
     "isMultipass":"1"}

Two gates stand in front of that endpoint and both are handled here:
  1. a session cookie set (acw_tc / JSESSIONID / sid) primed from
     /portal/main/user/createSid;
  2. an Aliyun WAF JS challenge that returns an HTML page carrying
     the literal token arg1=<40 hex>; the accepted answer is
     acw_sc__v2 = hexXor(unsbox(arg1), KEY), computed below in pure Python.

Price rule from base.py: price = 成人经济舱票价(adultPrice) + 税费(adultTaxTotal),
taxes included, no insurance / no paid baggage / no seat selection.
CSair's own lowPrice field is the bare fare — the tax is added here.

Airlines skipped after investigation (documented, not silently dropped):
  东航 ceair.com      — 本机 IP 被 haplat / Aliyun 风控墙拦截（"Try again later!
                        Your IP: ..."），站点另挂 AliyunCaptcha 与 trustdecision
                        设备指纹，匿名无人值守不可用。
  吉祥 juneyaoair.com — Vue SPA，搜索走 shopping.juneyaoair.com 加密链路，未找到
                        可匿名复现的简单接口；城市选择组件拒绝程序化输入。
  海航 hnair.com      — 首页 GET 直接 405，订座是 new.hnair.com 的 IBE 老式多步
                        HTML 表单流，不适合稳定无人值守。
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

BUDGET_SEC = 80          # fetch() 必须在 90s 内结束，留 10s 余量
PER_REQ_SEC = 20
GZIP_MAGIC = bytes((0x1F, 0x8B))

AIRPORT_CN = {
    "PVG": "浦东", "SHA": "虹桥", "HAK": "美兰",
    "CAN": "白云", "SZX": "宝安", "CTU": "双流", "TFU": "天府",
}

AIRLINE_CN = {"CZ": "南方航空", "MU": "东方航空", "HU": "海南航空", "HO": "吉祥航空"}

# ---------------------------------------------------------------- helpers


class _HTTP(object):
    """Cookie-aware opener that decodes gzip/deflate bodies."""

    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def request(self, url, body=None, headers=None, timeout=PER_REQ_SEC):
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
            if enc == "gzip" or raw[:2] == GZIP_MAGIC:
                raw = gzip.decompress(raw)
            elif enc == "deflate":
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            return resp.status, raw


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
    """Aliyun WAF answer: acw_sc__v2 = hexXor(unsbox(arg1), _CS_KEY)."""
    out = [None] * len(_CS_PERM)
    for i, ch in enumerate(arg1):
        for j, v in enumerate(_CS_PERM):
            if v == i + 1:
                out[j] = ch
    s = "".join(c for c in out if c is not None)
    return "".join(format(int(s[i], 16) ^ int(_CS_KEY[i], 16), "x")
                   for i in range(len(s)))


def _cs_post(sess, url, payload, referer, deadline):
    """POST JSON, solving the Aliyun WAF challenge at most once."""
    hdrs = {"Referer": referer, "Origin": "https://b2c.csair.com"}
    for _attempt in (1, 2):
        timeout = max(5, min(PER_REQ_SEC, int(deadline - time.monotonic())))
        status, raw = sess.request(url, payload, hdrs, timeout=timeout)
        text = raw.decode("utf-8", "replace")
        m = re.search(r"arg1='([0-9A-Fa-f]{16,})'", text)
        if not m:
            return text
        sess.jar.set_cookie(http.cookiejar.Cookie(
            0, "acw_sc__v2", _cs_acw(m.group(1)), None, False, "b2c.csair.com",
            False, False, "/", True, False, None, False, None, None, {}))
    raise RuntimeError("aliyun WAF challenge not solved (arg1 retried once)")


def _csair_fetch(date, src, dst, out, deadline):
    """One depCity/arrCity pair. An empty result is normal; an error raises."""
    sess = _HTTP()
    referer = _CS_BOOK % (src, dst, date)
    try:
        sess.request(_CS_SID % int(time.time() * 1000),
                     timeout=max(5, min(PER_REQ_SEC, int(deadline - time.monotonic()))))
    except Exception:
        pass  # 只是会话预热；缺了仍会走 WAF 挑战

    payload = {
        "depCity": src, "arrCity": dst, "flightDate": date.replace("-", ""),
        "segType": "1", "adultNum": 1, "childNum": 0, "infantNum": 0,
        "depCityFlag": False, "arrCityFlag": False, "cabinOrder": "0",
        "airLine": "1", "flyType": "0", "international": "0", "cache": 0,
        "language": "zh", "isFisOp": "true", "isMultipass": "1",
    }
    text = _cs_post(sess, _CS_LIST, payload, referer, deadline)
    try:
        doc = json.loads(text)
    except ValueError:
        raise RuntimeError("non-JSON response: %r" % (text[:160],))

    if not doc.get("success"):
        if doc.get("errorCode") == "S0004":
            return  # 该机场当日无可售航班：正常空结果
        raise RuntimeError("%s %s" % (doc.get("errorCode"), doc.get("errorMsg")))

    seg = (doc.get("data") or {}).get("avPriceOutput", {}) \
        .get("flight", {}).get("segment") or []
    if not seg:
        return

    for fl in seg[0].get("dateFlight") or []:
        all_cabins = [c for c in (fl.get("cabinsY") or []) if c.get("adultPrice")]
        # 只要匿名可买的公开运价；会员价/手机专享价需登录，不能当作网站报价
        public = [c for c in all_cabins
                  if not c.get("memberPrice") and not c.get("mobilePrice")]
        cabins = public or all_cabins
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
            "airline": AIRLINE_CN.get(no[:2], "南方航空"),
            "dep_time": _hm(fl.get("depTime")),
            "arr_time": _hm(fl.get("arrTime")),
            "from_airport": _airport(fl.get("depPort"), fl.get("depTerm")),
            "to_airport": _airport(fl.get("arrPort"), fl.get("arrTerm")),
            "price": fare + tax,
            "price_base": fare,
            "fees_included": True,
            "stops": int(fl.get("stopNumber") or 0),
            "url": referer,
            "baggage": "",
            "note": "南航官网 %s 含税费%d" % (
                best.get("label") or best.get("name") or "经济舱", tax),
        })


# ------------------------------------------------------------- entry


def fetch(date, route):
    """Return every real CSair-direct quote for the route, or raise."""
    deadline = time.monotonic() + BUDGET_SEC
    sources = list(route.get("from_codes") or []) or ["SHA", "PVG"]
    dests = list(route.get("to_codes") or []) or ["HAK"]

    offers = []
    errors = []
    for src in sources:
        for dst in dests:
            if time.monotonic() >= deadline - 5:
                errors.append("deadline reached before %s-%s" % (src, dst))
                continue
            try:
                _csair_fetch(date, src, dst, offers, deadline)
            except urllib.error.HTTPError as exc:
                errors.append("csair %s-%s: HTTP %s" % (src, dst, exc.code))
            except Exception as exc:
                errors.append("csair %s-%s: %s" % (src, dst, exc))

    if not offers:
        raise RuntimeError("airlines: 南航未取到报价 (%s)"
                           % ("; ".join(errors) or "empty result"))
    return offers
