# Участие в разработке / Contributing

Сообщения об ошибках и предложения принимаются в
[GitHub Issues](https://github.com/Wetoshkin/WaySwitch/issues).
Можно писать по-русски или по-английски.

В отчёте укажите версию WaySwitch, дистрибутив, рабочий стол, раскладки,
шаги воспроизведения и ожидаемый результат. Приложите вывод
`wayswitch doctor`, предварительно удалив личные сведения. Для ошибок
исправления используйте вымышленные слова и предложения.

Не публикуйте пароли, токены, приватные пути и необработанные журналы:
`--verbose` и `dry-run` могут содержать введённый текст.

## Проверки перед pull request

Требуется Python 3.11 или новее. Из корня репозитория:

```bash
python -m pip install pytest ruff
python -m pytest -q
python -m ruff check .
```

На Linux для тестов XKB нужна `libxkbcommon.so.0`. На Windows эти тесты
пропускаются. Проверка реального ввода проводится отдельно по
[чек-листу](docs/testing-vm.md); юнит-тесты её не заменяют.

Исправляйте причину проблемы и добавляйте небольшой тест для изменения
логики. Сохраняйте локальные импорты `gi`/`evdev` в чистых модулях.
Описание устройства проекта — в [архитектуре](docs/architecture.md).
Порядок подготовки пакета Arch — в [инструкции AUR](docs/aur.md).

## English

Issues and pull requests in English are welcome. Include your WaySwitch
version, distribution, desktop, keyboard layouts, reproduction steps and
expected behaviour. Remove private information from diagnostic output;
verbose and dry-run logs can contain typed text. Use sample text to
demonstrate a correction problem.

Run the commands above before submitting a change. Keep platform imports
local in the pure Python modules and include a focused regression test
when changing behaviour. Mention which checks you actually ran, including
whether the change was tested in a live Wayland session.
