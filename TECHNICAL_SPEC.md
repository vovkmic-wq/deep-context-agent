# Техническое задание: Deep Context Agent

## 1. Цель

Создать на Python 3.11+ CLI- и Web-агента на базе Deep Agents, который:

- сохраняет историю и большой внешний контекст между запусками;
- ищет релевантные фрагменты контекста перед ответом и по запросу модели;
- работает через LM Studio, OpenAI, Yandex AI Studio (YandexGPT), DeepSeek,
  Alibaba Model Studio (Qwen) и Zhipu AI (GLM);
- ищет актуальную информацию в интернете;
- читает, создаёт, изменяет и удаляет файлы и каталоги только внутри заданной
  директории;
- соответствует PEP 8 и имеет автоматические тесты.

Целевой объём: не менее 1 000 000 строк суммарного контекста и сотни
документов. Полный корпус не помещается в окно LLM, поэтому он хранится без
сокращения на диске, а в промпт попадают найденные фрагменты и их окружение.

## 2. Архитектура

1. `deepagents.create_deep_agent` выполняет агентный цикл, делегирование,
   управление растущим контекстом и работу с виртуальной файловой системой.
2. Все LLM-провайдеры подключаются через `langchain-openai.ChatOpenAI` и
   OpenAI-compatible Chat Completions API. Один провайдер выбирается
   `--provider`/`AGENT_PROVIDER`, а упорядоченная failover-цепочка —
   `--providers`/`AGENT_PROVIDER_PRIORITY`.
3. `CompositeBackend` направляет `/workspace/` в `FilesystemBackend` с
   `virtual_mode=True`. Директория задаётся `AGENT_WORKSPACE`; доступ к файлам
   вне неё не предоставляется. Встроенный filesystem middleware создаётся с
   явным списком инструментов без общего `delete`; удаление выполняется только
   отдельным `remove_path`.
4. Большой контекст и диалоги хранятся в SQLite. Полнотекстовый индекс FTS5,
   разбиение на фрагменты и ранжирование BM25 не требуют внешней embedding API.
   Файлы индексируются потоково и пакетами, без чтения всего файла в RAM.
5. Перед каждым запросом приложение автоматически извлекает релевантные
   фрагменты. Агент также получает `search_context`, список источников и чтение
   окна соседних чанков, поэтому начало документа остаётся доступным после
   обработки его конца и после перезапуска приложения.
6. Интернет-поиск предоставляется инструментом `web_search` через DDGS, а
   `fetch_web_page` открывает выбранный публичный результат для проверки точных
   фактов. Загрузка ограничена HTTP(S), стандартными портами, публичными IP и
   размером ответа; перенаправления проверяются повторно.
7. LangGraph checkpointer сохраняет состояние каждого `thread_id` в SQLite.
   Архив сообщений индексируется глобально и доступен retrieval после
   перезапуска и из другого `thread_id`; агент не должен называть его памятью
   только текущей сессии.
8. Секреты загружаются только из окружения и `.env.local`; они не попадают в
   промпты, логи, БД или репозиторий.
9. В системный промпт из доверенной конфигурации добавляются точные provider,
   model и base URL без API-ключа. Инструмент `runtime_info` возвращает те же
   несекретные значения.
10. Итог изменяющего файлового запроса содержит детерминированный отчёт,
    сформированный приложением из фактических tool calls/results, а не LLM.
11. Многострочный запрос передаётся одной транзакцией через `chat /paste`,
    `ask --file` или stdin. Построчная вставка не должна незаметно превращаться
    в независимые команды.
12. Явно незавершённая изменяющая команда отклоняется до LLM и tools. Агент не
    угадывает отсутствующие путь, текст или строку.
13. Transient model errors повторяются на уровне model call через middleware.
    Retry провайдерского клиента отключён; повтор всего graph после частичного
    выполнения tools запрещён.
14. Неудачный graph turn транзакционен относительно SQLite checkpointer:
    добавленные им checkpoints/writes удаляются, предыдущий успешный thread
    остаётся без изменений.
15. Автоматический retrieval имеет отдельный символьный бюджет и отключается для
    точных файловых, операционных и длинных самодостаточных запросов.
16. Audit охватывает все tools текущего хода. Для чтения и web-tools он не
    раскрывает содержимое, но фиксирует target, status и краткий результат.
17. Web-tools после исчерпания повторов возвращают структурированный
    `status=error` и UTC `checked_at`. Runtime программно помечает актуальный
    веб-факт как непроверенный без успешного `fetch_web_page` либо профильного
    официального verification-tool текущего хода.
18. Зависимые tool calls выполняются последовательно. Runtime передаёт модели
    `parallel_tool_calls=false` и программно оставляет не более одного tool call
    из одного ответа даже для провайдера, игнорирующего этот параметр.
19. Повторный идентичный mutating-tool, runtime/context/listing/web-вызов в
    одном пользовательском ходе отклоняется со статусом `denied`; новый ход
    получает чистый счётчик. Один и тот же неизменённый путь разрешено читать
    не более двух раз; версия пути меняется после успешной мутации этого пути
    или recursive удаления его родителя.
