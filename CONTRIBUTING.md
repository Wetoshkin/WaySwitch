# Участие в разработке / Contributing

Сообщения об ошибках и предложения принимаются в
[GitHub Issues](https://github.com/Wetoshkin/WaySwitch/issues).
Можно писать по-русски или по-английски.

Укажите дистрибутив, программу, шаги воспроизведения и ожидаемый результат.
Если пропускается исправление, запустите в терминале графического сеанса
`wayswitch doctor --delay 5`, за пять секунд вернитесь в обычное текстовое поле
проблемной программы и оставьте его в фокусе до конца проверки. Приложите весь
отчёт, предварительно удалив личные сведения. Для других проблем достаточно
`wayswitch doctor`. Для примеров используйте вымышленные слова.

Для первого отчёта достаточно диагностики. Перед публикацией удалите пароли,
токены и приватные пути, если они есть в данных, которые вы прикладываете.

## Проверки перед pull request

Требуется Python 3.11 или новее. Из корня репозитория:

```bash
python -m pip install pytest ruff
python -m pytest -q
python -m ruff check .
```

На Linux для тестов XKB нужна `libxkbcommon.so.0`, а для проверки порядка
версий RPM — команда `rpm` со встроенным сравнением версий. В CI обе зависимости
устанавливаются; при их отсутствии соответствующие тесты пропускаются.
Проверка реального ввода проводится отдельно по
[чек-листу](docs/testing-vm.md); юнит-тесты её не заменяют.

Исправляйте причину проблемы и добавляйте небольшой тест для изменения
логики. Сохраняйте локальные импорты `gi`/`evdev` в чистых модулях.
Описание устройства проекта — в [архитектуре](docs/architecture.md).
Порядок подготовки пакета Arch — в [инструкции AUR](docs/aur.md).

## Воспроизводимый эталон и измерения

`tests/fixtures/focus_golden_v1.json` содержит только синтетические данные:
физические коды, Shift/Caps/group, настройки, исключения, локальные словарные
фикстуры и фиксированные ожидаемые решения. Включены детектор, буфер, защита
фокуса и отмена отложенной коррекции. Формат имеет `schema_version=1`, seed,
проверяемую NFC-нормализацию и SHA256 моделей. Runner не переснимает ожидания:
намеренное изменение поведения требует отдельного объяснения diff корпуса.

```bash
python tools/benchmark_focus.py golden --output build/bench/base.json
python tools/benchmark_focus.py compare build/bench/base.json build/bench/candidate.json
python -m pytest -q tests/test_focus_golden.py tests/test_benchmark_focus.py
```

Сравнивайте два checkout на одном стенде с одинаковыми инструментами и
корпусом. При переносе harness в старый checkout сохраняйте его файлы и
фикстуры без изменений. Результат содержит SHA приложения, хеш harness,
настройки, окружение, хеши данных и каждый неуспех. Отличающиеся окружение,
корпус или конфиг делают сравнение несовместимым. Персональные данные и
необработанные результаты не добавляйте в Git.

Для настоящего ввода есть отдельный opt-in инструмент `tools/live_focus.py`.
Он поддерживает GNOME/Wayland с ровно двумя XKB-источниками us/ru, одной
локальной графической сессией текущего пользователя на seat0 и доступным
`udevadm`. Другие окружения этим runner не подтверждаются. Перед каждым
key-down проверяются собственное активное GTK-поле, PID из Shell и активная
разблокированная сессия; виртуальное устройство должно принадлежать seat0.
Отказ прекращает ввод. Отрицательная проверка скрытого окна использует
записывающую заглушку, а не настоящее устройство.

Отдельная проверка контекста создаёт свои обычное и парольное GTK4-поля,
проверяет их защиту и считает настоящие D-Bus-вызовы. Клавиши не отправляет.
Запускайте её в графическом сеансе; для окружения без расширения замените
`present` на `absent`:

```bash
python tests/smoke_shell_context_desktop.py --expect-shell present --output build/bench/context.json
```

Запускайте на отдельном тестовом рабочем столе. Сохраните состояние штатной
службы и раскладки, предусмотрите их восстановление в `finally`/shell trap,
остановите штатный демон и используйте отдельный синтетический конфиг.
Не меняйте пользовательский dconf ради измерений: `XDG_CONFIG_HOME` влияет и
на чтение раскладок GNOME, а отдельный XDG-каталог не изолирует общую шину.
Профилировщик изолирует только файлы состояния WaySwitch через `--state-dir`.

```bash
python tools/profile_focus.py --config build/bench/config.toml --state-dir build/bench/state --output build/bench/stages.json
# В другом терминале того же тестового сеанса:
python tools/live_focus.py --session-id SESSION --app-sha SHA --config build/bench/config.toml --output build/bench/live.json --samples 100 --warmup 5 --check-refusal
```

`SESSION` — ID графического сеанса из `loginctl`, а не SSH-сеанса; `SHA` —
проверенный коммит работающего демона. Конфиг должен отключать раннее
исправление и обучение, включать защиту паролей и использовать изолированные
исключения/словарь. Для завершения профилировщика отправьте его процессу
SIGTERM: обычное завершение демона записывает отчёт. Не запускайте второй
демон рядом со штатной службой на одной сессионной шине.

Runner очищает только собственный TextBuffer, держит виртуальное устройство
открытым до конца операции и читает настоящий результат из GTK. Каждая
успешная коррекция должна оставить ожидаемую раскладку; серия из нескольких
образцов проверяет оба направления. После успешной серии исходная раскладка
восстанавливается ещё одной проверенной коррекцией. После неуспеха новых
нажатий нет; отчёт явно указывает, восстановлена ли раскладка. Внешнее
восстановление после ошибки выполняйте только по достоверному состоянию.

`--idle-seconds 31` и `--idle-seconds 306` задают разные серии первого слова
после простоя; для длинных серий достаточно малого количества попыток,
но по ним нельзя заявлять p99. Сначала выполните smoke корректности, затем
пилот, зафиксируйте прогрев и размер серии до сравнения версий. Не исключайте
таймауты, отказы, потери или дубликаты. `compare` отвергает незавершённые live
серии, даже если уже записанные образцы совпадают.

Время последнего физического пробела → чтение GTK-буфера измеряет доставку
текста с шагом опроса `--poll-ms` (по умолчанию 20 мс), а не появление пикселя.
Отдельно сохраняются время от первой клавиши и стоимость guard.
Для калибровки чтения повторите серию с `--poll-ms 1`, сохранив остальные
параметры; это меняет измеритель, а не быстродействие продукта. Сравнение
разных шагов опроса не доказывает ускорение приложения. Не запускайте
сборки или тесты одновременно с сериями задержки.

p50/p95 выводятся с 20 успешных образцов, p99 — со 100; короткая серия
показывает все значения и максимум.
Профилировщик считает обработку событий, решение, AT-SPI, запрос контекста,
переключение/ожидание и изменение текста. `context` считает вызовы обёртки;
только `shell_ipc.*` считает фактическую отправку соответствующего D-Bus-метода.
Стадии вложены, их длительности нельзя складывать. Суммарные счётчики за
весь сеанс включают прогрев, восстановление и фоновый опрос: их нельзя
напрямую делить на количество измеряемых исправлений. Это инструментированные
серии с дополнительной стоимостью wrappers, отдельно от обычной работы.

`first_main_dispatch_ns` — от начала скрипта до первого callback главного
контекста, без запуска интерпретатора; `imports_ns` выделяет импорты.
RSS/HWM относятся к процессу демона, fd/потоки измеряются при завершении.
GUI/вспомогательные процессы, отрисовка и клиентский ack отмечены
`unavailable`, а не нулём. Полную стоимость запуска измеряйте внешним
монотонным генератором с отдельной проверкой готовности. Таймеры разных
clock domains не вычитайте без проверки их совместимости.

Проверки существующего расширения выполняются отдельно:

```bash
node tests/test_extension_focus.cjs
node tests/test_extension_typing.cjs
```

Live-матрицу GTK3/4, Qt6, браузер/Electron, Writer/Calc и окружения проверяйте
отдельно; отсутствие стенда записывайте «не проверено». Зелёный эталон или
проверка расширения без Shell не означает проверку настоящего композитора.

## Ветки, проверки и выпуск

Перед работой обновите сведения о main и проверьте состояние issue/PR:

```bash
python -I tools/dev_workflow.py preflight
python -I tools/dev_workflow.py install-hook
```

`preflight` получает свежий main и показывает состояние ветки, не меняя
рабочие файлы. Для независимой задачи создавайте отдельную ветку и worktree
от `origin/main`. Изменения по разным причинам разделяйте на коммиты;
один общий баг нескольких issue можно исправлять одним коммитом.
Сообщения на русском описывают результат: `fix: исправление …`,
`docs: уточнение …`, `build: закрепление …`.

Установленный pre-push действует во всех worktree этого клона. Он запрещает
прямой пуш в main, незакоммиченные изменения и отправку ветки без свежего
main. Неизвестные пользовательские hooks не перезаписываются. Весь новый
диапазон истории проверяется на рабочие файлы, образы VM, известные форматы
ключей/токенов и личные пути. Найденное секретное содержимое не печатается.
Проверка не заменяет просмотр diff: все виды персональных данных распознать
автоматически невозможно. Полные тесты и сборки запускаются в CI.

```bash
python -I tools/dev_workflow.py check --base origin/main
```

CI проверяет историю и запускает тесты и сборку каждого пакета. Если после
переписывания ветки предыдущий SHA недоступен, проверяется вся история от
общего предка с main; проверка содержимого не пропускается. Итоговый
`development-gate` должен быть успешен перед слиянием. Он учитывает также
отменённые и пропущенные jobs как неуспех. При добавлении job включайте его
в однострочный список `needs` итогового статуса; проверка политики контролирует
полноту списка. Файлы CI используют отступ в два пробела для ключей jobs.

После изменений обновляйте документацию по фактическому поведению. Различайте
готовый код, пройденные тесты, проверку в настоящем приложении и наличие
исправления в опубликованной версии. Перед слиянием перепроверяйте актуальный
SHA PR и CI; после слияния начинайте следующую задачу от обновлённого main.

Релиз готовится отдельно: зелёный CI, проверка версии и пакетов, публикация,
проверка доступности опубликованных файлов и контрольных сумм. Сообщать
пользователю об исправлении в релизе и закрывать issue следует после этой
проверки. Issue с нерешёнными частями остаётся открытым.

Перед тегом добавьте в `CHANGELOG.md` раздел с точной версией: `release.yml`
собирает из него заметки и ссылку на `docs/PROGRESS.md` этого тега. Раздел
`[Не опубликовано]` автоматически в релиз не попадает; если раздела для тега
нет, публикация останавливается.

## Чек-лист выпуска

Этот чек-лист обязателен для каждого выпуска, а не только для текущего.
Перед тегом и после публикации проверьте по порядку:

1. Сверьте версию в `wayswitch/__init__.py`, `pyproject.toml`, Arch
   `PKGBUILD`/`.SRCINFO`, метаданных расширения GNOME, виджета Plasma и
   Flatpak. Исторические разделы выпусков и тестовые версии не переписывайте.
2. Проверьте и при необходимости обновите GitHub README на русском и
   английском, пользовательскую документацию в `docs/`, `CHANGELOG.md` и
   заметки релиза. Не приписывайте выпуску непроверенные исправления.
3. Проверьте ссылки на версии, команды установки и обновления,
   `packaging/get.sh`, `packaging/install.sh`, поддерживаемые рабочие столы и
   приложения, а также границы реальных проверок. Отдельно проверьте, что
   заявленный перевод действительно входит во все форматы пакета.
4. Если выпуск меняет пользовательские тексты или страницу проекта,
   обновите сайт и его копию установщика; каталог `site/` и инфраструктурные
   файлы не добавляйте в Git или веб-корень. Выкладывайте сайт только после
   проверки опубликованного выпуска.
5. До тега пройдите development-gate и все обязательные проверки версий,
   пакетов и истории. Не ослабляйте их и не добавляйте исключений ради
   выпуска.
6. После публикации проверьте доступность релиза, тег и точный исходный
   коммит, наличие всех пакетов и совпадение SHA256. Только после этого
   сообщайте об исправлении в выпуске и закрывайте issue.
7. Не указывайте модели, ИИ-помощников или другие инструменты как авторов,
   участников или соавторов проекта и его выпуска.

Итоговый `development-gate` должен быть успешен до слияния; порядок проверок
и публикации не сокращается.

Не включайте в Git личные настройки, файлы агентов, рабочие планы/отчёты,
необработанные журналы и тестовые образы. На Windows загрузки и временные
файлы храните на D:, на удалённых стендах — внутри папки проекта; образы VM
остаются на удалённом хосте.

## Источники сборки AppImage

Ubuntu-зависимости закреплены по версиям и SHA256 в
`packaging/appimage/dependencies.json`. Они скачиваются из одного
датированного снимка официального Ubuntu Snapshot Service: обычное зеркало
может удалять заменённые версии. При обновлении зависимостей обновляйте
дату снимка вместе с файлами и суммами; проверка SHA256 остаётся обязательной.

## English

Issues and pull requests in English are welcome. Include your distribution,
application, reproduction steps and expected behaviour. For a missed correction,
run `wayswitch doctor --delay 5` in a terminal in the graphical session. Within
five seconds, focus the affected text field and keep it focused until the check
finishes. Attach the whole report. For other problems, use `wayswitch doctor`.
Review the report for private information before posting. Use sample text to
demonstrate a correction problem.

Run the commands above before submitting a change. Keep platform imports
local in the pure Python modules and include a focused regression test
when changing behaviour. Mention which checks you actually ran, including
whether the change was tested in a live Wayland session.

For deterministic comparisons, run `python tools/benchmark_focus.py golden
--output build/bench/base.json` in each source checkout, then
`python tools/benchmark_focus.py compare build/bench/base.json build/bench/candidate.json`.
Use identical harness/corpus files and the same machine, settings and data.
The synthetic NFC corpus pins physical inputs and expected outcomes; the runner
never regenerates expectations. Keep raw results outside Git.

The opt-in `tools/live_focus.py` runner supports a dedicated GNOME/Wayland
desktop with exactly us/ru, one local graphical login on seat0, and `udevadm`.
It verifies its own active editable GTK field, Shell PID and unlocked active
session before every key-down. The negative focus probe uses a recording
device. Any failure stops injection; partial runs are failures. Every successful
sample checks text readback and the target layout, and multi-sample runs cover
both directions. After a successful series it restores the original layout;
after a failure it reports restoration status without recovery typing.

For context-only checks on a real desktop, run
`python tests/smoke_shell_context_desktop.py --expect-shell present --output
build/bench/context.json`. It creates its own normal/password GTK4 fields and
counts actual IPC without injecting keys. Use `absent` without the extension.

Stop the normal daemon only on a dedicated test desktop, save its state, and
arrange automatic restoration. Use a synthetic config with early correction
and learning disabled, password protection enabled, and isolated exceptions
and dictionary. Run `tools/profile_focus.py --config CONFIG --state-dir STATE
--output STAGES`, then in the same session run `tools/live_focus.py
--session-id SESSION --app-sha SHA --config CONFIG --output RESULT --samples 100
--warmup 5 --check-refusal`. SESSION is the graphical login, not SSH. Stop the
profile process normally with SIGTERM to write its report. Keep GNOME's real
platform configuration readable; redirect only WaySwitch state with `--state-dir`.

Measure first input after idle separately with `--idle-seconds 31` and `306`.
Start with correctness smoke and a pilot; fix sample count/warmup before comparing.
Boundary-key-to-GTK-readback uses `--poll-ms` (default 20 ms) and does not measure
rendered pixels. p50/p95 need 20 successful samples, p99 needs 100. Short series
list all values. Refusals, timeouts, loss and duplicates are never discarded.
`--poll-ms 1` calibrates readback resolution, not product performance; keep all
other parameters fixed. Do not run builds or tests alongside latency series.
`context` counts API invocations; `shell_ipc.*` counts
actual corresponding method sends. Session totals include warmup, restoration
and background polling, not only measured corrections. Profiled stages overlap
and include wrapper overhead. First main-context dispatch excludes interpreter startup; memory is
daemon-only, and unavailable GUI/helper/render/ack metrics are explicit.
Unit tests do not verify a real compositor. Record
the GTK/Qt/browser/Writer/Calc and desktop combinations actually tested.

Every release follows the release checklist above: audit both READMEs, the
user documentation, changelog, release notes and the website when affected;
check version references, installers, supported desktops/apps, real-test
limits and translations; and verify the published release, tag, packages and
SHA256 before claiming a fix. Do not relax the development gate or version
checks, and do not credit models or helper tools as authors.
