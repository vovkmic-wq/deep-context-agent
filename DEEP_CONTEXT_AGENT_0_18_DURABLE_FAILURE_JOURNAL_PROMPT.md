# Production prompt: durable failure journal 0.18.0

## 1. Цель

Реализовать отдельный надёжный журнал ошибок и неудачных запросов Deep Context
Agent. Неуспешный graph turn по-прежнему обязан откатываться, но текст запроса,
безопасная диагностика, попытки провайдеров, tool evidence и результат rollback
не должны исчезать вместе с checkpoint. Журнал нужен для воспроизведения ошибки,
операторского анализа и регрессионных live-тестов.

## 2. Сначала зафиксировать факты

1. Воспроизвести текущий сценарий: Web chat создаёт task, runtime вызывает LLM,
   оба провайдера завершаются ошибкой, checkpoint откатывается, SSE заканчивается
   `failed`, а запроса нет в обычной успешной conversation history.
2. Зафиксировать, какие данные сегодня сохраняются в `context.sqlite3`,
   `checkpoints.sqlite3`, process-memory task registry и server stderr.
3. Не считать raw traceback пользовательским API-контрактом и не ослаблять
   rollback ради диагностики.

## 3. Отдельное хранилище

1. Создать `diagnostics.sqlite3` в `AGENT_DATA_DIR`. Эта БД не входит в
   checkpoint rollback и не индексируется в FTS5/обычную память агента.
2. Включить WAL, foreign keys, `busy_timeout`, короткие транзакции и
   thread-safe доступ с отдельными соединениями/lock policy.
3. Добавить таблицу попыток запросов со стабильным `request_id`:
   `created_at_utc`, `finished_at_utc`, `duration_ms`, `task_id`, `thread_id`,
   `operation_kind`, `source` (`cli`/`web`/`audit`), app version, query mode,
   SHA-256 полного запроса, сохранённый query/preview, исходный byte length,
   truncation flag, provider priority и status.
4. Добавить связанные записи provider attempts: ordinal, provider, model,
   safe error type/code, retry count, duration и active/fallback outcome.
5. Сохранять bounded tool audit: tool, virtual target, status, result count/hash;
   не сохранять file body, raw tool arguments с секретами или physical path.
6. Сохранять checkpoint head до хода, факт rollback attempt/success/failure,
   число удалённых checkpoint/write rows и наличие подтверждённых filesystem
   side effects, которые rollback БД не отменяет.
7. Persistent Web task record обязан хранить sanitized terminal event, чтобы
   status и SSE replay работали после перезапуска процесса.
8. Checkpoint rollback обязан исключать физический остаток удалённого prompt:
   использовать SQLite `secure_delete` и завершать rollback WAL checkpoint,
   затем подтверждать отсутствие fixture secret побайтовым scan всех DB/WAL.

## 4. Жизненный цикл записи

1. До `agent.invoke` атомарно создать запись `in_progress` и зафиксировать
   request hash. Ошибка записи журнала не должна запускать LLM без явной
   безопасной политики: в production fail closed для режима, требующего лог.
2. На успехе перевести запись в `completed` с provider/tool metadata. Обычный
   successful conversation archive остаётся источником истории диалога.
3. На исключении сначала захватить safe exception chain и tool audit, затем
   выполнить checkpoint rollback, после чего атомарно записать `failed` и
   результат rollback. Не терять исходное исключение, если rollback тоже упал.
4. При старте помечать оставшиеся `in_progress` как `interrupted`, сохраняя
   причину `process_restart_or_crash`; не объявлять rollback успешным без факта.
5. Повтор пользователя создаёт новый request ID и `parent_request_id`, не
   перезаписывает предыдущую попытку.

## 5. Режимы хранения текста запроса

1. Ввести `AGENT_FAILURE_LOG_MODE=off|metadata|redacted|full`.
2. Default — `redacted`: хранить bounded текст после deterministic redaction,
   SHA-256 исходных байтов, исходный размер и truncation flag.
3. `metadata` хранит только hash, размер и bounded нейтральный preview без
   пользовательских атомарных значений; `off` не хранит попытки, кроме
   критической process-level ошибки в обычном логе.
4. `full` хранит точный локальный запрос только после явной настройки
   оператора и предупреждения, что там могут быть персональные данные/секреты.