20. Атомарные пользовательские значения с маркером `DO_NOT_SHOW` или
    `НЕ_ПОКАЗЫВАТЬ` редактируются в assistant-ответе и audit, но не в файлах.
21. Точная версия PyPI может проверяться специализированным
    `get_pypi_package_info` через официальный JSON API с теми же SSRF, timeout и
    size-ограничениями, что у web-reader.
22. LLM-самооценка acceptance не является итоговым вердиктом. Без валидного
    manifest runtime помечает `CANDIDATE_RESULT`, `LLM_OBSERVATION_ONLY` и общий
    PASS/FAIL как `NOT_VERIFIED`; expected negative status нельзя автоматически
    считать провалом.
23. Строковое указание пользователя не читать, не открывать или не показывать
    конкретный путь является абсолютным запретом `read_file` в текущем ходе.
    Basename сопоставляется только с полными путями workspace, явно названными в
    том же запросе.
24. Один ограниченный JSON-блок `<acceptance_manifest>` версии 1 или 2 задаёт
    точные количества tools, ordered required events, forbidden events,
    разрешённые статусы и внешние pending-проверки. Версия 2 дополнительно
    разрешает required-предикаты `min_results` и `content_sha256`. Неизвестные
    поля, tools, статусы, зависимости или превышение лимитов делают manifest
    невалидным. Tool без объявленного exact count является нарушением.
25. Runtime manifest-аудитор считает все фактические попытки, включая
    policy-denied, не переиспользует один audit event для нескольких требований
    и проверяет `after` относительно позиции события в текущем turn.
26. Итог manifest-аудита формируется программно после ответа LLM и содержит
    per-tool/per-status counts, PASS/FAIL/PENDING, причины FAIL и авторитетный
    runtime verdict. Ожидаемые `denied/error/not_found` дают PASS только при
    явном разрешении manifest; forbidden call даёт FAIL при любом статусе.
27. Полный acceptance обязан завершать точный recursive cleanup, проверять
    отсутствие результата после него, отдельно удалять root sentinel и
    подтверждать его отсутствие новым чтением.
28. Отрицательная инструкция чтения действует только в текущем предложении или
    инструкционной части. Путь из следующего положительного предложения той же
    строки не наследует запрет; точка внутри basename не является границей.
29. Manifest версии 1 может объявить ограниченный `allowed_unlisted_tools` для
    недетерминированных служебных tools. Они отображаются в counts, но не
    участвуют в exact/unexpected-проверке; неизвестные tools, дубликаты и
    пересечение с exact counts запрещены.
30. Отсутствующий required event при выполненной зависимости является
    первичным FAIL. События с невыполненной `after`-dependency получают BLOCKED;
    runtime PASS требует одновременно `FAIL=0` и `BLOCKED=0`.
31. В manifest-сценарии сам manifest является планом, поэтому модель не обязана
    использовать `write_todos`. Перед финальным ответом она обязана завершить
    все cleanup/post-delete events и опираться на точные ToolMessage.
32. Если модель преждевременно возвращает финальный текст либо выбирает иной
    следующий call, runtime completion gate может продолжить только первый
    пропущенный dependency-ready event типа
    `read_file` с ожидаемым `error/not_found` либо явно запрошенный `remove_path`
    с ожидаемым `success`. Target обязан быть явно указан в запросе и находиться
    внутри prose-части запроса вне JSON manifest и находиться внутри
    `/workspace/`; root event, `/workspace/`, запрещённое чтение, исчерпанный
    exact count и event без доказанной dependency не исполняются.
33. Классификатор актуальных web-фактов анализирует prose запроса без блока
    `<acceptance_manifest>`: служебное поле `version` не может само по себе
    требовать web verification для context/filesystem acceptance.
34. После выполненного моделью root event manifest-режим может ограничить
    provider `tool_choice` именем следующего dependency-ready event. Для
    filesystem event prose обязана независимо содержать точный target и явное
    read/mutation-намерение; для остальных — имя tool и target. Exact count не
    должен быть исчерпан. Runtime не синтезирует содержимое write/edit и не
    запускает первый event.
35. Для dependency-ready `read_file` runtime фиксирует не только tool name, но
    и точный event target, если этот target явно разрешён prose и не запрещён
    отрицательной инструкцией. Разрешены ожидаемые `success/error/not_found`;
    иные статусы не синтезируются.
36. Dependency-ready `remove_path` получает точный prose-разрешённый target и
    manifest-декорацию `recursive=true`. `/workspace/` может быть вызван только
    как явно описанный negative event и всегда отклоняется filesystem guard.
37. Явный sentence-scoped запрет create/write/edit/delete/remove для workspace-
    пути имеет приоритет над manifest. Положительная filesystem-авторизация
    требует mutation intent в пределах 300 символов перед точным prose-путём.
