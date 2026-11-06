"""One definition of "this flag is on".

Seven modules parsed environment switches with their own copy of the truthy set,
and eight more parsed the same set out of stored settings. The copies had already
drifted (``IPHOTO_DISABLE_OPENGL`` was read in six places, one of them built its own
literal set inline), so a user could reasonably wonder why the same switch behaved
differently depending on which code path read it.
"""

from __future__ import annotations

import os

#: Values treated as "on" for environment switches and stored settings.
TRUE_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})
#: Values treated as "off"; anything else falls back to the caller's default.
FALSE_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off", ""})


def is_truthy(value: object) -> bool:
    """Return whether *value* represents an enabled flag."""

    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in TRUE_VALUES


def env_flag(name: str, *, default: bool = False) -> bool:
    """Return the boolean value of environment variable *name*.

    Unset variables keep *default*; anything outside the recognised sets also keeps
    it, so a typo does not silently flip behaviour.
    """

    raw = os.environ.get(name)
    if raw is None:
        return default
    normalised = raw.strip().lower()
    if normalised in TRUE_VALUES:
        return True
    if normalised in FALSE_VALUES:
        return False
    return default


__all__ = ["FALSE_VALUES", "TRUE_VALUES", "env_flag", "is_truthy"]
