"""One definition of "this flag is on".

Environment switches and stored settings both parse a truthy set; seven modules had
their own copy and they had already drifted.
"""

from __future__ import annotations

import pytest

from iPhoto.utils.env import env_flag, is_truthy


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_truthy_values(value: str) -> None:
    assert is_truthy(value) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "  "])
def test_falsy_values(value: str) -> None:
    assert is_truthy(value) is False


def test_none_and_booleans() -> None:
    assert is_truthy(None) is False
    assert is_truthy(True) is True
    assert is_truthy(False) is False


def test_env_flag_reads_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IPHOTO_TEST_FLAG", "yes")
    assert env_flag("IPHOTO_TEST_FLAG") is True

    monkeypatch.setenv("IPHOTO_TEST_FLAG", "off")
    assert env_flag("IPHOTO_TEST_FLAG") is False


def test_unset_variable_keeps_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IPHOTO_TEST_FLAG", raising=False)

    assert env_flag("IPHOTO_TEST_FLAG") is False
    assert env_flag("IPHOTO_TEST_FLAG", default=True) is True


def test_unrecognised_value_keeps_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IPHOTO_TEST_FLAG", "maybe")

    assert env_flag("IPHOTO_TEST_FLAG", default=False) is False
    assert env_flag("IPHOTO_TEST_FLAG", default=True) is True