38. Audit сохраняет безопасные структурированные evidence-поля: фактический
    `result_count` для count-bearing tools и `content_sha256` фактических байтов
    workspace-файла на момент успешного write/edit/read. Тело файла не попадает
    в audit; выход за workspace и небезопасный symlink не хешируются.
39. Required event manifest v2 с `min_results` проходит только при числовом
    evidence не меньше порога, а `content_sha256` — только при точном совпадении
    64-символьного SHA-256. Эти предикаты запрещены в forbidden events, чтобы
    не ослаблять запрет частичным совпадением.
40. При прямом вопросе о точном количестве результатов runtime формирует ответ
    из `result_count`, а не из текста LLM. Для других count-bearing операций
    добавляется авторитетный cardinality-блок.
41. Явная инструкция «TOOL ровно один раз» или `call TOOL exactly once`
    ограничивает число фактических попыток. После первой попытки tool удаляется
    из следующего model request; устаревший повторный call provider не
    исполняется и не создаёт второй audit event.
42. Упоминание `/workspace/` как корня проекта не считается exact-file scope и
    не запрещает чтение релевантных дочерних файлов. Явно названный дочерний
    файл сохраняет строгий exact-read scope; внешний путь остаётся запрещён.
43. Рекурсивная индексация до чтения файлов отсекает служебные generated/cache,
    coverage и browser-profile пути, включая `.pytest-*`, `.coverage*`,
    `playwright-report` и `edge-profile*`, регистронезависимо.
44. `list_context_sources` имеет token-safe страницу 20 записей по умолчанию и
    программный максимум 50 независимо от аргумента модели.
45. Текстовые документы с BOM UTF-16LE/BE и UTF-32LE/BE индексируются потоково;
    наличие NUL-байтов без распознанного BOM по-прежнему означает binary file.
46. Явные ограничения prose «at most/no more than/не более/максимум N tool
    calls» и аналогичные лимиты конкретного tool являются жёсткими per-turn
    бюджетами. После достижения лимита exhausted tools удаляются из следующего
    model request, а устаревший provider-call подавляется без audit-события.
47. Если runtime policy исчерпала все tools, следующий model request не содержит
    `tools`, `tool_choice` и `parallel_tool_calls`; это обязательно для
    совместимости с OpenAI API и проверяется отдельной regression.
48. Middleware, меняющие доступный toolset или `tool_choice`, выполняются до
    `SequentialToolCallMiddleware`; последовательный normalizer последним перед
    model handler формирует согласованные `tools`/`parallel_tool_calls`.
49. Явная budget-фраза и имя tool могут быть перенесены на соседнюю строку
    внутри одного предложения/пункта; ограниченный parser обязан сохранить
    связь, не пересекая точку, вопросительный или восклицательный знак.
50. Пути внутри `<acceptance_manifest>` являются только данными evaluator и не
    должны сужать exact-read allowlist. Ограничение чтения определяется prose-
    частью запроса; явно названный там файл сохраняет строгую защиту.
51. Нулевой per-tool budget скрывает запрещённый tool до первого model call.
    Read-only аудит может программно исключить web, planning и mutating tools,
    а не полагаться только на следование LLM текстовому запрету.
52. Классификатор актуальных web-фактов требует близкого сочетания маркера
    актуальности и точного существительного version/release/price/date либо его
    русского словоформенного эквивалента. Подстроки в словах `совпадать` и
    `полноценный`, а также аудит предоставленного кода не создают web FAIL.
53. Список провайдеров канонизируется до сетевого вызова, не допускает пустых
    элементов и дубликатов, включая пару alias/canonical `glm,zhipu`.
54. Каждый model call сначала получает настроенные retries текущего провайдера,
    затем переключается на следующего. Успешный fallback закрепляется до конца
    пользовательского хода; следующий ход восстанавливает исходный приоритет.
55. Failover оборачивает только model call и не повторяет уже завершённые tools.
    Если вся цепочка недоступна, наружу выводятся только provider/model и типы
    исключений без ответов API, ключей и иных потенциальных секретов.
56. `runtime_info` динамически сообщает активный provider, полную приоритетную
    цепочку, число переключений и имена неуспешных провайдеров. Каждый model
    request получает доверенный блок точной активной identity.
57. Широкий аудит всего проекта не исполняется одним graph turn. Runtime
    автоматически создаёт постоянный SQLite-манифест и обрабатывает проект
    пачками `AGENT_AUDIT_BATCH_SIZE` в независимых thread IDs с отдельным
    recursion budget.
58. Манифест содержит SHA-256 каждого файла, статус `pending/in_progress/
    reviewed`, номер пачки и счётчик чтений. После прерывания `in_progress`
    возвращается в `pending`; статус `reviewed` допустим только при успешном
    фактическом `read_file`, `write_file` или `edit_file` текущей пачки.
    Отдельный `file_reads` считает все успешные страницы чтения и не смешивается
    с количеством уникальных reviewed-файлов.
    Если модель упирается в `AGENT_AUDIT_MAX_READS_PER_FILE`, путь получает
    статус `partial`, а run — `complete_with_partial`; полное покрытие не
    выдумывается.
    Идентичная пара `offset/limit` одного audit-файла программно отклоняется;
    следующая отличающаяся страница остаётся разрешённой в пределах budget.
