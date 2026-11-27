"""Collector contract — FROZEN. A collector module must not depend on editing this file.

Every collectors/<platform>.py exposes exactly:

    PLATFORM = "tongcheng"
    def fetch(date: str, route: dict) -> list[dict]: ...

route = {"from_city": "上海", "to_city": "海口", "from_codes": ["SHA","PVG"],
         "to_codes": ["HAK"], "date": "2026-12-16"}

Required offer keys:
    platform      str   must equal PLATFORM
    flight_no     str   "MU5305"
    airline       str   "东方航空"
    dep_time      str   "07:35"
    arr_time      str   "10:50"
    from_airport  str   "虹桥T2"   (include terminal when known)
    to_airport    str   "美兰T1"
    price         int   成人经济舱【含机建+燃油的实付总价】, CNY
    stops         int   0 = direct
    url           str   deep link that opens this search on that platform

Optional offer keys (strongly preferred — these drive the price comparison):
    price_base    int   页面标价（不含机建/燃油）
    fees_included bool  是否已在 price 里包含机建+燃油；默认 True。填 False 时框架会补上估算税费
    baggage       str   "含20kg托运" / "无免费托运" / ""  仅记录展示，不参与比价
    note          str   "会员价" / "含税" / ...

PRICE RULE (non-negotiable): price is what the user actually pays with
  - no insurance, no paid baggage, no seat selection,
  - airport construction fee + fuel surcharge INCLUDED.
If a site only shows a pre-tax fare, either add the known fees yourself and keep
fees_included=True, or leave price as the bare fare and set fees_included=False.

Rules: no login, no user interaction, finish within 90 seconds, and RAISE on failure
(run_cycle records the error and continues with the other platforms).
"""

REQUIRED = ("platform", "flight_no", "airline", "dep_time", "arr_time",
            "from_airport", "to_airport", "price", "stops", "url")


def validate(offer: dict) -> dict:
    for k in REQUIRED:
        if k not in offer:
            raise ValueError("offer missing key %r: %r" % (k, offer))
    if not isinstance(offer["price"], int) or offer["price"] <= 0:
        raise ValueError("price must be a positive int: %r" % (offer,))
    if not isinstance(offer["stops"], int) or offer["stops"] < 0:
        raise ValueError("stops must be int >= 0: %r" % (offer,))
    offer.setdefault("note", "")
    offer.setdefault("baggage", "")
    offer.setdefault("price_base", None)
    offer.setdefault("fees_included", True)
    if offer["price_base"] is not None and not isinstance(offer["price_base"], int):
        raise ValueError("price_base must be int or None: %r" % (offer,))
    return offer


def total_price(offer: dict, fees_cfg: dict) -> int:
    """All-in price the user pays: no insurance, no baggage, taxes included."""
    price = int(offer["price"])
    if offer.get("fees_included", True):
        return price
    base = offer.get("price_base")
    if isinstance(base, int) and base > 0:
        return base + int(fees_cfg["fallback_total_cny"])
    return price + int(fees_cfg["fallback_total_cny"])
