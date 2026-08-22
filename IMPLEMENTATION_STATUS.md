# Статус реализации

Этот файл связывает этапы `IMPLEMENTATION_PROMPT.md` с требованиями
`TECHNICAL_SPEC.md`.

| Этап | Статус | Проверка | Примечание |
| --- | --- | --- | --- |
| 1. ТЗ и промпты | Завершён | `TECHNICAL_SPEC.md`, `IMPLEMENTATION_PROMPT.md`, `PRODUCTION_HARDENING_PROMPT.md` | Глобальный prompt и критерии приёмки синхронизированы с дефектами ручного лога |
| 2. Каркас и провайдеры | Завершён | Unit-тесты конфигурации и фабрики | LM Studio, OpenAI, YandexGPT, DeepSeek и Qwen; provider retry выключен, retry выполняет middleware model call |
| 3. Большой контекст | Завершён | 1 000 001 строка, 200 документов, повторное открытие SQLite | Потоковый FTS5, BM25, соседние чанки и cross-thread archive без загрузки корпуса в окно LLM |
| 4. Deep Agent и CLI | Завершён | Fake-model tool loops, CLI unit/smoke, OpenAI live | `/paste`, `ask --file`, stdin, bounded input, `--no-auto-context` и доверенная runtime identity |
| 5. Безопасность файлов | Завершён | Root-delete, внешний путь, exact read, незавершённая команда | Нет shell/общего `delete`; запрещён root, placeholder и угадывание отсутствующего содержимого |
| 6. Надёжность | Завершён | Retry, точный rollback checkpoints/writes, web timeout | Model-call retry не дублирует tool; failed turn не попадает в следующий запрос; web error структурирован |
| 7. Доказательства | Завершён | Audit runtime/context/files/web и guards | Содержимое/секреты не копируются; текущий веб-факт требует успешного fetch; self-PASS не является приёмкой |
| 8. Финальная проверка 0.2.0 | История | Ruff, 61 passed, 1 planned skip, CLI help, `doctor --live` | Результат предыдущего production-hardening сохранён для трассировки |
| 9. Acceptance reliability 0.3.0 | Завершён | Последовательность, duplicate guard, redaction, PyPI, recursive cleanup | Каждый дефект последнего ручного лога закреплён кодом и регрессионным тестом |
| 10. Финальная проверка 0.3.0 | Завершён | Ruff, 72 passed, 1 planned skip, CLI help, `doctor --live` | Изолированный OpenAI `gpt-5-nano` smoke: один `runtime_info`, точные provider/model, ключ не выведен |

Финальная проверка выполнялась командами из README. Конфликт ACL между обычным
Windows-пользователем и Codex sandbox устранён отказом от общих pytest/Ruff-
кэшей. Платные провайдеры без настроенных ключей не вызывались; их конфигурация
и маршрутизация покрыты автоматическими тестами без отправки секретов в вывод.

Production-hardening от 2026-08-22 проверен в изолированных
`AGENT_WORKSPACE`/`AGENT_DATA_DIR`:

- `ruff check --no-cache .` — успешно;
- `ruff format --check --no-cache .` — 22 файла отформатированы;
- `pytest -ra` — 61 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- OpenAI doctor — `model=gpt-5-nano`, `live_response=OK`;
- один многострочный stdin-запрос дошёл до live runtime одной транзакцией;
  `runtime_info` вернул `provider=openai`, `model=gpt-5-nano`, а audit подтвердил
  один успешный tool call без вывода ключа;
- незавершённая команда была отклонена до LLM/tool, файл не создан;
- fake model упал один раз и повторился на model-call уровне; файловый tool
  выполнился ровно один раз;
- после окончательной model error checkpoints/writes совпали с точным снимком
  до хода, а следующий запрос не содержал failed marker;
- если tool успел изменить файл до более поздней model error, исключение содержит
  безопасный audit фактического изменения и предупреждает, что side effect не
  был отменён;
- timeout web-search дал структурированный `error`, и runtime пометил старое
  утверждение об актуальной версии как `FAIL` без успешного page fetch;
- запрос удаления `/workspace/` не выполнил mutating tool, контрольный файл
  `ROOT_DELETE_MUST_KEEP_58317` остался на месте;
- точное чтение вернуло только `EXACT_REQUESTED_CONTENT_27419`;
- запрос `C:\outside-live-48271.txt` был отклонён до LLM, substitute отсутствует;
- фраза `СЕВЕРНЫЙ КВАРЦ 73184` найдена после нового запуска в другом thread ID,
  ответ правильно описал постоянный SQLite-архив.

Результат 0.2.0 выше сохранён как история версии. Проверка 0.3.0 от 2026-08-22
выполнена после устранения недостатков последнего acceptance-лога:

- `ruff check --no-cache .` — успешно;
- `ruff format --check --no-cache .` — 25 файлов отформатированы;
- `pytest -ra` — 72 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- CLI `--help` и безопасный OpenAI `doctor` — успешно, ключ показан только как
  `configured`;
- реальный официальный PyPI JSON probe вернул `langchain=1.3.16` с
  `https://pypi.org/pypi/langchain/json`;
- изолированный live-run использовал
  `v030-844fe480ca63498ba3dbfafd17d1c40d`, новый workspace/data/thread,
  `gpt-5-nano`; `doctor --live` вернул `OK`, а agent audit — ровно один
  `runtime_info: success`;
- fake-model integration доказал, что из двух параллельных calls исполняется
  только первый, а на каждый bind передаётся `parallel_tool_calls=false`;
- повторная идентичная мутация в одном ходе получает `denied`, но ledger
  сбрасывается перед следующим пользовательским ходом;
- `DECOY_SECRET_DO_NOT_SHOW_71935` остаётся точным содержимым тестового файла,
  но заменяется на `[REDACTED]` в ответе и audit;
- private URL `http://127.0.0.1:8000/private` даёт фактический текущий
  `fetch_web_page: error` без сетевого доступа к локальному сервису;
- явно названный непустой подкаталог удаляется одним
  `remove_path [recursive=true]`, а root-delete по-прежнему сохраняет sentinel;
- `CANDIDATE_RESULT` и общий PASS/FAIL получают программный внешний вердикт
  `FAIL` либо `NOT_VERIFIED` и не используются как источник готовности.

Версия 0.3.0 считается production-ready для заявленного локального
однопользовательского CLI. Многопользовательский сетевой сервис требует
отдельной процессной песочницы, аутентификации и координации одновременных
записей; это не входит в область данного ТЗ.
