# Промт Web-интерфейса 0.27: durable task UX

Обнови Web API и интерфейс Deep Context Agent по
`DURABLE_EXECUTION_SCHEDULER_TECHNICAL_SPEC.md`. Не создавай отдельную вкладку
Autopilot и не дублируй runtime policies во frontend. Чат остаётся основной точкой
постановки и продолжения задач.

## 1. Асинхронная архитектура

1. `POST /api/chat` или task submit сохраняет job/queue transaction до HTTP 202.
2. Background scheduler не принадлежит lifespan конкретного HTTP/SSE соединения.
3. Отключение/перезагрузка вкладки не отменяет job и не создаёт новый worker.
4. После server restart queued/interrupted job проходит reconciliation.
5. SSE использует монотонный sequence и поддерживает replay по `Last-Event-ID`.
6. Одинаковый idempotency key не создаёт вторую задачу.

## 2. Контракт состояния

DTO job/task обязан содержать:

- task/job ID и revision;
- workflow, phase, status и terminal reason;
- completed/yielded/failed/interrupted units раздельно;
- model turn, current unit, active-task и wall-clock elapsed/remaining;
- discovery units/reads/searches/unique lines и ceilings;
- changed files, checks run и verification status;
- last verified progress и structured next operation;
- current provider/model, routing/escalation reason;
- existing diagnostic ID или явное `diagnostics_unavailable`.

Не выводить `units=0/11` как единственный показатель. Planned yield не показывать
красной ошибкой. Поля времени подписывать как «активное вычисление», «текущий этап»
и «срок жизни задачи», а не одним словом timeout.

## 3. Чат

- Закреплённый заголовок и выбор модели сохраняются.
- Под сообщением задачи отображается компактная карточка live-progress.
- Основная строка: текущая фаза и понятное действие, например
  «Реализация: создаётся history/store.py».
- Дополнительные данные раскрываются по запросу, чтобы не перегружать пользователя.
- Пользователь не вводит batch size, число файлов или размер work unit.
- Кнопки: Пауза / Pause, Продолжить / Resume, Отменить / Cancel, Диагностика /
  Diagnostics. Операции используют CSRF и expected revision.
- Resume продолжает выбранную task identity, а не создаёт полный аудит.
- При WAITING_USER показывать ровно один необходимый вопрос.

## 4. Фазы и переходы

Локализовать и показывать:

- Исследование / Discover;
- Планирование / Plan;
- Реализация / Implement;
- Проверка / Verify;
- Исправление / Repair;
- Завершено / Complete;
- Приостановлено / Paused;
- Требуется решение / Waiting for input;
- Заблокировано / Blocked.

История переходов содержит время, reason и evidence summary. Не показывать raw
prompt, секреты, внутренний traceback и абсолютный host path.

## 5. Бюджеты без перекладывания работы на пользователя

В обычном режиме показывать progress bar активного времени и текущей unit. В
подробностях — четыре бюджета, tool/token/cost ceilings и причина последнего renewal.

Настройки оператора допускают defaults из S01/S04, но UI:

- валидирует диапазоны и взаимосвязи;
- объясняет по-русски / English назначение каждого поля;
- предупреждает, что увеличение срока не устраняет discovery loop;
- применяет новые значения только к новым задачам, если migration policy не говорит
  иначе;
- не меняет скрыто ручной model/local-only выбор.

## 6. Discovery и обязательный результат

Карточка показывает `discovery 1/2`, reads/searches/unique lines и покрытие. При
ceiling UI ожидает один из результатов:

- переход к PLAN/IMPLEMENT;
- reasoning escalation с причиной;
- structured blocker.

Если allow-write IMPLEMENT закончилась без мутации, UI показывает точный blocker,
а не общий «операция завершилась ошибкой». Для read-only отсутствие записи нормально.

## 7. Structured next operation

Показывать безопасное представление: фаза, действие, относительный `/workspace`
target, ожидаемый эффект и будущая проверка. Не исполнять значение во frontend и
не отображать raw shell. Невалидная/устаревшая операция имеет отдельный reason.

## 8. Диагностика

Простой пользователь видит краткое объяснение и рекомендуемое действие. Технические
детали раскрываются отдельно:

- parent/child IDs;
- units/model attempts/provider/model/duration;
- tool receipt summaries;
- timeout type и remaining budgets;
- rollback/reconciliation;
- redacted exception chain.

Ссылка активна только для существующей записи. Export сохраняет redaction policy.

## 9. API и concurrency

- Mutation endpoints требуют CSRF/auth и `expected_revision`.
- Stale revision возвращает 409 с актуальным состоянием без скрытого retry.
- Polling/SSE GET не продлевают lease и task budget.
- Одновременные вкладки получают одну и ту же последовательность событий.
- Cancel подтверждается только после runtime acknowledgement до следующего side
  effect; закрытие модального окна не является cancel.
- API schema версионируется additive; старый UI получает безопасные defaults.

## 10. Доступность и адаптивность

- Sticky header остаётся видимым при прокрутке сообщений.
- Keyboard navigation, focus states, aria-live для terminal и progress updates.
- Не озвучивать каждый heartbeat; сообщать только фазу, meaningful progress,
  blocker и terminal.
- На узком экране подробные counters сворачиваются, основные controls доступны.
- Русский / English через слэш применяется ко всем новым подписям.

## 11. Тестирование

Добавь API/schema/SSE/frontend/browser tests:

1. submit 202 и durable queue record;
2. disconnect/reconnect/replay без новой unit;
3. restart и восстановление карточки;
4. четыре независимых таймера;
5. yielded не отображается failed;
6. discovery ceiling → implement/escalation/blocker;
7. changed files/checks и next operation обновляются по evidence;
8. pause/resume/cancel revision/CSRF/concurrency;
9. diagnostics existing/missing/retained;
10. sticky header и keyboard/aria states;
11. mobile layout;
12. отсутствие ключей, raw traceback и host paths в DOM/API;
13. исходный 15-минутный incident после reload/restart;
14. два повторных live browser прогона на чистых SQLite.

## 12. Критерий готовности

Web-часть готова только если одна большая задача переживает закрытие вкладки и
restart сервера, автоматически переходит между фазами, выполняет подтверждённую
мутацию и проверку либо возвращает точный blocker. Одного работающего SSE или
увеличенного timeout недостаточно. Результаты записываются в implementation status;
непроверенное не называется production.
