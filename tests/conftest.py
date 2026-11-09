"""Явный выбор языка интерфейса в тестах."""

from __future__ import annotations

import pytest

_LOCALE_VARIABLES = ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG")


def _set_locale(monkeypatch, **environment: str) -> None:
    for name in _LOCALE_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def ru_locale(monkeypatch):
    """Процесс видит русскую локаль; переменные остальных уровней сняты."""
    _set_locale(monkeypatch, LANG="ru_RU.UTF-8")


@pytest.fixture
def en_locale(monkeypatch):
    """Процесс видит английскую локаль, а не унаследованную локаль CI."""
    _set_locale(monkeypatch, LANG="en_US.UTF-8")


@pytest.fixture
def locale_env(monkeypatch):
    """Задать ровно те locale-переменные, которые нужны тесту."""
    def apply(**environment: str) -> None:
        _set_locale(monkeypatch, **environment)
    return apply