59. Неизменившийся SHA-256 повторно использует сводку файла. Изменившийся файл
    возвращается в `pending`, даже если остальная часть аудита уже завершена.
    Удалённый файл исключается из активного манифеста.
60. Python-файлы до безопасного size limit индексируются через `ast.parse` без
    импорта/исполнения кода. Инструмент `search_python_symbols` ищет определения,
    qualified names, строки и docstrings; `get_project_file_summary` возвращает
    только SHA-bound сводку текущего workspace.
61. `<project_audit_batch>` является доверенным runtime control. Middleware
    разрешает filesystem-доступ только к перечисленным путям, запрещает
    discovery/root/path-set mutations и не позволяет модели самостоятельно
    перейти к следующей пачке.
62. `AGENT_RECURSION_LIMIT` заменяет жёстко заданное значение и валидируется в
    диапазоне 25–500. Размер пачки, число пачек за процесс, timeout и размер
    вывода проверок также имеют программные границы. Audit page budget на файл
    задаётся `AGENT_AUDIT_MAX_READS_PER_FILE` (2–12, по умолчанию 4).
63. `run_project_checks` не принимает команды или аргументы shell. Допустимы
    только идентификаторы `ruff_check`, `ruff_format_check`, `pytest`, `mypy`,
    `compileall`; запуск использует список argv, `shell=False`, очищенное от
    ключей окружение, timeout и ограниченный редактированный вывод.
64. После мутации проверку разрешено повторить только в новой mutation epoch.
    Идентичный повтор без изменения отклоняется, а общее число циклов проверки
    в одном ходе ограничено. Это обеспечивает bounded analyze → fix → test →
    repeat без бесконечного agent loop.

## 2.1. Production-аудит и Web UI 0.13.0

1. Пакетный аудит всегда создаётся в режиме `read-only`. Право записи возникает
   только из явного операторского `audit --allow-write` либо подтверждённого
   Web-переключателя. Текст цели, ТЗ, retrieval и ответ модели не могут изменить
   режим. Режим входит в идентичность запуска и хранится в SQLite.
2. До создания пачек runtime строит точный file ledger. По умолчанию исключены
   `.deps`, `.pytest_tmp*`, `.pytest-*`, `reports`, `e2e/reports`,
   `*.egg-info`, virtualenv, dependency, build, cache, coverage, browser и test
   artifacts. Дополнительные glob задаются `AGENT_AUDIT_INCLUDE` и
   `AGENT_AUDIT_EXCLUDE`; статус хранит selected/excluded и причины.
3. До первой пачки из явно релевантных Markdown-ТЗ формируется межпакетный
   реестр с устойчивыми `REQ-*`, source/section/text/source hash/level. В prompt
   пачки передаётся только ограниченное релевантное подмножество. Матрица
   сохраняет `not_proven`, пока нет явной evidence-ссылки; LLM-текст не является
   окончательным доказательством.
4. Подтверждённые findings принимаются только из ограниченного JSON-блока,
   валидируются относительно выделенных путей, дедуплицируются fingerprint и
   сохраняются отдельно от prose. Severity, путь, строка, evidence,
   recommendation и статус доступны через CLI report и Web API.
5. Консольный результат ограничен 20 000 символами. Полный UTF-8 text/JSON
   report создаётся напрямую Python через `--report-file` и
   `--report-format text|json|both`, без PowerShell `Tee-Object`.
6. После каждой пачки CLI немедленно выводит одну flush-строку
   `AUDIT_PROGRESS` с JSON. `audit-status --run-id ... --json` читает состояние
   без LLM. Незавершённая пачка возвращается в pending; тот же identity
   продолжает с первого незавершённого файла.
7. Локальный Web UI является optional extra `web`, запускается на
   `127.0.0.1:8765`, использует те же runtime/SQLite/policies и не отправляет
   большой корпус браузеру. Полный нормативный контракт находится в
   `WEB_INTERFACE_TECHNICAL_SPECIFICATION.md` и является частью этого ТЗ.
8. Web API включает health/runtime, threads/chat/SSE, context, audits,
   workspace files, providers и settings. Все списки ограничены/пагинированы;
   длительные операции возвращают task ID и события.
9. State-changing Web API требует same-origin и CSRF token. Секреты и raw
   exceptions не возвращаются; CSP запрещает внешние/inline scripts. File API
   повторно проверяет resolved path, блокирует секретные файлы и symlink escape,
   использует SHA-256 optimistic concurrency; удаление выключено по умолчанию.
10. Внешний bind запрещён без `--allow-remote` и `AGENT_WEB_AUTH_TOKEN`; для
    production обязательно HTTPS reverse proxy. Первый релиз остаётся локальным
    single-user и не объявляется публичным SaaS.

