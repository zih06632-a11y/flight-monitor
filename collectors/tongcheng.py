# -*- coding: utf-8 -*-
"""同程旅行 (m.ly.com H5 / 北京同程) 机票列表采集器.

接口（真实线上接口，匿名可用，无需登录/无 dun 签名头）:
    POST https://wx.17u.cn/flightbffv2/book1/flights
    body: {"dcc":"SHA","acc":"HAK","pt":0,"ddate":"2026-12-16","cabin":0,"cc":0,
           "entrance":0,"depAirport":null,"arrAirport":null,"rtm":0,
           "passengerInfo":{"adultCount":1,"childCount":0,"infantCount":0},
           "pc":{"sd":"2026-12-09","ed":"2026-12-23"}}
    dcc/acc 为城市三字码（SHA 会同时返回虹桥与浦东）；pc 是价格日历窗口，非必需但线上页面会带。

关键返回字段（data.fl[]）:
    fn 航班号 dst/ast 起降时间 dasn/dat 出发机场/航站楼 aasn/aat 到达机场/航站楼
    asn 航司简称 nos 经停次数(缺省即 0) icsf 是否共享航班 sfd 实际承运航班号
    lps[] 各舱位最低价: dct==1 经济舱 / dct==2 公务舱, atp 票面价(不含机建燃油), brs[0].al 免费行李额kg
"""
import datetime
import json
import urllib.parse
import urllib.request

PLATFORM = "tongcheng"
API = "https://wx.17u.cn/flightbffv2/book1/flights"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36")
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Origin": "https://m.ly.com",
    "Referer": "https://m.ly.com/",
    "User-Agent": UA,
    "tcplat": "10060",
    "tcgroup": "1",
    "tcpolaris": "1",
}
AIRLINE = {
    "MU": "东方航空", "FM": "上海航空", "HO": "吉祥航空", "CZ": "南方航空", "CA": "中国国航",
    "HU": "海南航空", "MF": "厦门航空", "ZH": "深圳航空", "3U": "四川航空", "JD": "首都航空",
    "GJ": "长龙航空", "Y8": "金鹏航空", "SC": "山东航空", "9C": "春秋航空", "GS": "天津航空",
    "KY": "昆明航空", "PN": "西部航空", "8L": "祥鹏航空", "EU": "成都航空", "DZ": "东海航空",
    "A6": "湖南航空", "G5": "华夏航空", "NS": "河北航空", "TV": "西藏航空", "QW": "青岛航空",
    "UQ": "乌鲁木齐航空", "RY": "江西航空", "GX": "北部湾航空", "CN": "大新华航空",
}


def _post(payload: dict, timeout: int = 25) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        body = json.loads(raw)
    except ValueError:
        raise RuntimeError("tongcheng: 返回非 JSON (%d bytes): %r" % (len(raw), raw[:200]))
    if not body.get("success"):
        raise RuntimeError("tongcheng: 接口失败 %s %s" % (body.get("errorCode"), body.get("errorMessage")))
    return body


def _eco_fare(flight: dict):
    """经济舱最便宜的一条报价（票面价，不含机建燃油）。"""
    best = None
    for lp in flight.get("lps") or []:
        if lp.get("dct") != 1:
            continue
        price = lp.get("atp")
        if not isinstance(price, int) or price <= 0:
            continue
        if best is None or price < best.get("atp"):
            best = lp
    return best


def _baggage(fare: dict) -> str:
    for br in fare.get("brs") or []:
        al = br.get("al")
        if isinstance(al, int):
            return "含%dkg托运" % al if al > 0 else "无免费托运"
    return ""


def _deep_link(date: str, dcc: str, acc: str, dcn: str, acn: str) -> str:
    return ("https://m.ly.com/ft/retail/book1?date=%s&childticket=0,0&an=1&cn=0&baby=0"
            "&fromCity=%s&toCity=%s&fromcitycode=%s&fromCode=%s&tocitycode=%s&toCode=%s"
            "&acn=%s&dcn=%s&cabin=0&direct=0&frompage=HOME"
            % (date, urllib.parse.quote(dcn), urllib.parse.quote(acn), dcc, dcc, acc, acc,
               urllib.parse.quote(acn), urllib.parse.quote(dcn)))


def fetch(date: str, route: dict) -> list:
    dcc = (route.get("from_codes") or ["SHA"])[0]
    acc = (route.get("to_codes") or ["HAK"])[0]
    dcn = route.get("from_city") or ""
    acn = route.get("to_city") or ""
    day = datetime.date(int(date[0:4]), int(date[5:7]), int(date[8:10]))
    sd = max(day - datetime.timedelta(days=7), datetime.date.today())
    ed = day + datetime.timedelta(days=7)

    body = _post({
        "dcc": dcc, "acc": acc, "pt": 0, "ddate": day.isoformat(), "cabin": 0, "cc": 0,
        "entrance": 0, "depAirport": None, "arrAirport": None, "rtm": 0,
        "passengerInfo": {"adultCount": 1, "childCount": 0, "infantCount": 0},
        "pc": {"sd": sd.isoformat(), "ed": ed.isoformat()},
    })
    flights = ((body.get("data") or {}).get("fl")) or []
    if not flights:
        raise RuntimeError("tongcheng: 接口返回 0 个航班 (%s %s->%s)" % (date, dcc, acc))

    url = _deep_link(day.isoformat(), dcc, acc, dcn, acn)
    # 同一架飞机常被多家以共享航班号重复售卖（icsf=True），只保留实际承运航班
    picked = {}
    for f in flights:
        fare = _eco_fare(f)
        if fare is None:
            continue
        key = f.get("sfd") or f.get("fn")
        is_share = bool(f.get("icsf"))
        prev = picked.get(key)
        if prev is None or (prev[1] and not is_share) or (prev[1] == is_share and fare["atp"] < prev[0]["atp"]):
            picked[key] = (fare, is_share, f)

    offers = []
    for fare, is_share, f in picked.values():
        price = int(fare["atp"])
        note_bits = []
        for pt in fare.get("pts") or []:
            if pt.get("td"):
                note_bits.append(pt["td"])
                break
        if is_share:
            note_bits.append("共享航班")
        note_bits.append("票面价+机建50+燃油")
        offers.append({
            "platform": PLATFORM,
            "flight_no": f.get("fn", ""),
            "airline": AIRLINE.get(f.get("ac"), f.get("asn") or f.get("ac") or ""),
            "dep_time": f.get("dst") or (f.get("dt") or "")[11:16],
            "arr_time": f.get("ast") or (f.get("at") or "")[11:16],
            "from_airport": "%s%s" % (f.get("dasn") or f.get("dac") or "", f.get("dat") or ""),
            "to_airport": "%s%s" % (f.get("aasn") or f.get("aac") or "", f.get("aat") or ""),
            # 页面给的是票面价：机建+燃油不在其中，交给框架按 config.fees 补算
            "price": price,
            "price_base": price,
            "fees_included": False,
            "stops": int(f.get("nos") or 0),
            "baggage": _baggage(fare),
            "url": url,
            "note": "/".join(note_bits),
        })
    if not offers:
        raise RuntimeError("tongcheng: 有航班但无可售经济舱报价")
    return offers
