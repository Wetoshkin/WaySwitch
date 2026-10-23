"""Интерфейс бэкенда раскладки: узнать, переключить, дождаться, подписаться."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from wayswitch.keymap import LayoutSpec


class BackendError(RuntimeError):
    pass


class LayoutBackend(ABC):
    name = "abstract"
    supports_auto = False  # можно ли доверять current() для автоисправления

    @abstractmethod
    def layouts(self) -> list[LayoutSpec]: ...

    @abstractmethod
    def xkb_options(self) -> list[str]: ...

    @abstractmethod
    def current(self) -> int | None: ...

    @abstractmethod
    def set(self, index: int) -> None: ...

    @abstractmethod
    def wait_applied(self, index: int, timeout: float) -> bool: ...

    @abstractmethod
    def on_change(self, cb: Callable[[int, bool], None]) -> None:
        """cb(index, external): external=True, если раскладку сменили не мы."""

    def verify_layout(self) -> bool:
        """Совпадает ли представление бэкенда о раскладке с настоящей.

        Нужно там, где источник истины ненадёжен (IBus на GNOME 46+ меняет
        движок, но не раскладку компоузитора): если бэкенд врёт, исправление
        перепечатало бы слово в прежней раскладке и испортило текст.
        Бэкенды, которые читают раскладку у самого компоузитора, всегда True.
        """
        return True

    def get_selection(self) -> str | None:
        """Выделенный сейчас текст (для «Исправить выделенное»); None — бэкенд не умеет."""
        return None

    def close(self) -> None:  # noqa: B027 — необязательный хук, не все бэкенды держат ресурсы
        pass
