"""Chinese → latin place aliases for the location search.

The bundled offline reverse geocoder returns transliterated names ("wuhan —
hubei"), so a Chinese query like 武汉 or 武汉科技大学 never matched anything. This
table bridges the common cases; anything missing simply falls through to the
normal semantic search instead of producing wrong results.
"""

from __future__ import annotations

#: Chinese place name → the strings the geocoder may return.
CITY_ALIASES: dict[str, tuple[str, ...]] = {
    # China — cities
    "武汉": ("wuhan",),
    "武昌": ("wuchang",),
    "汉口": ("hankou",),
    "汉阳": ("hanyang",),
    "北京": ("beijing",),
    "上海": ("shanghai",),
    "广州": ("guangzhou", "canton"),
    "深圳": ("shenzhen",),
    "杭州": ("hangzhou",),
    "南京": ("nanjing",),
    "成都": ("chengdu",),
    "重庆": ("chongqing",),
    "西安": ("xi'an", "xian"),
    "天津": ("tianjin",),
    "苏州": ("suzhou",),
    "长沙": ("changsha",),
    "郑州": ("zhengzhou",),
    "青岛": ("qingdao",),
    "厦门": ("xiamen",),
    "福州": ("fuzhou",),
    "昆明": ("kunming",),
    "大理": ("dali",),
    "丽江": ("lijiang",),
    "桂林": ("guilin",),
    "三亚": ("sanya",),
    "海口": ("haikou",),
    "合肥": ("hefei",),
    "南昌": ("nanchang",),
    "济南": ("jinan",),
    "太原": ("taiyuan",),
    "石家庄": ("shijiazhuang",),
    "沈阳": ("shenyang",),
    "大连": ("dalian",),
    "长春": ("changchun",),
    "哈尔滨": ("harbin",),
    "呼和浩特": ("hohhot",),
    "兰州": ("lanzhou",),
    "西宁": ("xining",),
    "银川": ("yinchuan",),
    "乌鲁木齐": ("urumqi",),
    "拉萨": ("lhasa",),
    "贵阳": ("guiyang",),
    "南宁": ("nanning",),
    "香港": ("hong kong", "hongkong"),
    "澳门": ("macau", "macao"),
    "台北": ("taipei",),
    "高雄": ("kaohsiung",),
    # China — provinces / regions
    "湖北": ("hubei",),
    "湖南": ("hunan",),
    "河南": ("henan",),
    "河北": ("hebei",),
    "山东": ("shandong",),
    "山西": ("shanxi",),
    "陕西": ("shaanxi",),
    "江苏": ("jiangsu",),
    "浙江": ("zhejiang",),
    "安徽": ("anhui",),
    "福建": ("fujian",),
    "江西": ("jiangxi",),
    "广东": ("guangdong",),
    "广西": ("guangxi",),
    "海南": ("hainan",),
    "四川": ("sichuan",),
    "云南": ("yunnan",),
    "贵州": ("guizhou",),
    "辽宁": ("liaoning",),
    "吉林": ("jilin",),
    "黑龙江": ("heilongjiang",),
    "内蒙古": ("inner mongolia",),
    "新疆": ("xinjiang",),
    "西藏": ("tibet", "xizang"),
    "甘肃": ("gansu",),
    "青海": ("qinghai",),
    "宁夏": ("ningxia",),
    # Other common destinations
    "东京": ("tokyo",),
    "大阪": ("osaka",),
    "京都": ("kyoto",),
    "北海道": ("hokkaido",),
    "冲绳": ("okinawa",),
    "首尔": ("seoul",),
    "釜山": ("busan",),
    "曼谷": ("bangkok",),
    "清迈": ("chiang mai", "chiangmai"),
    "普吉": ("phuket",),
    "新加坡": ("singapore",),
    "吉隆坡": ("kuala lumpur",),
    "雅加达": ("jakarta",),
    "马尼拉": ("manila",),
    "河内": ("hanoi",),
    "胡志明": ("ho chi minh",),
    "迪拜": ("dubai",),
    "巴黎": ("paris",),
    "伦敦": ("london",),
    "罗马": ("rome",),
    "米兰": ("milan",),
    "威尼斯": ("venice",),
    "巴塞罗那": ("barcelona",),
    "马德里": ("madrid",),
    "柏林": ("berlin",),
    "慕尼黑": ("munich",),
    "阿姆斯特丹": ("amsterdam",),
    "苏黎世": ("zurich",),
    "维也纳": ("vienna",),
    "布拉格": ("prague",),
    "莫斯科": ("moscow",),
    "圣彼得堡": ("saint petersburg",),
    "伊斯坦布尔": ("istanbul",),
    "纽约": ("new york",),
    "洛杉矶": ("los angeles",),
    "旧金山": ("san francisco",),
    "西雅图": ("seattle",),
    "芝加哥": ("chicago",),
    "波士顿": ("boston",),
    "华盛顿": ("washington",),
    "拉斯维加斯": ("las vegas",),
    "温哥华": ("vancouver",),
    "多伦多": ("toronto",),
    "悉尼": ("sydney",),
    "墨尔本": ("melbourne",),
    "奥克兰": ("auckland",),
    "开罗": ("cairo",),
    "开普敦": ("cape town",),
}


def aliases_for(query: str) -> list[str]:
    """Return lowercase needle strings to look for in a place label.

    The query itself is always included (so an English or pinyin query works),
    plus every alias whose Chinese key appears inside the query — that is what
    makes 武汉科技大学 match photos tagged "wuhan — hubei".
    """

    text = (query or "").strip().lower()
    if not text:
        return []
    needles = [text]
    for key, values in CITY_ALIASES.items():
        if key in query:
            needles.extend(values)
    # Longer needles first: fewer, more specific matches win.
    unique = {needle for needle in needles if needle}
    return sorted(unique, key=len, reverse=True)


__all__ = ["CITY_ALIASES", "aliases_for"]
