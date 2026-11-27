"""City name -> IATA airport code resolution.

Only Chinese mainland cities are covered by the built-in table. Anything else must be
passed as a 3-letter code (--from-code / --to-code) — an unknown name raises a clear
error instead of silently querying the wrong airport.
"""

CITY_CODES = {
    "北京": ["PEK", "PKX"],
    "上海": ["SHA", "PVG"],
    "广州": ["CAN"],
    "深圳": ["SZX"],
    "成都": ["CTU", "TFU"],
    "重庆": ["CKG"],
    "天津": ["TSN"],
    "杭州": ["HGH"],
    "南京": ["NKG"],
    "武汉": ["WUH"],
    "西安": ["XIY"],
    "昆明": ["KMG"],
    "厦门": ["XMN"],
    "青岛": ["TAO"],
    "长沙": ["CSX"],
    "郑州": ["CGO"],
    "济南": ["TNA"],
    "合肥": ["HFE"],
    "福州": ["FOC"],
    "南昌": ["KHN"],
    "南宁": ["NNG"],
    "贵阳": ["KWE"],
    "兰州": ["LHW"],
    "西宁": ["XNN"],
    "银川": ["INC"],
    "乌鲁木齐": ["URC"],
    "拉萨": ["LXA"],
    "呼和浩特": ["HET"],
    "太原": ["TYN"],
    "石家庄": ["SJW"],
    "海口": ["HAK"],
    "三亚": ["SYX"],
    "大连": ["DLC"],
    "沈阳": ["SHE"],
    "哈尔滨": ["HRB"],
    "长春": ["CGQ"],
    "温州": ["WNZ"],
    "宁波": ["NGB"],
    "无锡": ["WUX"],
    "珠海": ["ZUH"],
    "汕头": ["SWA"],
    "桂林": ["KWL"],
    "丽江": ["LJG"],
    "西双版纳": ["JHG"],
    "张家界": ["DYG"],
    "烟台": ["YNT"],
}


def resolve(name_or_code, explicit_code=None):
    """Returns a list of IATA codes for a city name (or the given explicit code)."""
    if explicit_code:
        return [explicit_code.strip().upper()]
    text = str(name_or_code).strip()
    # ASCII-only: "北京市".isalpha() is True in Python, so a plain isalpha() check would
    # treat a 3-character Chinese city name as an IATA code.
    if len(text) == 3 and text.isascii() and text.isalpha():
        return [text.upper()]
    key = text
    for suffix in ("市", "机场"):
        if key.endswith(suffix):
            key = key[: -len(suffix)]
    if key in CITY_CODES:
        return CITY_CODES[key]
    raise KeyError(
        "unknown city %r. Known: %s. Or pass --from-code/--to-code with a 3-letter IATA code."
        % (text, ", ".join(sorted(CITY_CODES)))
    )


def primary(name_or_code, explicit_code=None):
    """The code used to key a route in the database, e.g. 上海 -> SHA."""
    return resolve(name_or_code, explicit_code)[0]
