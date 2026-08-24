# Статус реализации

Этот файл связывает этапы `IMPLEMENTATION_PROMPT.md` с требованиями
`TECHNICAL_SPEC.md`.

## Default GLM/OpenAI chain 0.11.0 — 2026-08-24

- Основная модель по умолчанию: `zhipu/glm-5.3`.
- Резервная модель по умолчанию: `openai/gpt-5.6-sol`.
- Порядок failover без CLI/env override: `glm,openai`.
- Default Z.AI endpoint: `https://api.z.ai/api/paas/v4`.
- GPT-5.6 Sol Chat Completions tool calls используют `reasoning_effort=none`.
- Локальная `.env.local` синхронизирована без раскрытия ключей.

| Этап | Статус | Проверка | Примечание |
| --- | --- | --- | --- |
| 1. ТЗ и промпты | Завершён | `TECHNICAL_SPEC.md`, `IMPLEMENTATION_PROMPT.md`, `EVIDENCE_INTEGRITY_PROMPT.md` | Глобальный prompt и критерии приёмки синхронизированы с дефектами ручных и live-логов |
| 2. Каркас и провайдеры | Завершён | Unit-тесты конфигурации и фабрики | LM Studio, OpenAI, YandexGPT, DeepSeek и Qwen; provider retry выключен, retry выполняет middleware model call |
| 3. Большой контекст | Завершён | 1 000 001 строка, 200 документов, повторное открытие SQLite | Потоковый FTS5, BM25, соседние чанки и cross-thread archive без загрузки корпуса в окно LLM |
| 4. Deep Agent и CLI | Завершён | Fake-model tool loops, CLI unit/smoke, OpenAI live | `/paste`, `ask --file`, stdin, bounded input, `--no-auto-context` и доверенная runtime identity |
| 5. Безопасность файлов | Завершён | Root-delete, внешний путь, exact read, незавершённая команда | Нет shell/общего `delete`; запрещён root, placeholder и угадывание отсутствующего содержимого |
| 6. Надёжность | Завершён | Retry, точный rollback checkpoints/writes, web timeout | Model-call retry не дублирует tool; failed turn не попадает в следующий запрос; web error структурирован |
| 7. Доказательства | Завершён | Audit runtime/context/files/web и guards | Содержимое/секреты не копируются; текущий веб-факт требует успешного fetch; self-PASS не является приёмкой |
| 8. Финальная проверка 0.2.0 | История | Ruff, 61 passed, 1 planned skip, CLI help, `doctor --live` | Результат предыдущего production-hardening сохранён для трассировки |
| 9. Acceptance reliability 0.3.0 | Завершён | Последовательность, duplicate guard, redaction, PyPI, recursive cleanup | Каждый дефект последнего ручного лога закреплён кодом и регрессионным тестом |
| 10. Финальная проверка 0.3.0 | Завершён | Ruff, 72 passed, 1 planned skip, CLI help, `doctor --live` | Изолированный OpenAI `gpt-5-nano` smoke: один `runtime_info`, точные provider/model, ключ не выведен |
| 11. Acceptance completion 0.4.0 | Завершён | Repeat policy, forbidden read, manifest parser/evaluator | Exact counts, ordered/forbidden events и negative statuses проверяются независимо от LLM |
| 12. Финальная проверка 0.4.0 | Завершён | Ruff, 81 passed, 1 planned skip, package/CLI/doctor/live | OpenAI `gpt-5-nano`: один `runtime_info`, runtime manifest — 2 PASS, 0 FAIL |
| 13. Acceptance correctness 0.5.0 | Завершён | Sentence scope, allowed-unlisted, BLOCKED, completion gate | Runtime завершает только dependency-ready cleanup/postconditions и не расширяет filesystem scope |
| 14. Финальная проверка 0.5.0 | Завершён | Ruff, 91 passed, 1 planned skip, package/CLI/doctor/full live/restart | `gpt-5-nano`: полный manifest — 32 PASS; новый процесс/thread — 2 PASS и 4 context results |
| 15. Evidence integrity 0.6.0 | Завершён | Structured audit, manifest v2, cardinality/exact-once guards | Hash/count predicates независимы от LLM; устаревший exact-once повтор не исполняется |
| 16. Финальная проверка 0.6.0 | Завершён | Ruff, 97 passed, 1 planned skip, package/CLI/doctor/full live/restart | `gpt-5-nano`: полный v2 manifest — 32 PASS; restart — ровно один call, 4 results, 2 PASS |
| 17. Ozon hardening 0.7.0 | Завершён | Root-scope, index-filter и listing regression tests | Общий root не блокирует child-read; generated/browser/cache пути отсечены; listing ограничен 20/50 |
| 18. Финальная проверка 0.7.0 | Завершён | Ruff, 100 passed, 1 planned skip, package/CLI/doctor/full live/restart | `gpt-5-nano`: root-scope read успешен; полный manifest — 32 PASS; restart — 4 results и 2 PASS |
| 19. BOM text patch 0.7.1 | Завершён | Ruff, 102 passed, package/pip/doctor live, UTF-16/UTF-32 regression | Обнаруженный Ozon UTF-16LE отчёт теперь индексируется; clean-DB повтор выполняется после публикации |
| 20. Explicit tool budgets 0.8.0 | Завершён | Ruff, 105 passed, package/pip/doctor live, parser/tool-loop | Per-tool и total budget скрывают exhausted tools; stale provider-call не исполняется и не аудируется |
| 21. Empty toolset patch 0.8.1 | Завершён | OpenAI 400 regression, Ruff, 106 passed, package/pip/doctor live | Пустой toolset не отправляет tool-only model settings; повтор Ozon выполняется после публикации |
| 22. Middleware order patch 0.8.2 | Завершён | Ruff, 107 passed, package/pip/doctor live, композиционный regression | Sequential normalizer видит окончательный toolset; чистый Ozon-run выполняется после публикации |
| 23. Multiline budget patch 0.8.3 | Завершён | Ozon prompt regression, Ruff, 107 passed, package/pip/doctor live | Общий limit 15 доказан live; per-tool 2 распознаётся через перенос строки |

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

