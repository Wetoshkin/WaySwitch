#!/usr/bin/env python3
"""Сквозной тест в VM: виртуальная «физическая» клавиатура печатает ghbdtn␣,
а мы читаем evdev-узел выходного устройства демона и сверяем последовательность.

Запуск при работающем демоне и открытом текстовом поле в фокусе:
    python3 tests/e2e_vm.py
Ожидание: демон молчал во время набора, затем 7×Backspace, затем перепечатка
«привет » кодами ru-раскладки; все клавиши отпущены.
"""

import sys
import time

from evdev import InputDevice, UInput, ecodes, list_devices

VIRTUAL_NAME = "WaySwitch Virtual Keyboard"
WORD = [34, 35, 48, 32, 20, 49]  # g h b d t n
EXPECTED_TAIL = [34, 35, 48, 32, 20, 49, 57]  # те же коды в ru = привет + пробел


def find_output():
    for path in list_devices():
        dev = InputDevice(path)
        if dev.name == VIRTUAL_NAME:
            return dev
    sys.exit("не найдено выходное устройство демона — он запущен?")


def main() -> int:
    out = find_output()  # без grab: копию потока читаем параллельно с компоситором
    fake = UInput({ecodes.EV_KEY: list(range(1, 128))}, name="WaySwitch E2E Keyboard")
    time.sleep(1.0)  # демон открывает новое устройство с задержкой
    for code in WORD:
        fake.write(ecodes.EV_KEY, code, 1)
        fake.syn()
        fake.write(ecodes.EV_KEY, code, 0)
        fake.syn()
        time.sleep(0.05)
    silent = [e for e in _drain(out, 0.2) if e.type == ecodes.EV_KEY]
    fake.write(ecodes.EV_KEY, 57, 1)
    fake.syn()
    fake.write(ecodes.EV_KEY, 57, 0)
    fake.syn()
    events = [e for e in _drain(out, 1.5) if e.type == ecodes.EV_KEY]
    presses = [e.code for e in events if e.value == 1 and e.code not in (42, 54)]
    held = set()
    for e in events:
        (held.add if e.value else held.discard)(e.code)
    ok = (not silent and presses[:7] == [14] * 7 and presses[7:] == EXPECTED_TAIL
          and not held)
    print("во время набора:", "тихо" if not silent else f"{len(silent)} событий (плохо)")
    print("нажатия демона:", presses)
    print("зажатых осталось:", sorted(held))
    print("РЕЗУЛЬТАТ:", "OK" if ok else "FAIL")
    return 0 if ok else 1


def _drain(dev, seconds):
    import select

    deadline = time.time() + seconds
    out = []
    while time.time() < deadline:
        r, _, _ = select.select([dev.fd], [], [], max(0.0, deadline - time.time()))
        if r:
            out.extend(dev.read())
    return out


if __name__ == "__main__":
    raise SystemExit(main())
