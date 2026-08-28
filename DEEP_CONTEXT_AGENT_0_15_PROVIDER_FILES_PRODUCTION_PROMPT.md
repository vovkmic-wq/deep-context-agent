# Production-промпт Deep Context Agent 0.15.0

Цель: устранить подтверждённые live-дефекты Web UI без создания
обходной business logic и без ослабления security boundary.

1. По логам и реальному `/v1/models` отдели недоступный LM Studio,
   пустой каталог, незагруженную модель и ошибку Chat Completions.
2. Если `LM_STUDIO_MODEL=local-model`, в Web live-doctor выбери первую
   загруженную чат-модель, исключив embedding/rerank, и обнови
   thread-safe provider registry для новых операций.
3. Не показывай payment confirmation для loopback provider. Пометь его
   как «локально, без платы API». Для remote live-call сохрани
   явное предупреждение о возможной оплате.
4. Добавь в UI/API создание OpenAI-compatible provider `custom-*`.
   Валидируй ID, model и URL; HTTP разрешай только loopback, для
   remote требуй HTTPS. Не принимай ключ из browser: remote ключ
   бери только из `CUSTOM_<ID>_API_KEY` окружения Web-процесса.
5. В Files реализуй настоящую history-back отдельно от parent-up.
   Каждое открытие каталога показывает loading, success/error, virtual
   path и count; кнопки disabled только когда действие невозможно.
6. Ожидаемые AgentError в SSE объясняй safe операторским сообщением,
   не раскрывая raw exception, ключи или реальные пути.
7. Добавь unit/API/bundle regression для custom provider, SSRF/secret
   boundary, LM Studio model resolution, local-free label и file history.
8. Выполни Ruff lint/format, mypy, pytest, compileall, pip check, TypeScript
   check/build/bundle, wheel inspection, real LM Studio doctor, local Uvicorn API и
   desktop/mobile browser acceptance. Исправляй найденное и повторяй
   затронутые проверки.
9. Синхронизируй system prompt, глобальный prompt/ТЗ, Web-ТЗ, README,
   env example, changelog, status и package version.
10. Перед commit/tag/push проверь diff, артефакты и секреты.
    Не публикуй несвязанные пользовательские файлы.
