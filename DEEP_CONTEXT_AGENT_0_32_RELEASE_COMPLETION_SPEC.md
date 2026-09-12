# ТЗ 0.32: завершение production-приёмки

Дата: 2026-09-09; актуализация 2026-09-12. Статус: **R1–R6 реализованы,
приёмка 0.32.0 C01–C18 PASS**. Факты и финальный пакет:
[матрица C01–C18](RELEASE_COMPLETION_0_32_ACCEPTANCE.md).
Дополняет [основное ТЗ lifecycle](TASK_LIFECYCLE_VERIFICATION_CONTEXT_TECHNICAL_SPEC.md)
L01–L13/A01–A34; не отменяет ограничения безопасности и исходные критерии.
Порядок реализации: [пошаговый промпт](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_PROMPT.md).

## 1. Baseline и границы

Опубликованная ветка `codex/task-lifecycle-0.32`, baseline `9edcece`:
449 passed, 1 skipped; Ruff/mypy PASS; контролируемые lifecycle-тесты и live
continuity PASS. Эти результаты не являются полным production PASS.
Исходная версия пакета 0.31.0; после runtime gate подготовлена 0.32.0.
Не повторять уже выполненные правки без проверки кода.
В работе использовать отдельные временные workspace/БД, не перезапускать рабочий
сервер и не изменять реальный Ozon. Не раскрывать ключи; использовать существующие
настройки только в разрешённых live-вызовах. Не устанавливать зависимости глобально.

## 2. R1 — полный сохраняемый контекст верификации

Ввести версионируемый неизменяемый VerificationContext, проверяемый сервером:
context_id, task/job/run/attempt identifiers, trusted root и источник его выбора,
scope/capabilities snapshot, code revision, manifest/config/lock SHA-256,
interpreter launch reference, sys.prefix/base_prefix, Python/tool/dependency
versions и origin проектных пакетов, required check plan, capture time.
Хранить контекст один раз; receipts ссылаются на ID и canonical hash.

VERIFY, model tool, CLI/Web, REPAIR и recovery используют один resolver/contract.
Сохранённый валидный root имеет приоритет; явный конфликт создаёт новую revision
после проверки scope. Не выбирать workspace по умолчанию при неоднозначности.
Monorepo использует отдельные contexts по проектам. Обход — с pruning и курсором,
лимитами entries/time; небезопасные пути отклоняются без fallback.

Preflight проверяет origin установленных проектных пакетов: нельзя незаметно
проверять другую checkout-копию. Если определить пакет нельзя, фиксировать причину,
не фабриковать origin. Метаданные fingerprint собираются фиксированным bounded
probe выбранного интерпретатора, без произвольного shell и вывода секретов.
До принятия PASS повторно проверять код, manifest/config/lock и окружение.
Drift делает evidence stale; REPAIR создаёт новую code/context revision и recheck.
Исторические root-less receipts не повышаются до актуального PASS.

Уточнение реализованного R1 (2026-09-12): schema 2 неизменяема, canonical SHA-256
равен content-addressed ID. Nullable task/job/request IDs допустимы только когда
соответствующей сущности нет (прямой runner); run/attempt IDs всегда новые.
Capability snapshot описывает запуск проверок, не право переписывать проект.
Загрузка контекста не меняет действующие runtime permissions. Schema 1 сохраняется
для истории и bounded выбора прежнего root, но не даёт текущий PASS без preflight.
Origin проверяется для regular/src/namespace и объявленных setuptools/Poetry roots;
необнаруженные пакеты отмечаются no_packages_discovered, без выдуманного origin.
Неизвестный build backend не объявляется автоматически проверенным.

Root discovery сохраняет frontier, offsets и directory identity/mtime в root_discovery.
Одна страница имеет entries/time ceiling, весь scan — 1 000 000 записей и 2 MB
checkpoint; общего списка файлов в model context нет. Смена дерева инвалидирует
snapshot. Portable scandir требует bounded replay текущей директории после restart;
если этот replay не укладывается, вернуть точный blocker, а не вечный zero-progress.
Обход выполняется без долгого SQLite writer lock, commit использует CAS.
Interrupted/cancelled страница не уничтожает предыдущий валидный commit.
Контекст/scan database следует хранить в исключённом data dir вне сканируемого
project root; изменение директории БД не должно менять scan snapshot.

