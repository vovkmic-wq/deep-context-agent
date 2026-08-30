# Production prompt: active-context recovery 0.17.0

## Цель

Устранить неудачное завершение длинных Web/CLI-запросов, когда постоянная
SQLite-история и большие `ToolMessage` разрастаются быстрее, чем активное окно
провайдера. Сохранить полный архив и поиск по корпусу 1 000 000+ строк, но не
пересылать весь архив каждой модели.

## Обязательные действия

1. Измерить фактическое состояние сбойного thread: сообщения, символы, типы и
   крупнейшие tool results. Не считать размер файла SQLite размером LLM prompt.
2. Добавить валидируемый `AGENT_ACTIVE_CONTEXT_MAX_TOKENS`. До каждого model
   call работать с глубокой копией сообщений: при превышении порога заменить
   старые тела/аргументы tools безопасными маркерами, сохранив не менее восьми
   последних tool results. Не изменять checkpoint, архив или FTS5.
3. Оставить встроенную Deep Agents summarization вторым уровнем защиты.
   Миллион строк хранить и искать на диске; в LLM подавать retrieval, текущую
   рабочую пачку и компактную историю.
4. Классифицировать `AgentError` в стабильные safe-коды: переполнение окна,
   quota, rate limit, authentication, timeout, network, step limit и общий
   failover. Raw SDK exception, ключи и physical paths не отдавать браузеру.
5. Сохранять bounded terminal event фоновой задачи и предоставлять read-only
   task status. Повторное SSE-подключение после завершения обязано получить
   terminal event, а не пустой поток.
6. На Windows подавлять только известный callback-noise Proactor
   `_call_connection_lost` с `WinError 10054`; все прочие loop exceptions
   передавать прежнему/default handler.
7. Обновить глобальный промпт, основное и Web-ТЗ, env, README, changelog,
   version и status.

## Проверка

1. Unit: старая tool-history компактируется в копии, последние восемь
   результатов и текущий запрос остаются, исходные messages неизменны.
2. API: причина ошибки получает safe code; raw detail отсутствует; task status
   и повторный SSE возвращают тот же terminal event.
3. Windows: распознаётся только точная benign-комбинация callback + 10054.
4. Полный Ruff format/check, mypy, pytest, TypeScript, compileall, wheel/install
   и secret scan проходят.
5. На копии реального длинного checkpoint подтверждается уменьшение model input.
6. На чистых DB/workspace/thread выполнить live doctor основной и резервной
   модели, затем Web chat через SSE и повторное чтение terminal state.

Production готов только при фактических PASS. Не публиковать и не объявлять
release, если любой обязательный контур не завершён.