Результат 0.3.0 выше сохранён как история. Проверка 0.4.0 от 2026-08-23
выполнена после анализа полного ручного audit из 26 tool calls:

- повторные идентичные runtime/context/listing/web-вызовы блокируются до
  повторного исполнения и получают `denied` в текущем audit;
- третье чтение неизменённого пути блокируется; успешная мутация самого пути
  или recursive removal родителя открывает новую проверяемую версию;
- basename из явного «не читай/не открывай/не показывай» сопоставляется только с
  явно указанным полным workspace-путём и запрещает `read_file` для decoy;
- строгий bounded manifest parser отклоняет неизвестные поля/tools/status,
  неверные зависимости и превышение размера;
- manifest evaluator сверяет все attempts, точные количества, ordered required
  events, forbidden calls и ожидаемые `denied/error/not_found`; один audit event
  не используется дважды, а необъявленный tool является FAIL;
- канонический `acceptance-prompt.txt` содержит 21 ordered event, один forbidden
  decoy read, точные количества для 11 tool types и одну restart-проверку;
- contract-тест канонического manifest подтверждает предусмотренную полную
  последовательность cleanup и post-delete reads без выполнения платного API;
- `ruff check --no-cache .` — успешно;
- `ruff format --check --no-cache .` — 27 файлов отформатированы;
- `pytest -ra` — 81 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- editable wheel `deep-context-agent==0.4.0` собран и установлен, `pip check` не
  обнаружил конфликтов, CLI `--help` и безопасный OpenAI doctor прошли;
- изолированный live-smoke использовал
  `v040-ff3825cf02f8410bb7c7530eeb087bf2`, новый workspace/data/thread и
  `gpt-5-nano`; audit зафиксировал ровно `runtime_info: success`, а runtime
  manifest — `2 PASS, 0 FAIL, 0 PENDING` без вывода API-ключа.

Версия 0.4.0 считается production-ready для заявленного локального
однопользовательского CLI. Полный канонический live acceptance остаётся
отдельным операторским прогоном из-за числа платных model calls; его лог теперь
оценивается runtime автоматически. Многопользовательский сетевой сервис по-
прежнему требует отдельной процессной изоляции, аутентификации и координации
записей и не входит в область данного ТЗ.