## 3. R2 — полный план проверок и доказуемый PASS

Required plan: Ruff check → Ruff format --check → pytest → mypy, если настроен
или явно требуется → compileall. Сохранять план до исполнения и учитывать
явно ограниченный запрос пользователя: частичный набор не означает project PASS.
Для ненастроенного optional mypy возвращать not_applicable с причиной.
Для обязательного недоступного инструмента — environment blocker, не skipped PASS.

Receipt содержит check/run/context IDs, exit code/status, duration, effective
config/cwd reference, bounded redacted output, truncation и SHA-256 полного
захваченного вывода. Различать failed, timeout, cancelled, not_run, unavailable,
stale, not_applicable. Итоговый gate принимает только полный required plan
одной revision/context, а не объединение старых зелёных результатов.
Проверки выполняются в venv проекта. Подготовка окружения требует разрешения;
missing dependency не является поводом менять исходники/lock. Отмена проверяется
между checks и до принятия результата; поздний ответ не разрешает новые действия.
Неожиданные изменения source/config от проверок делают итог недействительным.
Отмену проверять также во время preflight и запущенного subprocess: короткий
poll authority, bounded остановка только собственного дерева и reap. Не ждать
общего command timeout после отзыва прав и не принимать поздний PASS.
Не обещать нулевую задержку ОС или sandbox для detached чужих процессов.

## 4. R3 — устойчивость владения и восстановления

Bounded retry SQLite busy/locked: ограничение числа попыток и monotonic deadline
не длиннее оставшейся аренды с safety margin. Неповторяемые ошибки — fail closed.
Частичный успех task/job renewal не даёт продолжать работу без обоих прав.
Heartbeat не меняет ownership revision и не сбрасывает бюджеты полезной работы.

Разделить причины expired, replaced, revoked, storage_unavailable и job lease loss;
сохранить совместимую category task_authority_lost, но передавать reason в parent
diagnostics. Перед side effects и terminal commit проверять fencing.
Очередь до начала worker, handoff и промежутки между units не должны терять аренду.

Reconciliation использует keyset/cursor, bounded batches и справедливый обход:
первые живые записи не должны вытеснять остальные. Сохранять курсор безопасно,
обрабатывать рестарт и новые записи. Legacy task без достоверной связи job нельзя
угадывать по тексту: reconciliation_required с точным действием оператора.
Migration additive/idempotent; подтверждённые связи переносятся без смены прав.
Старый owner не может отменить/завершить нового; активные leases не захватываются.
Сбой между commits сходится через outbox; неопределённая мутация проверяется по
receipt/файлу, а не исполняется повторно вслепую. Cross-DB atomicity не заявлять.
Исключение/отмена из VERIFY или preflight вне model-unit handler обязательно
проходит fenced controller finalization: job/units не остаются running после
остановки Web-задачи. Истёкшего или заменённого owner финализация не подменяет.

## 5. R4 — сквозная приёмка планировщика

Изолированный вложенный Ozon-like проект с отдельным venv и отличимыми настройками
должен пройти IMPLEMENT → VERIFY FAIL → явно разрешённый REPAIR → VERIFY PASS.
Проверять task/job identity, generation, approval и context на каждом переходе.
Read-only задача не получает запись скрыто: создаётся связанная repair-задача
через существующее подтверждение 0.31; исходный evidence остаётся неизменным.
Успех фиксируется не по словам LLM, а по мутации, receipts и полному плану checks.
Проверить yield, bounded replan, смену worker, отмену во время вызова/проверки,
перезапуск и повторную доставку событий; side effects не дублируются.

## 6. R5 — Web/API и понятный статус