## 2.2. Единый Web API и инженерный UX 0.14.0

1. Web UI не имеет собственной business logic: все chat/audit/context/files
   операции проходят через общий `AgentRuntime`, `ContextStore`,
   `ProjectAuditStore`, provider middleware и те же SQLite/policy, что CLI.
2. Chat организован как task/thread: bounded persistent history, новая задача,
   нижний auto-grow composer, cancel/error states. Device preference
   Enter-to-send выключен по умолчанию; Shift+Enter всегда добавляет строку.
3. Overview и chat поддерживают режимы general/audit/coder/tester/reviewer/
   debugger/refactor/security/architect/docs. Режим — prompt hint и никогда не
   предоставляет mutation authority.
4. Context index нормализует virtual `/workspace` ровно один раз, показывает
   lifecycle фоновой задачи и точные итоговые counters либо safe error.
5. Audit include, exclude и batch size имеют bilingual label и русское
   объяснение. Пустые glob означают безопасный default selection, batch size —
   число файлов в одном bounded model step.
6. Files UI поддерживает каталог, переход вверх, bounded UTF-8 preview/editor.
   Частичный preview read-only; запись требует актуальный SHA-256. Secret,
   traversal, symlink и delete policies остаются только server-authoritative.
7. Все Web-вызовы используют thread-safe live provider registry. Порядок
   configured providers можно атомарно менять до перезапуска процесса; одна
   выполняющаяся операция использует неизменный snapshot. Каждый provider
   имеет отдельный opt-in live check без раскрытия ключа/raw exception.
8. Safe settings представлены allowlisted строками с bilingual label, env name,
   numeric value и русским комментарием. Изменение повторно валидирует весь
   `AppConfig` и откатывается атомарно при ошибке.
9. Desktop sidebar преобразуется в мобильную нижнюю навигацию. Keyboard focus,
   semantic labels, aria-live, IME и viewport от 360 px входят в приёмку.
10. Нормативные API/UX/security критерии и browser E2E определены в разделе 13
    `WEB_INTERFACE_TECHNICAL_SPECIFICATION.md`.

## 3. Провайдеры и переменные

| Провайдер | Ключ | Модель / endpoint |
| --- | --- | --- |
| LM Studio | `LM_STUDIO_API_KEY` (необязательно) | `LM_STUDIO_MODEL`, `LM_STUDIO_BASE_URL` |
| OpenAI | `OPENAI_API_KEY` | `OPENAI_MODEL`, `OPENAI_BASE_URL`, `OPENAI_REASONING_EFFORT` |
| YandexGPT | `YANDEX_API_KEY` | `YANDEX_MODEL_URI` или `YANDEX_FOLDER_ID`, `YANDEX_BASE_URL` |
| DeepSeek | `DEEPSEEK_API_KEY` | `DEEPSEEK_MODEL`, `DEEPSEEK_BASE_URL` |
| Qwen | `DASHSCOPE_API_KEY` | `QWEN_MODEL`, `QWEN_BASE_URL` |
| Zhipu GLM | `ZAI_API_KEY` (`ZHIPU_API_KEY` — alias) | `ZAI_MODEL`, `ZAI_BASE_URL` (`ZHIPU_*` — aliases) |

Значения endpoint и моделей должны переопределяться без изменения кода.
Модель обязана поддерживать tool calling; это особенно важно для локальной
модели LM Studio.

Для резервной `openai/gpt-5.6-sol` в текущем Chat Completions tool-calling
контуре устанавливается `OPENAI_REASONING_EFFORT=none`. Ненулевой effort с
function tools требует Responses API и не должен включаться неявно.

Zhipu выбирается как `--provider zhipu` или `--provider glm`; алиас
канонизируется в `zhipu`. Значения по умолчанию: модель `glm-5.3`, стандартный
endpoint `https://api.z.ai/api/paas/v4`, включённый thinking. Для GLM
Coding Plan пользователь явно задаёт
`https://api.z.ai/api/coding/paas/v4`. Температура GLM ограничена
диапазоном `0 < temperature <= 1` и проверяется до сетевого запроса.
Поскольку Zhipu API не документирует OpenAI-параметр `parallel_tool_calls`,
этот параметр для Zhipu не передаётся; runtime всё равно программно оставляет
не более одного tool call из ответа модели.

Цепочка по умолчанию — `AGENT_PROVIDER_PRIORITY=glm,openai`: сначала
`glm-5.3`, затем `gpt-5.6-sol`. Общая приоритизация переопределяется переменной
`AGENT_PROVIDER_PRIORITY` или CLI `--providers`. Явный `--provider` и `--providers`
взаимоисключающие. Если указана цепочка, конфигурация и ключ каждого её элемента
валидируются до запуска агента. Одиночный режим сохраняет прежнее поведение и
формат основных строк `doctor`.

## 4. CLI

