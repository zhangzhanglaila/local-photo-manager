"""Query-time ranking helpers for semantic photo search.

Deliberately free of Qt and torch imports: this runs inside the search worker and
must stay cheap so it never competes with browsing. Two ideas live here:

* **Adaptive cutoff** – a single global cosine threshold means different things for
  different queries (``dog`` admitted 366 photos, ``beach`` only 61 in a real
  library). Keep the head of each query's own score distribution instead.
* **Contrastive animal filter** – CLIP's text space puts ``dog`` and ``cat`` at
  cosine 0.91, so a query for one often admits the others. For animal queries we
  subtract the best-scoring *other* animal label, which is the zero-shot
  "is this more a dog than any other animal?" decision. Generic labels such as
  ``pet``/``animal`` are excluded so a dog labelled "pet" is not discarded.
"""

from __future__ import annotations

import re
from typing import Iterable, Sequence

import numpy as np

#: ``(floor, margin, margin_tolerance, zscore)`` per precision tier.
#: There is deliberately no result cap: the cutoff decides what was found, and the
#: gallery loads result windows lazily, so a large match set stays cheap to browse.
#: ``margin_tolerance`` is the minimum ``sim(query) - max(sim(other animals))``;
#: ``None`` disables the label filter (the "loose" tier is the old behaviour plus
#: the relative cutoff).
#:
#: ``zscore`` is the standard-deviation cutoff. CLIP text/image cosines on a real
#: library cluster in a narrow band (0.21-0.30 for *any* query), so an absolute
#: floor let 70-95% of the library through and the same few "generic" images
#: (screenshots, flat backgrounds) appeared for every search. Keeping only the
#: tail of each query's own distribution makes the cutoff query-relative.
PRECISION_TIERS: dict[str, tuple[float, float, float | None, float]] = {
    "strict": (0.25, 0.04, 0.0, 2.2),
    "balanced": (0.24, 0.06, -0.01, 2.0),
    "loose": (0.23, 0.08, -0.02, 1.6),
}

#: Cosine ranges depend on how a model was trained. SigLIP's sigmoid objective
#: puts *matching* pairs around 0.10-0.15 and unrelated ones around 0.03, so the
#: CLIP floors (0.23-0.25) would reject every result and the CLIP-appropriate
#: ones return half the library for SigLIP. Floors are therefore per family; the
#: relative ``zscore`` term stays the same because it adapts to each query.
FAMILY_TIERS: dict[str, dict[str, tuple[float, float, float | None, float]]] = {
    "siglip": {
        "strict": (0.10, 0.02, 0.0, 2.2),
        "balanced": (0.08, 0.03, -0.005, 2.0),
        "loose": (0.06, 0.04, -0.01, 1.6),
    },
}

DEFAULT_PRECISION = "balanced"

#: Filler words removed before translating so "我的猫" behaves like "猫".
CJK_FILLERS: tuple[str, ...] = (
    "帮我找",
    "照片",
    "图片",
    "相片",
    "一些",
    "一只",
    "一个",
    "一张",
    "我的",
)

