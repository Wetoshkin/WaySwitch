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

    def close(self) -> None:  # noqa: B027 — необязательный хук, не все бэкенды держат ресурсы
        pass