Использовать общий backend outcome и projection state в saved task/job/Web task/
diagnostics/SSE. Pending не изображать как активный worker или PASS.
Показывать virtual root, environment label, результаты checks и причину остановки;
IDs разных сущностей подписывать отдельно. Технические leases/generation — в
раскрываемых подробностях. Нет новой вкладки Autopilot и frontend-owned renewal.
Reload, SSE replay и две вкладки не создают второй запуск/approval/мутацию.
Reconnect не оживляет terminal-задачу. При недоступном сервере — честное offline.
Public DTO/ошибки/логи: без owner tokens, ключей, полных prompts и host paths.
Доступ к diagnostics, CSRF и existing scope policies не ослаблять.

## 7. R6 — live и выпуск

Граф сохраняет каждый checkpoint синхронно до следующего шага (durability="sync").
На малом executor (max_concurrency=2) многошаговые tool/duplicate сценарии должны
завершаться без взаимного ожидания delta/checkpoint futures. CI исполняет реальные
POSIX symlink tests отдельно и всю регрессию на Linux/Windows; зависший/отменённый
прогон не является PASS. Это дополнительный тест C12/C18, не ослабление A01–A34.

Дважды на чистых БД выполнить реальный LLM end-to-end из R4. Отдельно —
контролируемый HTTP timeout/backoff/fallback, cancel, crash/restart. Не путать
симуляцию транспорта с реальным provider response. Проверять разрешённый fallback,
privacy/manual model policy и конечный бюджет попыток/затрат.
При недоступном API указать конкретный незакрытый gate, не заменять его mock PASS.

Выполнить Ruff/format/pytest/mypy/compileall, frontend type/tests/build,
сборку wheel/sdist, установку wheel в чистый venv и CLI/Web smoke без source-tree
подмены импорта. Проверить миграцию копии прежней БД, повторный старт и историю.
Пропущенный symlink-тест компенсировать прогоном в поддерживающей среде либо
явно ограничить поддержку, без молчаливого общего security PASS.
Только после закрытия gates синхронизировать версии, changelog/status/README,
сформировать release notes и пакет доказательств. Публиковать/tag/merge только
в рамках актуального разрешения; не выдавать публикацию ветки за релиз.

## 8. Матрица завершения C01–C18

| ID | Проверка | Связь с исходной приёмкой |
| --- | --- | --- |
| C01 | Context roundtrip VERIFY/REPAIR/restart без смены scope | A18–A20 |
| C02 | Origin другой checkout/неверный Python отклонён | A21–A24 |
| C03 | Code/config/lock/environment drift исключает старый PASS | A25 |
| C04 | Полный plan и optional/required mypy | A26 |
| C05 | Нет объединения PASS разных contexts; partial не project PASS | A25–A27 |
| C06 | Cancel и неожиданные изменения при checks | A06/A29 |
| C07 | SQLite busy: успех bounded retry или диагностируемый loss | A13 |
| C08 | Queue/handoff/units сохраняют fencing и бюджеты | A01–A05/A14 |
| C09 | Более batch-size записей: нет starvation после restart | A10–A12 |
| C10 | Legacy неоднозначность и additive migration | A10/A31 |
| C11 | Полный разрешённый FAIL→REPAIR→PASS | A20/A32/A33 |
| C12 | Crash/cancel/repeated delivery без двойной мутации | A06/A09/A12 |
| C13 | Browser reload/SSE/две вкладки согласованы | A30 |
| C14 | Public payloads без секретов/host paths/owner tokens | A28/A30 |
| C15 | Два реальных LLM прогона с receipts | A33 |
| C16 | Отдельный HTTP timeout/fallback с policy и budget | A14/A33 |
| C17 | Wheel/sdist/clean install/CLI/Web/DB migration | A31/A34 |
| C18 | Все исходные A01–A34 сопоставлены с evidence и release gate | A34 |

Для каждой строки вести NOT RUN/PASS/FAIL/BLOCKED, команду, fixture/run ID,
artifact и ограничения. Все строки изначально NOT RUN для этого дополнения;
ранее полученное доказательство разрешено переиспользовать только с явной
ссылкой и проверкой соответствия текущему коду. Процент готовности не подменяет gate.