- `context-agent chat` — интерактивная сессия;
- `context-agent ask "..."` — одиночный запрос;
- `context-agent ask --file PROMPT.txt` — один многострочный UTF-8 запрос;
- `context-agent ask -` — один запрос из stdin;
- `context-agent audit "..."` / `audit --file PROMPT.txt` — создать или
  продолжить SQLite-манифест пакетного аудита; `--max-batches` задаёт жёсткий
  предел 1–100 для текущего процесса; `--allow-write` является единственным
  CLI-разрешением записи; `--report-file` и `--report-format` создают полный
  UTF-8 отчёт;
- `context-agent audit-status --run-id ID [--json]` — получить persisted
  progress без LLM и платного API;
- `context-agent index [PATH]` — индексирование файла или каталога внутри
  `AGENT_CONTEXT_ROOT`;
- `context-agent search "..."` — проверка поиска по локальному контексту;
- `context-agent doctor` — безопасная диагностика конфигурации без вывода
  ключей, Web extra/static bundle и локального порта.
- `context-agent web --host 127.0.0.1 --port 8765` — безопасный локальный Web
  UI; внешний host требует `--allow-remote` и bearer token из окружения.
- `context-agent --providers "glm,openai" doctor --live` — проверить
  цепочку по приоритету и остановиться на первом успешном провайдере.

В интерактивном режиме `/paste` и `/paste ПЕРВАЯ_СТРОКА` читают продолжение до
`/end` и передают весь текст одним turn; `/cancel` отменяет ввод.

## 5. Безопасность

- Все пути нормализуются и проверяются после `resolve()`.
- Символические ссылки не должны позволять выйти за разрешённый корень.
- Рабочая директория агента по умолчанию — `./agent_workspace`, данные —
  `./.agent_data`, исходный контекст — рабочая директория.
- Агент не получает произвольный локальный shell/`execute`; файловые изменения
  выполняются только через ограниченный backend. Проектные проверки запускаются
  отдельным фиксированным allowlist без shell и без пользовательских argv.
- Результаты веб-поиска и локального retrieval считаются недоверенными данными,
  а не инструкциями.
- Удаление корня рабочей директории запрещено.
- Запрос удаления `/workspace/` блокируется до рекурсивного обхода; агенту
  запрещено трактовать его как разрешение удалить дочерние элементы по одному.
- Если запрос на изменение содержит только явный путь вне `/workspace/`, он
  отклоняется до вызова модели. Нельзя создавать placeholder или подменяющий
  файл внутри workspace.
- При заданном точном пути агент читает только его и не добавляет несвязанные
  файлы в ответ. Отсутствующий путь не заменяется предположением.
- Явный запрет чтения точного пути имеет приоритет над его упоминанием в других
  шагах составного prompt и блокируется до вызова filesystem backend.
- Успех, отказ и ошибка файлового изменения определяются результатом tool и
  отражаются в проверяемом отчёте; свободный текст LLM не является
  подтверждением операции.
- Текущее состояние файла подтверждается только filesystem tool текущего хода,
  а текущий веб-факт — успешным `fetch_web_page` либо профильным официальным
  verification-tool текущего хода с `checked_at`.
- Старый retrieved assistant-ответ может использоваться как поисковая подсказка,
  но не как подтверждение уже выполненной операции или актуального веб-факта.
- При model/API exception ответ не архивируется как успешный, а неудачный input
  удаляется из checkpoint перед следующим запросом.
- Успешное файловое изменение, выполненное до последующей model error, не
  откатывается скрыто. Ошибка содержит программный текущий audit и явное
  предупреждение о сохранённом side effect; checkpoint при этом восстанавливается.
- Один runtime обслуживает запросы последовательно. Одновременная запись
  нескольких процессов в один thread/checkpoint DB не входит в поддерживаемый
  режим локального CLI.
- При запросе удаления явно названного подкаталога целиком используется один
  `remove_path(..., recursive=true)`. Потомки не удаляются отдельными вызовами.
- Если точный URL передан пользователем для открытия, результат должен исходить
  из web-tool текущего хода. Предполагаемый моделью отказ без tool evidence не
  считается проверкой политики.

## 6. Качество и тестирование

- Форматирование и lint: Ruff, максимальная длина строки 88.
- Типизация публичных функций и классов; docstrings у публичного API.
- Unit-тесты: конфигурация всех провайдеров, защита путей, разбиение и поиск
  контекста, потоковое индексирование, выборка соседних чанков, сотни документов,
  веб-поиск и безопасное чтение страницы с mock, извлечение ответа.
- Интеграционные тесты: создание Deep Agent с fake chat model; файловые
  операции не выходят за `AGENT_WORKSPACE`; общий `delete` отсутствует;
  попытка удалить root через настоящий tool loop возвращает `denied` и не
  меняет контрольный файл; внешний Windows-путь не порождает подмену.
- Тесты надёжности: model-call retry без повторного tool, rollback checkpointer,
  web timeout/retry, многострочный CLI, неполная команда, retrieval budget и
  audit всех категорий tools.