#: Chinese query terms mapped to the English text tower. Keys are matched
#: longest-first so compounds ("海边", "酸奶") are not split by single
#: characters; values stay lowercase.
ZH_EN_TERMS: dict[str, str] = {
    # animals
    "狗": "dog", "小狗": "puppy", "猫": "cat", "猫咪": "cat", "小猫": "kitten",
    "鸟": "bird", "小鸟": "bird", "鱼": "fish", "马": "horse", "牛": "cow",
    "羊": "sheep", "猪": "pig", "鸡": "chicken", "鸭": "duck", "兔子": "rabbit",
    "熊": "bear", "狼": "wolf", "狐狸": "fox", "鹿": "deer", "老虎": "tiger",
    "狮子": "lion", "熊猫": "panda", "大象": "elephant", "猴子": "monkey",
    "松鼠": "squirrel", "蝴蝶": "butterfly", "蜜蜂": "bee", "蛇": "snake",
    # food and drink
    "酸奶": "yogurt", "奶茶": "milk tea", "饮料": "drink beverage",
    "果汁": "juice", "牛奶": "milk", "咖啡": "coffee", "茶": "tea",
    "啤酒": "beer", "红酒": "wine", "白酒": "liquor", "酒": "wine alcohol",
    "蛋糕": "cake", "面包": "bread", "甜点": "dessert", "巧克力": "chocolate",
    "冰淇淋": "ice cream", "水果": "fruit", "苹果": "apple", "香蕉": "banana",
    "橙子": "orange", "葡萄": "grape", "西瓜": "watermelon", "草莓": "strawberry",
    "米饭": "rice", "面条": "noodles", "火锅": "hotpot", "烧烤": "barbecue",
    "汉堡": "hamburger", "披萨": "pizza", "寿司": "sushi", "早餐": "breakfast",
    "午餐": "lunch", "晚餐": "dinner", "零食": "snack", "糖果": "candy",
    "美食": "food", "食物": "food", "杯子": "cup", "瓶子": "bottle",
    # scenes and objects
    "树": "tree", "花": "flower", "山": "mountain", "水": "water",
    "海": "sea ocean", "海边": "beach", "沙滩": "sand beach", "河": "river",
    "湖": "lake", "森林": "forest", "草地": "grass", "天空": "sky",
    "日落": "sunset", "日出": "sunrise", "太阳": "sun", "月亮": "moon",
    "星星": "star", "雪": "snow", "雨": "rain", "云": "cloud",
    "建筑": "building", "房子": "house", "桥": "bridge", "路": "road",
    "公园": "park", "花园": "garden", "城市": "city",
    "夜景": "night cityscape", "乡村": "countryside village",
    "风景": "landscape scenery", "旅行": "travel", "婚礼": "wedding",
    "生日": "birthday", "聚会": "party", "自拍": "selfie",
    # vehicles and people
    "车": "car", "汽车": "car", "自行车": "bicycle", "飞机": "airplane",
    "船": "boat", "火车": "train", "地铁": "subway", "人": "person people",
    "孩子": "child children", "宝宝": "baby", "女孩": "girl", "男孩": "boy",
    "男人": "man", "女人": "woman",
    # colours
    "红色": "red", "红": "red", "蓝色": "blue", "蓝": "blue",
    "绿色": "green", "绿": "green", "黄色": "yellow", "黄": "yellow",
    "白色": "white", "白": "white", "黑色": "black", "黑": "black",
}

_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")

#: Grammatical particles never count as an untranslated word.
_CJK_PARTICLES = frozenset("的了在是和与")


def translate_query(query: str) -> str:
    """Best-effort Chinese → English rewrite for the English CLIP text tower.

    Unknown CJK is dropped once a known part was translated ("茉酸奶" → "yogurt"),
    otherwise the original text is returned so the caller can tell that nothing
    was understood.
    """

    text = (query or "").strip()
    if not text or not _CJK_PATTERN.search(text):
        return text

    lowered = text.casefold()
    for filler in sorted(CJK_FILLERS, key=len, reverse=True):
        lowered = lowered.replace(filler, " ")
    translated = lowered
    for term in sorted(ZH_EN_TERMS, key=len, reverse=True):
        if term in translated:
            translated = translated.replace(term, ZH_EN_TERMS[term])

    had_translation = translated != lowered
    if _CJK_PATTERN.search(translated):
        english = re.findall(r"[a-zA-Z]+", translated)
        if had_translation and english:
            return " ".join(english)
        return text
    return " ".join(translated.split())


def translation_is_partial(query: str) -> bool:
    """True when part of the CJK query has no known English translation."""

    text = (query or "").strip()
    if not _CJK_PATTERN.search(text):
        return False
    for filler in sorted(CJK_FILLERS, key=len, reverse=True):
        text = text.replace(filler, "")
    matched: set[str] = set()
    for term in ZH_EN_TERMS:
        if term in text:
            matched.update(term)
    remaining = set(_CJK_PATTERN.findall(text)) - matched - _CJK_PARTICLES
    return bool(remaining)

#: Species labels used only for contrast; generic ones would defeat the purpose.
ANIMAL_LABELS: tuple[str, ...] = (
    "dog",
    "puppy",
    "cat",
    "kitten",
    "horse",
    "cow",
    "sheep",
    "bird",
    "rabbit",
    "bear",
    "wolf",
    "fox",
    "deer",
    "pig",
)