5. Ввести максимальный размер сохранённого prompt; при превышении хранить
   начало/конец, полный SHA-256 и точный `truncated=true`, не утверждая, что
   сохранён полный текст.
6. Redaction обязана закрывать `Authorization`, bearer/basic tokens,
   `*_API_KEY`, известные значения ключей из server environment, marked secrets,
   cookies и распространённые provider-key prefixes. Raw secret запрещён в
   query, exception, provider response, traceback и JSON export.

## 6. Retention и доступ

1. Ввести валидируемые `AGENT_FAILURE_LOG_RETENTION_DAYS`,
   `AGENT_FAILURE_LOG_MAX_ROWS` и `AGENT_FAILURE_LOG_QUERY_MAX_BYTES`.
2. Cleanup выполнять bounded batches после старта/записи; использовать UTC и
   не блокировать chat длительной VACUUM. Ручной purge требует явного действия.
3. CLI-команды: list/show/export/purge с pagination, filters и safe default.
   Полный query показывать только если он реально сохранён и оператор запросил
   `--include-query`.
4. Web API по умолчанию возвращает safe summary. Full/redacted query download
   требует loopback/auth, CSRF для state changes и явного подтверждения.
5. Journal DB, WAL/SHM, export и rotated logs исключить из workspace indexing,
   Git и агентных filesystem tools.

## 7. Логи процесса и корреляция

1. Настроить structured rotating JSONL server log с timestamp UTC, level,
   event code, request/task/thread ID и safe fields. Не использовать `print` как
   единственный production logger.
2. Browser error DTO содержит только stable code, safe message, retryable и
   request ID. Подробная запись остаётся локальной.
3. Exception chain хранит имена типов и sanitized bounded messages. Полный
   traceback допустим только отдельным explicit debug mode и после redaction.
4. Известный Windows Proactor reset 10054 остаётся benign transport event и не
   создаёт failed LLM request; остальные loop exceptions журналируются.

## 8. Миграция и совместимость

1. Schema version и идемпотентные migrations обязательны; повторный старт не
   портит существующие diagnostics.
2. Старые `context.sqlite3`/`checkpoints.sqlite3` открываются без ручной
   миграции и не переносят успешную историю в failure journal.
3. CLI/Web без новых env используют безопасные defaults. Provider failover,
   tool exact-once, workspace policy и rollback сохраняют прежнюю семантику.

## 9. Автоматические тесты

1. Unit: режимы off/metadata/redacted/full, truncation/hash, redaction всех
   типов секретов, retention и migration.
2. Runtime: один провайдер падает/fallback успешен; вся цепочка падает;
   tool успел изменить файл; rollback успешен/падает; journal сохраняет точные
   статусы без raw data.
3. Crash recovery: искусственная `in_progress` после restart становится
   `interrupted`.
4. Web: terminal status/replay после нового app instance читает SQLite;
   browser DTO не содержит query/raw exception; pagination/export защищены.
5. Concurrency: несколько Web tasks пишут без lock errors и не смешивают IDs.
6. Security: journal не индексируется, не доступен как `/workspace`, не входит
   в Git/wheel, secret scan не находит fixture keys.

## 10. Live acceptance

1. Использовать новые `AGENT_WORKSPACE`, `AGENT_DATA_DIR` и thread ID.
2. Выполнить успешный запрос, failover с успешным fallback и контролируемый
   failed request с тестовым не-секретным маркером.
3. Подтвердить: failed turn отсутствует в successful conversation history, но
   присутствует в diagnostics с тем же marker/hash и `rollback_success=true`.
4. Перезапустить Web и проверить task status/SSE terminal replay из SQLite.
5. В redacted-сценарии передать fixture secret и доказать его отсутствие в
   API, DB text dump, JSONL и export.
6. Выполнить Ruff check/format, mypy, pytest, TypeScript/bundle, compileall,
   wheel/install/pip check, secret scan и `git diff --check`.

## 11. Definition of done

Production готов только если неудачный запрос воспроизводимо находится по
request ID после rollback и restart, его причина полезна оператору, секреты не
утекают, retention ограничен, а все offline/live проверки имеют фактический
PASS. До этого запрещено объявлять задачу завершённой или публиковать release.
