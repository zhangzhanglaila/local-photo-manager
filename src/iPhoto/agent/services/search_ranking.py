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

#: ``(floor, margin, cap, margin_tolerance)`` per precision tier.
#: ``margin_tolerance`` is the minimum ``sim(query) - max(sim(other animals))``;
#: ``None`` disables the label filter (the "loose" tier is the old behaviour plus
#: the relative cutoff).
PRECISION_TIERS: dict[str, tuple[float, float, int, float | None]] = {
    "strict": (0.25, 0.04, 120, 0.0),
    "balanced": (0.24, 0.06, 200, -0.01),
    "loose": (0.23, 0.08, 300, None),
}

DEFAULT_PRECISION = "balanced"

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


def precision_params(tier: str | None) -> tuple[float, float, int, float | None]:
    """Return ``(floor, margin, cap, margin_tolerance)`` for *tier*."""

    return PRECISION_TIERS.get(tier or "", PRECISION_TIERS[DEFAULT_PRECISION])


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
    cap: int,
    margins: Sequence[float] | np.ndarray | None = None,
    margin_tolerance: float | None = None,
) -> list[int]:
    """Return indices worth showing, strongest first.

    Keeps scores within *margin* of the best match but never below *floor*, drops
    results whose contrastive margin is below *margin_tolerance* (when provided),
    then caps the list.
    """

    scores_array = np.asarray(scores, dtype=np.float32)
    if scores_array.size == 0:
        return []

    cutoff = max(float(floor), float(scores_array.max()) - float(margin))
    keep = scores_array >= cutoff

    if margins is not None and margin_tolerance is not None:
        margins_array = np.asarray(margins, dtype=np.float32)
        if margins_array.shape == scores_array.shape:
            keep &= margins_array >= float(margin_tolerance)

    indices = np.nonzero(keep)[0]
    if indices.size == 0:
        return []

    order = np.argsort(scores_array[indices])[::-1]
    indices = indices[order]
    if cap > 0 and indices.size > cap:
        indices = indices[:cap]
    return [int(index) for index in indices.tolist()]