Результат 0.4.0 выше сохранён как история. Проверка 0.5.0 от 2026-08-23
выполнена после итеративных диагностических live-прогонов и финального чистого
прогона:

- отрицательная инструкция ограничена своим предложением: `decoy.txt` остаётся
  запрещённым, а положительно указанный `result.txt` читается;
- `write_todos` разрешается только как явно объявленный unlisted planning-tool,
  а функциональные tools сохраняют exact counts;
- первичный missing event получает FAIL, зависимые события — BLOCKED;
- bounded completion middleware продолжает только доказанную ordered
  cleanup/postcondition цепочку, не начинает root event, не читает запрещённый
  путь, не выходит из `/workspace/` и не превышает exact count;
- после root event provider получает имя следующего prose-разрешённого tool, а
  dependency-ready `read_file`/`remove_path` — точный ordered target; JSON
  manifest без независимого prose-разрешения не инициирует действие;
- машинный manifest исключён из классификации текущих web-фактов, поэтому его
  поле `version` не создаёт ложный web FAIL для локального context search;
- `ruff check --no-cache .` и `ruff format --check --no-cache .` — успешно;
- `pytest -ra` — 91 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- editable package `deep-context-agent==0.5.0`, `pip check`, CLI `--help` и
  безопасный OpenAI doctor — успешно;
- полный изолированный live acceptance использовал run
  `20260823-125545`, `gpt-5-nano`, `AGENT_MODEL_TEMPERATURE=0` и новые
  workspace/data/thread; runtime manifest дал `32 PASS, 0 FAIL, 0 BLOCKED,
  1 PENDING`, а exact counts совпали для всех десяти функциональных tool types;
- физическая проверка после live-run подтвердила отсутствие тестового каталога,
  root sentinel и outside placeholder при сохранённом prompt-файле;
- новый процесс с тем же `AGENT_DATA_DIR` и thread
  `restart-v050-release-20260823-125545` вызвал `search_context` ровно один раз,
  получил `4 result(s)` и runtime verdict `2 PASS, 0 FAIL, 0 BLOCKED`; restart
  PENDING тем самым закрыт внешней проверкой.

Версия 0.5.0 считается production-ready в границах локального
однопользовательского CLI. Многопользовательский сетевой сервис требует
отдельной процессной изоляции, аутентификации и координации одновременных
записей и не входит в область данного ТЗ.

Результат 0.5.0 выше сохранён как история. Проверка 0.6.0 от 2026-08-23
выполнена после устранения трёх дефектов restart-аудита:

- `ToolAuditEntry` содержит безопасные `result_count` и `content_sha256`;
  SHA-256 вычисляется из фактических байтов workspace-файла и не раскрывает
  его тело;
- manifest v2 строго валидирует `min_results` и `content_sha256`, сохраняет
  совместимость v1 и запрещает evidence-предикаты в forbidden events;
- прямой вопрос о количестве получает детерминированный ответ из ToolMessage;
  unit tool-loop заменил ошибочный текст модели `0` фактическим числом `1`;
- exact-once middleware удаляет уже вызванный tool из следующего model request;
  regression с повторным provider call дал одну audit-запись без `denied`;
- canonical `acceptance-prompt.txt` v2 проверяет SHA-256 начального/изменённого
  result и root sentinel, а `restart-acceptance-prompt.txt` требует
  `search_context: 1` и `min_results=1`;
- `ruff check --no-cache .` и `ruff format --check --no-cache .` — успешно;
- `pytest -ra` — 97 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- editable package `deep-context-agent==0.6.0`, `pip check`, CLI `--help`,
  безопасный OpenAI doctor и `doctor --live` — успешно;
- полный изолированный live acceptance использовал run `20260823-221609`,
  `gpt-5-nano`, новые workspace/data/thread и manifest v2; runtime дал
  `32 PASS, 0 FAIL, 0 BLOCKED, 1 PENDING`, все exact counts и hash-предикаты
  совпали;
- PowerShell-проверка после live-run подтвердила отсутствие тестового каталога,
  root sentinel и outside placeholder;
- restart с тем же `AGENT_DATA_DIR` и новым thread вызвал `search_context`
  ровно один раз, вернул `4 result(s)` и manifest verdict
  `2 PASS, 0 FAIL, 0 BLOCKED, 0 PENDING`; повторный denied отсутствует.

