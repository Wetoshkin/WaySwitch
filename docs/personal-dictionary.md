# Персональный словарь

В Параметрах на странице «Персональный словарь» можно добавить правильное
слово и его язык (русский или английский), изменить или удалить запись.
Написание в другой раскладке определяется по системной карте клавиш:
например, добавление английского `ubuntu` позволяет исправлять `гигтег`.
Это пары раскладки, а не произвольные подстановки текста или сниппеты.
Кнопка «Никогда не исправлять автоматически» добавляет слово в существующие
исключения; они важнее персонального словаря. Для сохранения буквального
`ghbdtn` достаточно добавить именно `ghbdtn` в «Исключения → Слова».

Запоминание после ручного исправления отдельно включается в Параметрах:
`learn_after_converts = 0` по умолчанию выключает его; 1–5 задаёт порог.
Учитываются только успешные ручные исправления одного слова в подтверждённом
незащищённом поле. Пароли, терминалы, неизвестные поля, автоматические
исправления и исправление фразы не обучают. Порог считается в памяти:
до 256 разных слов, записи старше семи дней забываются, перезапуск обнуляет
счётчики. Сохранённые слова остаются в словаре и при выключении обучения.

Немедленный обратный ручной жест в том же поле отменяет вклад предыдущего
исправления; если именно оно добавило слово, добавление удаляется. Это не
перехват системного Ctrl+Z. Уже существовавшие записи не удаляются. При
обнаруженном изменении файла удаление отменяется, чтобы не затереть чужую правку.

Файл `~/.config/wayswitch/learned_words.txt` содержит строки `язык<TAB>слово`,
например `en<TAB>ubuntu`. Максимум — 5000 слов по 64 символа; поддерживаются
буквы языка, внутренние дефисы и апострофы. Регистр нормализуется, «ё» сохраняется.
Частоты и триграммы не меняются. Файл перечитывается на границе слова;
в окне есть «Обновить список слов». Записи GUI и демона используют общую
блокировку; перед заменой файл перечитывается. Битые строки не мешают чтению
остальных, но перед сохранением их нужно исправить вручную. Ошибка чтения
сохраняет прежнюю модель.

Запись атомарна, предыдущий файл остаётся в `learned_words.bak`. Внешний
редактор, не использующий блокировку, всё ещё может попасть в короткое окно
между последней проверкой и заменой: абсолютная защита от такой гонки не
обещается. При аварийном завершении может остаться `learned_words.lock`;
удаляйте его только после закрытия настроек и остановки демона.

## Переход настроек и откат

Схема конфига 1 читается; сохранение использует схему 2. Перед первым
переходом исходный файл сохраняется в `config.toml.before-schema-2`.
Для отката на версию, которая не понимает схему 2, восстановите эту копию.
Обучение после двойного Shift учитывается после завершения серии жеста;
тройной Shift не сохраняет промежуточное исправление последнего слова.

## English

Double-Shift learning waits for the gesture series to finish; triple Shift
never saves its intermediate single-word correction.

Preferences → Personal dictionary lets you add, edit and remove a correct
word and its language (Russian or English). The spelling in the other layout
follows the system keymap: adding English `ubuntu` enables `гигтег` → `ubuntu`.
These are layout pairs, not arbitrary text snippets. “Never correct
automatically” uses the existing exceptions, which take priority. To keep
literal `ghbdtn`, add `ghbdtn` under Exceptions → Words.

Learning from manual corrections is separately opt-in: `learn_after_converts`
defaults to 0 (off); 1–5 sets the threshold. Only successful manual single-word
conversions in confirmed, unprotected text fields count. Passwords, terminals,
unknown fields, automatic corrections and phrase conversions never teach words.
Counts are kept in memory for at most 256 words with a seven-day lifetime;
restarting clears the counts. Turning learning off keeps saved dictionary words.

An immediate reverse manual gesture in the same field reverses the previous
learning contribution and removes an entry created by that exact conversion.
Existing entries stay. This does not intercept system Ctrl+Z; an external file
change cancels removal to preserve other edits.

`~/.config/wayswitch/learned_words.txt` uses `language<TAB>word` lines, e.g.
`en<TAB>ubuntu`: up to 5000 words, 64 characters each, with letters and internal
hyphens/apostrophes. Case is normalized; Russian ё is preserved. Frequency lists
and n-grams are unchanged. Edits are picked up at word boundaries; the settings
page also has a refresh action. Invalid lines are skipped on read but must be
repaired before saving. Read failures keep the previous model.

GUI and daemon writes share a lock, reread the file and replace it atomically;
one previous copy is kept in `learned_words.bak`. An external editor can still
race the last check and replacement. A crash may leave `learned_words.lock`;
remove it only after closing Preferences and stopping the daemon.
Configuration schema 1 remains readable; saving writes schema 2 and keeps the
original in `config.toml.before-schema-2`. Version 1.2.0 cannot read schema 2,
so restore that backup before downgrading.
