# ТЗ 0.25: устойчивая задача, семантический intent и адаптивный выбор LLM

## Дополнение к завершению 0.32 — запланировано

Нормативное дополнение: [ТЗ R1–R6/C01–C18](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_SPEC.md)
и [порядок реализации](DEEP_CONTEXT_AGENT_0_32_RELEASE_COMPLETION_PROMPT.md).
Оно уточняет сохраняемый verification context, полный check plan, устойчивое
владение/reconciliation, сквозной REPAIR, Web/API и live/release gates.
Исходные A01–A34 и ограничения прав остаются обязательными. Опубликованная ветка
и 449 passed не означают закрытие production-приёмки. В этом этапе изменены
только документы; код новых требований ещё предстоит реализовать.

## Запланированное уточнение 0.32

[ТЗ жизненного цикла](TASK_LIFECYCLE_VERIFICATION_CONTEXT_TECHNICAL_SPEC.md)
L01–L08 дополняет identity/continuation: сохранённая задача и job имеют связанное
владение с независимым heartbeat и durable transfer. Resume сохраняет
VerificationContext и повторно валидирует root/окружение, не подменяя его Python
агента. История 0.25–0.31 остаётся; текущий runtime-дефект ещё требует реализации.

Дополнение после инцидента 2026-09-04: последующий этап 0.26 описан в
`AUTOPILOT_PROGRESS_RECOVERY_TECHNICAL_SPEC.md`. Он уточняет R1/R3 runtime-owned
handoff до hard limit, R4 execution escalation и R5 parent/child diagnostics.
Реализация 0.26.0 принята 2026-09-06 повторным live-сценарием длительного чтения
без model-written checkpoint; доказательства приведены в `IMPLEMENTATION_STATUS.md`.
Прежние тесты 0.25 сами по себе не являются доказательством этого исправления.

## R1. Identity и состояние

Одна согласованная задача имеет стабильный task ID, thread и workspace. Исходная
цель не заменяется запросом «продолжи». SQLite хранит план, remaining/next step,
подтверждённые tool operations, последний результат и причину остановки. Resume
восстанавливает состояние без зависимости от контекстного окна модели.
Миграция не удаляет старые таблицы. Изменение checkpoint и side effects требуют
актуального owner/revision/lease. Для terminal reconciliation после expiry
controller применяет только durable evidence той же execution generation и
CAS-защиту из L04/L05 этапа 0.32, без выдачи worker права записи. Новый owner,
cancel/revoke имеют приоритет; старые логи не импортируются как полномочия.

## R2. Intent и полномочия

Результат resolver: new_task/resume_task/side_question/clarify, выбранный task ID,
confidence, source и reason codes. Прямое указание UI на продолжение является
доверенным действием; текстовая классификация использует только direct instruction.
Точные команды решаются локально; неоднозначные отсылки к предыдущей работе могут
использовать ограниченный вызов LLM со строгим JSON, timeout и без tools.
Неизвестный ID, неоднозначность, malformed JSON/низкая confidence/ошибка провайдера
дают безопасное уточнение, не новую задачу и не аудит. LLM не расширяет права.
Resume пересекает ранее выданные полномочия с текущими controls и read-only intent.

## R3. Исполнение без навязанного аудита

Только workflow project-audit создаёт manifest полного аудита. Project-change и
project-test используют короткие execute units с компактным checkpoint. Допустимы
целевые чтения и ограниченный discovery для следующего пункта; отсутствие списка
файлов не означает необходимость читать весь workspace. План и checkpoint —
данные, а не новые разрешения. Завершение worker не означает завершение задачи.
Без подтверждённого завершения сохранять partial/blocked. Общая задача считается
completed после подтверждения оператором; подтверждения tools показывать отдельно
от самооценки LLM. Это консервативная граница, не автоматический semantic verifier.

## R4. Модели как ресурсы

Нормативный порядок: сначала high risk/high complexity -> reasoning; затем low
complexity без требуемых tools -> fast; остальные -> standard. Complexity/risk —
объяснимая оценка, не доказательство качества. Safety policy независима от tier.
Для auto выбора нужны явно настроенные профили provider/model/tier/capabilities.
Без профилей сохранить совместимость с существующей цепочкой и явно сообщать
configured-chain, не притворяться, что экономическая оптимизация уже настроена.
Ручной выбор отключает адаптивную замену модели, сохраняя согласованный fallback.

Профиль содержит tools, context_tokens, optional input/output USD per 1M tokens,
latency_ms, quality (операторская оценка), enabled; local-only задаётся общей policy.
Выбор учитывает объём текущего model request, полные схемы инструментов,
добавляемый task checkpoint/policy и запас вывода. Стоимость — оценка,
не реальный billing; неизвестный тариф не считается нулём и не проходит заданный
cost ceiling. Аналогично неизвестная latency не проходит strict latency budget.
Ограничения инструментов/контекста/local-only сохраняются и при fallback.
Провайдеры берутся только из включённой trusted-конфигурации. Ошибки временно
понижают доступность кандидата; наблюдаемая latency/успешность — сигнал надёжности,
не семантическая оценка ответа. При отсутствии кандидатов безопасный отказ.

## R5. Наблюдаемость

Web/CLI используют один код выбора. Runtime metadata показывает выбранные tier,
provider/model, причины, input estimate, estimated cost, fallback и degraded mode.
Журнал фактических model attempts продолжает хранить реальную модель и duration.
UI показывает active task ID, следующий шаг и состояние, не скрывает partial.
Полные цели, checkpoints и запросы не попадают в обычные status metadata.

## R6. Приёмка

Обязательны тесты: разработка -> лог -> точная длинная фраза -> та же задача;
перезапуск; сохранённый checkpoint; отсутствие audit manifest для project-change;
блокирование неоднозначного resume; Ask/read-only и старый owner; semantic timeout
и injection; risk-first; tools/context/cost/latency/local-only; fallback без replay;
ручной выбор. Live проводится на изолированном workspace и отдельной SQLite,
не на пользовательском Ozon. Запуск текущего сервера не подменять молча.