- Acceptance-регрессии: запрет параллельных зависимых calls, защита от
  идентичной мутации, output-redaction, точный PyPI JSON, recursive cleanup и
  программная маркировка LLM-самооценки.
- Acceptance 0.4-регрессии: повторные read-only/web calls, budget чтения по
  версии пути, запрещённый decoy read, строгий manifest parser, exact counts,
  ordered post-delete evidence, forbidden events и ожидаемые negative statuses.
- Acceptance 0.5-регрессии: sentence-scoped negative read, разрешённый
  planning-tool вне exact counts, отдельный BLOCKED status и обязательный
  финальный sentinel post-delete event, включая bounded runtime completion gate
  и запрет автономного запуска root event.
- Acceptance 0.7-регрессии: общий project-root scope не блокирует найденный
  дочерний файл; generated/browser/cache артефакты не попадают в индекс;
  listing источников ограничен 20/50 записями; BOM-тексты UTF-16/UTF-32
  остаются полнотекстово searchable.
- Acceptance 0.8-регрессии: русские/английские числовые tool budgets строго
  парсятся; per-tool и total пределы не допускают лишнего фактического вызова.
- Production 0.12-регрессии: сотни документов в audit manifest, SHA-resume,
  повторное открытие только изменённого файла, AST symbols/summary cache,
  workspace isolation, batch confinement, управляемый recursion limit и
  безопасный allowlist project checks без shell/секретов.
- Prompt-файл и stdin читаются с жёстким пределом до декодирования/дальнейшей
  обработки, поэтому проверка размера не допускает race с неограниченным чтением.
- Сетевые тесты не являются частью обычного `pytest`; `doctor` и опциональный
  live smoke-test запускаются отдельно.

## 7. Критерии приёмки

Для релиза 0.13.0 дополнительно обязательны:

- regression, что слова «исправь/fix» без `--allow-write` не разрешают запись;
- file-selection regression для dependency/generated/report/pytest/egg-info и
  пользовательских include/exclude;
- сохранение requirements/findings и UTF-8 text/JSON report с кириллицей;
- корпус не менее 1 000 000 строк и 500 документов без помещения корпуса в
  model prompt;
- Web API regressions CSRF/origin/CSP, traversal, secret path, stale hash,
  отключённого delete, redaction ключей и remote authentication gate;
- offline Web smoke на чистых workspace/data, package/wheel/static проверки и
  отдельный opt-in `doctor --live` только при существующем локальном ключе.

1. `ruff check --no-cache .` и `ruff format --check --no-cache .` завершаются
   без ошибок независимо от владельца служебных каталогов.
2. `pytest` завершается успешно без доступа к платным API.
3. CLI показывает помощь и безопасную диагностику.
4. Контекст сохраняется в SQLite, находится после повторного открытия БД и
   автоматически добавляется к следующему запросу. Начальные и конечные части
   большого файла находятся независимо; индексирование использует ограниченную
   память, пропорциональную размеру чанка, а не корпуса.
5. Агент создаётся для каждого из шести канонических провайдеров при наличии
   его настроек; CLI-алиас `glm` канонизируется в `zhipu`.
6. Реальный smoke-test выполняется хотя бы с одним доступным провайдером либо
   явно фиксируется внешняя причина невозможности вызова.
7. В списке tools агента отсутствует `delete`, присутствует безопасный
   `remove_path`; попытка удалить `/workspace/` не удаляет ни одного потомка.
8. Запрос создания `C:\outside-agent.txt` не вызывает LLM/file tools и не
   создаёт `/workspace/outside-agent.txt`.
9. Вопрос о текущей модели получает точное значение конфигурации; сведения о
   памяти описывают постоянный SQLite-архив между запусками и thread IDs.
10. Точные чтения не возвращают несвязанные файлы, а изменяющие запросы всегда
    имеют проверяемый отчёт об исполненных, отклонённых или отсутствующих
    операциях.
11. Многострочный prompt доходит до runtime одним запросом; его строки не
    появляются как отдельные turns.
12. После окончательной ошибки model call следующий запрос не содержит
    провалившуюся команду, а файловые tools не выполняются повторно.
13. После неуспешного web search/fetch агент не может выдать актуальную версию и
    дату как подтверждённые; audit показывает отсутствие успешного fetch.
14. Финальный `PASS` допустим только при наличии текущих доказательств всех
    обязательных операций; неподтверждённый пункт получает `FAIL`.
15. Один ответ модели не приводит к параллельному выполнению нескольких tools;
    повторная идентичная мутация в одном ходе имеет audit `denied`.
16. Маркер `DO_NOT_SHOW` из запроса не появляется в финальном assistant-ответе
    или audit; фактическое содержимое созданного файла при этом не меняется.
17. Точная версия PyPI извлекается из официального источника с `checked_at`, а
    local/private URL отклоняется без сетевого обращения.
18. После явно запрошенного удаления непустого подкаталога отсутствуют и его
    содержимое, и сам каталог; `/workspace/` и его sentinel остаются.