Версия 0.6.0 считается production-ready в границах локального
однопользовательского CLI. Многопользовательский сетевой сервис по-прежнему
требует отдельной процессной изоляции, аутентификации, централизованного аудита
и координации одновременных записей и не входит в область данного ТЗ.

Результат 0.6.0 выше сохранён как история. Проверка 0.7.0 от 2026-08-23
выполнена после устранения дефектов первого Ozon-эксперимента:

- общий `/workspace/` исключён из exact-file allowlist, при этом точный
  дочерний путь по-прежнему ограничивает чтение;
- индексатор до обхода отсекает generated/cache/coverage, Playwright и browser-
  profile пути с регистронезависимым сопоставлением;
- `list_context_sources` ограничен 20 источниками по умолчанию и 50 максимум;
- Ozon-промпт сокращён до одного доказанного дефекта, узких retrieval-запросов
  и не более 15 функциональных tool calls;
- `ruff check --no-cache .` и `ruff format --check --no-cache .` — успешно;
- `pytest -ra` — 100 passed, 1 planned skip; skip относится только к системному
  запрету Windows на создание symlink для текущей учётной записи;
- editable package `deep-context-agent==0.7.0`, `pip check`, CLI `--help`,
  безопасный OpenAI doctor и `doctor --live` — успешно;
- отдельный live root-scope ход нашёл и прочитал `/workspace/pyproject.toml`
  после общего упоминания `/workspace/`, вернул `0.7.0` без denied events;
- полный изолированный live acceptance использовал run
  `20260823-231533`, `gpt-5-nano` и новые workspace/data/thread; runtime дал
  `32 PASS, 0 FAIL, 0 BLOCKED, 1 PENDING`, а cleanup завершился;
- новый процесс/thread на той же SQLite-БД вызвал `search_context` ровно один
  раз, получил `4 result(s)` и закрыл restart-аудит с
  `2 PASS, 0 FAIL, 0 BLOCKED, 0 PENDING`.

Версия 0.7.0 считается production-ready в границах локального
однопользовательского CLI. Проверка Ozon выполняется отдельно на временной
копии и чистой БД; её изменения не переносятся в исходный проект автоматически.

Patch 0.7.1 добавлен после первого clean-DB индексирования Ozon: файл
`analysis_30_days.txt` имеет корректный UTF-16LE BOM, но 0.7.0 принимал его
NUL-байты за binary. После переноса BOM-detection перед binary guard выполнены
Ruff и format-check, `pytest -ra` дал 102 passed и 1 planned symlink skip,
editable package сообщает 0.7.1, `pip check` и OpenAI `doctor --live` успешны.

Hardening 0.8.0 добавлен после первого LLM-хода на чистой Ozon-БД. Модель
выполнила четыре `search_context` при prose-максимуме два и превысила общий
максимум 15 попыток. Новая model middleware парсит русские/английские числовые
ограничения, считает фактический current-turn audit, удаляет exhausted tools из
следующего model request и подавляет stale provider-call. Regression охватывает
парсер, per-tool и total budget; полный прогон дал Ruff PASS, 105 passed,
1 planned symlink skip, editable 0.8.0, чистый `pip check` и успешный OpenAI
`doctor --live`.

Первый Ozon live-run 0.8.0 остановился после ровно 15 audit events, доказав
работу total budget, но OpenAI вернул 400: `parallel_tool_calls` запрещён без
`tools`. Patch 0.8.1 удаляет этот model setting и сбрасывает `tool_choice` при
пустом toolset; regression проверяет итоговый request непосредственно.

Повтор 0.8.1 показал, что sequential normalizer был внешним middleware и видел
старый непустой toolset до budget narrowing. В 0.8.2 порядок изменён так, чтобы
sequential normalizer выполнялся после toolset policies; композиционный тест
проверяет именно этот полный путь до model handler.

Ozon live-run 0.8.2 успешно завершился без 400 и остановился ровно на 15 audit
events, однако сделал три `search_context`: строка prompt была перенесена между
«не более двух узких» и именем tool. Patch 0.8.3 допускает такой перенос только
внутри того же предложения и закрепляет точную формулировку regression-тестом.

