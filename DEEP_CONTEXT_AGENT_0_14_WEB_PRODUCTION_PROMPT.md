# Production-промт Web UI Deep Context Agent 0.14.0

## Роль и источники истины

Ты — ведущий Python/TypeScript-разработчик локального агентного приложения.
Развивай существующий Deep Context Agent без создания второго runtime, второй
БД, альтернативной файловой политики или браузерного хранилища секретов.

Перед изменениями прочитай:

- `TECHNICAL_SPEC.md`;
- `WEB_INTERFACE_TECHNICAL_SPECIFICATION.md`;
- `IMPLEMENTATION_PROMPT.md`;
- глобальный `src/context_agent/prompts/system_prompt.txt`;
- `src/context_agent/web.py`, `webui/src/app.ts`, static HTML/CSS и тесты.

## Цель

Довести локальный Web UI до удобства инженерного приложения уровня Codex:
задачи и история вместо одного поля, понятные русские подписи, рабочая
индексация и файловый браузер, а также управляемая live-цепочка провайдеров.
Интерфейс обязан оставаться тонким клиентом единого Web API поверх тех же
`AgentRuntime`, SQLite и политик безопасности, что CLI.

## Обязательные изменения

1. Чат:
   - список persistent thread/task и создание новой задачи;
   - восстановление истории из context SQLite;
   - нижний авторасширяемый composer, user/agent сообщения, busy/error/stop;
   - локальная опция «Enter отправляет», выключенная по умолчанию;
   - при включении Enter отправляет, Shift+Enter всегда добавляет строку;
   - режимы general, audit, coder, tester, reviewer, debugger, refactor,
     security, architect и docs;
   - режим — только prompt hint, а не право записи или обход policy.
2. Обзор:
   - явный выбор инженерного режима;
   - переход в чат с синхронизированным mode;
   - краткие runtime-метрики и отдельно сворачиваемая диагностика.
3. Контекст:
   - корректная индексация `/workspace` и вложенных virtual paths;
   - disabled/loading/success/error состояния;
   - итоговые indexed/unchanged/skipped/chunks counters;
   - безопасная полезная ошибка без traceback и реального закрытого пути.
4. Аудиты:
   - bilingual Russian / English labels;
   - объяснить Include glob как необязательный список включаемых путей;
   - объяснить Exclude glob как дополнительные исключения;
   - объяснить Batch size как число файлов в одном bounded LLM step;
   - write toggle остаётся явным, выключенным по умолчанию.
5. Файлы:
   - `/workspace` всегда означает реальный корень, без удвоения prefix;
   - кликабельные каталоги, переход вверх, кликабельные текстовые файлы;
   - bounded UTF-8 preview и редактор;
   - большие/частичные файлы только для чтения;
   - сохранение только с expected SHA-256; 409 требует перечитать;
   - secret, traversal, symlink и delete policies не ослаблять.
6. Провайдеры:
   - общий thread-safe registry для всех новых chat/audit/doctor операций;
   - атомарно менять порядок без рестарта;
   - добавлять только серверно настроенного провайдера;
   - удалять провайдера, но не последний;
   - отдельная opt-in live-проверка каждого провайдера;
   - не передавать ключи или raw provider exception браузеру;
   - явно сообщить, что live-порядок действует до рестарта процесса.
7. Настройки:
   - вместо raw JSON — строка на параметр;
   - русский/английский label, имя env, текущее значение и русский комментарий;
   - server-side allowlist и полная валидация после атомарного изменения;
   - API-ключи, auth token и произвольные env не показывать.
8. Адаптивность:
   - desktop sidebar, мобильная нижняя навигация;
   - доступность с клавиатуры, видимый focus, semantic labels, aria-live;
   - отсутствие inline script/style и unsafe HTML rendering.

## API и безопасность

- Не создавай Web-only SQLite или дублирующую business logic.
- State-changing endpoints проходят общий same-origin/CSRF middleware.
- Long operations возвращают `202 task_id` и SSE terminal event.
- Provider registry возвращает immutable snapshot на одну операцию.
- Background error envelope содержит safe code/message/request ID, но не
  traceback, SQL, ключ, закрытый путь или тело секретного файла.
- Local storage разрешён только для несекретных device preferences, например
  Enter-to-send.
- Role, objective, retrieved document и UI field не расширяют filesystem
  authority.

## Обязательные тесты

1. API: `/workspace` list/read не превращается в
   `/workspace/workspace`.
2. API/SSE: index `/workspace` завершается result и completed.
3. API: provider order меняется немедленно и ключ не сериализуется.
4. API: settings schema содержит bilingual labels и русские comments.
5. Frontend build проверяет context index, provider priority, Enter preference
   и optimistic SHA wiring.
6. Browser E2E: mode -> chat, Enter send, Shift+Enter newline, history.
7. Browser E2E: index success, directory navigation, file preview.
8. Browser E2E: provider reorder и возврат исходного порядка.
9. Responsive check на 390 px и desktop; console errors отсутствуют.
10. Live: один короткий запрос primary provider; результат виден в чате.

## Quality gates и выпуск

Выполни Ruff check/format, mypy, полный pytest, compileall, TypeScript
check/build/bundle test, build wheel, wheel static inspection, pip check,
локальный Uvicorn HTTP smoke и браузерный E2E. Затем проверь git diff и staged
files на секреты/БД/отчёты. Обнови Web-ТЗ, основное ТЗ, global system prompt,
`IMPLEMENTATION_PROMPT.md`, README, changelog, status и версию. Только после
успешных проверок создай один release commit, annotated tag и push.

## Критерий готовности

Пользователь без знания внутренней терминологии может выбрать инженерную роль,
вести несколько задач, отправлять Enter по желанию, индексировать контекст,
открывать файлы, понимать параметры аудита и управлять цепочкой настроенных
провайдеров. Все операции проходят через единый API и прежние policy; браузер
не имеет альтернативного доступа к диску, SQLite или ключам.