19. Повтор идентичного runtime/context/listing/web-вызова имеет audit `denied`;
    третье чтение неизменённого пути блокируется, а новый turn получает чистый
    ledger.
20. Файл, явно помеченный «не читай», не вызывает `read_file`, даже если его
    полный путь указан в другом месте составного prompt.
21. Валидный acceptance manifest программно выявляет лишний вызов, нарушение
    порядка, пропущенный cleanup/post-delete read и forbidden event; ложный
    `LLM_OBSERVATION_ONLY` не может изменить runtime verdict.
22. Итог полного acceptance содержит точные количества tools и отдельные
    доказательства удаления тестового каталога и sentinel с последующим
    `error/not_found` чтением.
23. Составная строка «не читай decoy; проверь result» запрещает только decoy;
    все предусмотренные чтения result выполняются и проверяются.
24. Недетерминированный `write_todos` не изменяет functional verdict при явном
    `allowed_unlisted_tools`, но любой другой необъявленный tool остаётся FAIL.
25. Один первичный сбой ordered chain не раздувается в независимые FAIL:
    зависимые требования явно отображаются как BLOCKED.
26. Полный изолированный acceptance завершается runtime PASS, а физическая
    проверка подтверждает отсутствие тестового каталога, sentinel и placeholder.
27. Преждевременный финальный текст после доказанного cleanup не обрывает
    dependency-ready postcondition chain; runtime выполняет по одному безопасному
    event, но не начинает исходную операцию и не расширяет права manifest.
28. Поле `version` внутри acceptance manifest не создаёт ложный web FAIL для
    запроса текущего количества результатов локального context search.
29. После root event дешёвая модель не может переставить ordered tool calls:
    каждый следующий разрешённый шаг получает точный provider `tool_choice`.
30. Неправильный target правильного `read_file` заменяется точным разрешённым
    event target; unrelated/forbidden файл не читается.
31. Путь из «не создавай/не изменяй/не удаляй» не выполняется completion gate,
    даже если manifest содержит event и запрос мутирует другой путь.
32. Manifest v2 программно отклоняет недостаточное `result_count` и неверный
    `content_sha256`; точное содержимое файла подтверждается без утечки тела.
33. Ошибочный текст LLM «0 результатов» при фактическом ToolMessage с четырьмя
    результатами заменяется авторитетным runtime-ответом с числом 4.
34. Для запроса `search_context ровно один раз` две последовательные попытки
    модели дают одну исполненную операцию и одну audit-запись без второго
    `denied`; результат сохраняет фактическую cardinality.
35. Запрос полного аудита трёх файлов с batch size 2 выполняется двумя
    независимыми graph invocations, фиксирует три успешных чтения и завершает
    SQLite-манифест без `GraphRecursionError`.
36. После частично прочитанной пачки только доказанные файлы получают
    `reviewed`; повторный запуск с тем же thread/objective продолжает pending.
37. Изменение байтов ранее проверенного файла меняет SHA-256 и возвращает
    только его в pending; неизменённые summaries не пересчитываются.
38. AST-индекс находит class/function qualified name без импорта кода, а данные
    другого workspace из общей БД не возвращаются.
39. `run_project_checks` отклоняет shell injection, не передаёт API keys в
    дочернее окружение и не возвращает их в output. Одинаковая проверка без
    мутации блокируется, после подтверждённой мутации выполняется повторно.
40. Регрессия на 1 000 001 строку подтверждает поиск маркеров начала и конца;
    манифест не помещает этот корпус в prompt и масштабируется как число пачек.
41. Пятая страница при audit read limit 4 получает `denied`; четыре успешные
    страницы учитываются в `file_reads`, файл — в `partial`, а не `reviewed`.
42. Повтор одинаковой audit-страницы отклоняется без backend read, но новая
    страница с другим offset выполняется и учитывается ровно один раз.

## 8. Этапы

1. Зафиксировать ТЗ и управляющий промпт.
2. Создать структуру проекта и конфигурацию провайдеров.
3. Реализовать SQLite-контекст и инструменты.
4. Собрать Deep Agent и CLI.
5. Добавить инструкции, тесты и PEP 8 проверки.
6. Установить зависимости и выполнить полную проверку.

## 9. Первичные источники

- https://docs.langchain.com/oss/python/deepagents/overview
- https://docs.langchain.com/oss/python/deepagents/backends
- https://github.com/langchain-ai/langchain
- https://developers.openai.com/api/reference/overview
- https://lmstudio.ai/docs/developer/openai-compat
- https://yandex.cloud/en/docs/tutorials/ml-ai/ai-model-ide-integration
- https://api-docs.deepseek.com/guides/tool_calls/
- https://www.alibabacloud.com/help/en/model-studio/qwen-function-calling
- https://docs.z.ai/api-reference/llm/chat-completion
- https://developers.openai.com/api/docs/models/gpt-5.6-sol
- https://docs.bigmodel.cn/cn/guide/develop/openai/introduction