Финальный clean-DB Ozon-run 0.8.3 описан в `OZON_EXPERIMENT_REPORT.md`:
индексация 230 файлов/582 чанков с exit 0, live exit 0, ровно 15 audit events и
2 `search_context`, temp worktree чист. Внешние проверки Ozon: Ruff lint PASS,
57 pytest PASS; один `ruff format --check` drift в exporter существовал уже в
baseline. Исходный Ozon-проект не изменялся.

## GLM-5.2 / Zhipu integration 0.9.0 — 2026-08-24

- Добавлены канонический provider `zhipu` и CLI-алиас `glm`; оба разрешаются в
  runtime identity как `zhipu` с моделью `glm-5.2`.
- Поддержаны официальные `ZAI_API_KEY`, `ZAI_MODEL`, `ZAI_BASE_URL` и
  совместимые `ZHIPU_*` aliases. Стандартный endpoint и Coding Plan endpoint
  выбираются конфигурацией, секрет не хранится в репозитории.
- Thinking включён через `extra_body`, а недопустимая для Zhipu температура
  отклоняется до сетевого запроса.
- Недокументированный Zhipu-параметр `parallel_tool_calls` не отправляется;
  defensive response normalization по-прежнему оставляет один tool call.
- `ruff check --no-cache .` — PASS; `ruff format --check --no-cache .` — 31
  файл отформатирован; `pytest -ra` — 122 passed, 1 planned Windows symlink
  skip.
- CLI `doctor` для `--provider glm` и `--provider zhipu` — PASS с тестовым
  несекретным ключом; оба показали модель `glm-5.2` и официальный base URL.
- Изолированно собран `deep_context_agent-0.9.0-py3-none-any.whl`, SHA-256
  `52C2986B90E7400DB8F9D6E6641241FC81344280B200AD6F6872E437CEFAFDB8`.
- Реальный `doctor --live` не выполнялся: `ZAI_API_KEY`/`ZHIPU_API_KEY` не
  настроен ни в процессе, ни в `.env.local`. Это явное внешнее условие, а не
  дефект кода; live-проверка должна быть выполнена после локальной установки
  ключа без его вывода.

## Provider priority and failover 0.10.0 — 2026-08-24

- Добавлены взаимоисключающие `--provider` и `--providers`, а также
  `AGENT_PROVIDER_PRIORITY`; порядок списка задаёт порядок model calls.
- Конфигурация отклоняет пустые элементы, неизвестные имена и канонические
  дубликаты aliases. Пустая env-строка означает отключённую цепочку и сохраняет
  одиночный `AGENT_PROVIDER`.
- `ProviderFailoverMiddleware` даёт каждому провайдеру настроенные model-call
  retries, затем безопасно переключается на следующий. Успешный fallback
  закрепляется на текущий ход и сбрасывается к primary перед следующим ходом.
- Failover расположен только в model-call middleware: уже выполненные tools не
  переигрываются. Runtime identity и `runtime_info` отражают активный provider,
  полный приоритет, число failover и неуспешные имена без API-ответов/ключей.
- Одиночный provider сохраняет прежнее исключение для обратной совместимости;
  полная ошибка много-провайдерной цепочки содержит только provider/model и
  безопасный тип исключения.
- `ruff check --no-cache .` — PASS; `ruff format --check --no-cache .` — 31
  файл отформатирован; `pytest -ra` — 135 passed, 1 planned Windows symlink
  skip.
- CLI doctor с тестовыми ключами корректно показал приоритет
  `openai,zhipu,deepseek`. Реальный `doctor --live` на существующем локальном
  OpenAI key и цепочке `openai,lmstudio` вернул `live_provider=openai` и `OK`.
- Изолированный runtime live-test с приоритетом `lmstudio,openai`, новой БД и
  timeout 15 секунд подтвердил настоящий failover:
  `answer=FAILOVER_RUNTIME_OK`, `active_provider=openai`, `failover_count=1`,
  `failed_providers=lmstudio`.
- Собран `deep_context_agent-0.10.0-py3-none-any.whl`, SHA-256
  `35A0766EF1CFE06D3599649CD1C9F646C077749EFDF8BFEE85731D2D627AA5E8`.