#: ``(query terms, labels that count as a hit)``. Terms are matched case-insensitively;
#: ASCII terms match whole words only, CJK terms match as substrings.
QUERY_LABEL_GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("dog", "dogs", "doggy", "puppy", "puppies", "狗", "小狗", "犬"),
        ("dog", "puppy"),
    ),
    (
        ("cat", "cats", "kitty", "kitten", "kittens", "猫", "小猫", "猫咪"),
        ("cat", "kitten"),
    ),
    (("horse", "horses", "马"), ("horse",)),
    (("bird", "birds", "鸟", "小鸟"), ("bird",)),
    (("cow", "cows", "牛"), ("cow",)),
    (("sheep", "羊"), ("sheep",)),
    (("rabbit", "bunny", "兔子"), ("rabbit",)),
    (("bear", "熊"), ("bear",)),
    (("wolf", "狼"), ("wolf",)),
    (("fox", "狐狸"), ("fox",)),
    (("deer", "鹿"), ("deer",)),
    (("pig", "pigs", "猪"), ("pig",)),
)


def precision_params(
    tier: str | None,
    *,
    family: str = "clip",
) -> tuple[float, float, float | None, float]:
    """Return ``(floor, margin, margin_tolerance, zscore)`` for *tier*/*family*."""

    table = FAMILY_TIERS.get(family, PRECISION_TIERS)
    return table.get(tier or "", table[DEFAULT_PRECISION])


def labels_for_query(query: str) -> tuple[str, ...] | None:
    """Return the animal labels a query asks for, or ``None`` for other queries."""

    text = (query or "").casefold().strip()
    if not text:
        return None
    for terms, labels in QUERY_LABEL_GROUPS:
        for term in terms:
            if term.isascii():
                if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text):
                    return labels
            elif term in text:
                return labels
    return None


def other_labels(group: Iterable[str]) -> tuple[str, ...]:
    """Return the contrast labels that are not part of *group*."""

    wanted = set(group)
    return tuple(label for label in ANIMAL_LABELS if label not in wanted)


def select_results(
    scores: Sequence[float] | np.ndarray,
    *,
    floor: float,
    margin: float,
    margins: Sequence[float] | np.ndarray | None = None,
    margin_tolerance: float | None = None,
    zscore: float | None = None,
) -> list[int]:
    """Return indices worth showing, strongest first.

    Keeps scores within *margin* of the best match but never below *floor*, and
    drops results whose contrastive margin is below *margin_tolerance* (when
    provided). When *zscore* is given the cutoff also has to clear
    ``mean + zscore * std`` of this query's scores, which is what stops a query
    from returning most of the library. No fixed result limit: a larger match set
    is intentional.
    """

    scores_array = np.asarray(scores, dtype=np.float32)
    if scores_array.size == 0:
        return []

    cutoff = max(float(floor), float(scores_array.max()) - float(margin))
    if zscore is not None:
        std = float(scores_array.std())
        if std > 1e-6:
            tail_cutoff = float(scores_array.mean()) + float(zscore) * std
            cutoff = max(cutoff, tail_cutoff)
    keep = scores_array >= cutoff

    if margins is not None and margin_tolerance is not None:
        margins_array = np.asarray(margins, dtype=np.float32)
        if margins_array.shape == scores_array.shape:
            keep &= margins_array >= float(margin_tolerance)

    indices = np.nonzero(keep)[0]
    if indices.size == 0:
        return []

    order = np.argsort(scores_array[indices])[::-1]
    return [int(index) for index in indices[order].tolist()]


#: Families whose text tower understands Chinese directly.
MULTILINGUAL_FAMILIES = frozenset({"siglip", "chinese_clip"})


def query_texts(family: str, query: str, translated: str) -> list[str]:
    """Return the texts to encode for *query* on *family*.

    Multilingual models read the query as typed, and on SigLIP the raw Chinese
    often beats the translated English, so both are encoded and the best score
    per photo is kept. English-only CLIP gets just the translation.
    """

    if family in MULTILINGUAL_FAMILIES:
        texts = [query]
        if translated not in texts:
            texts.append(translated)
        return texts
    return [translated]


def translation_penalty_applies(family: str) -> bool:
    """Whether a partially translated query needs the stricter cutoff.

    A multilingual model reading the raw query is not guessing, so the penalty
    only applies to English-only families (it was also the reason SigLIP
    searches returned nothing: the penalty was a CLIP-scale 0.265 floor).
    """

    return family not in MULTILINGUAL_FAMILIES
