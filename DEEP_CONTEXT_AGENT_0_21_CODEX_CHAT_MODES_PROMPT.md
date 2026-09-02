# Deep Context Agent 0.21.0: five chat work modes

## Цель

Заменить прежний набор узких ролей единым переключателем из пяти понятных
режимов прямо в окне чата: `Agent`, `Ask`, `Plan`, `Debug`, `Multitask`.
Старые UI/API mode names удалить. Режим должен менять фактическую оркестрацию,
а не только декоративный текст.

## Обязательная реализация

1. `Agent` — основной режим. Выполняет задачу до проверяемого результата через
   ordinary turn либо persistent Autopilot, использует доступные workspace,
   retrieval, Web, provider и project-check tools. Запись разрешается только
   trusted `allow_write`; workspace/path/destructive/MCP policies не обходятся.
2. `Ask` — принудительный read-only single-turn. Изучает код/контекст и отвечает,
   но backend игнорирует попытку включить запись и не запускает реализацию.
3. `Plan` — принудительный read-only single-turn. Сначала собирает недостающие
   факты и задаёт только необходимые уточняющие вопросы, затем формирует план.
   Реализация начинается лишь после явного одобрения в `Agent`; текст «одобряю»
   сам по себе не повышает права текущего Plan-turn.
4. `Debug` — гипотезо-ориентированный последовательный режим. Формулирует
   гипотезу и наблюдаемые признаки; при trusted write может добавить минимальную
   обратимую диагностику, просит воспроизвести проблему, затем анализирует новый
   лог. Не маскирует отсутствие reproduction как найденную root cause.
5. `Multitask` — несколько независимых Web tasks одновременно. Каждый запрос
   получает отдельный namespaced child thread/job и SSE stream; серверный pool
   ограничен. Read/write остаётся явным, конкурентная запись в один checkout
   предупреждается, а worktree isolation должна использоваться оператором при
   конкурирующих изменениях.
6. Старые `general/audit/coder/tester/reviewer/debugger/refactor/security/docs/
   architect` удалить из HTML, TypeScript, Pydantic literals, runtime metadata,
   тестов и документации. Backend должен отклонять эти mode values как 422.
7. В composer добавить маленький круговой индикатор заполнения активного
   контекста. Backend возвращает bounded estimate по архиву thread,
   `estimated_tokens`, configured limit и percent; raw messages сверх страницы
   ради индикатора не передаются.
8. Объяснить, что Deep Agents автоматически суммаризует старую переписку и
   продолжает с компактным summary, сохраняя полную SQLite-историю для поиска.
   Индикатор является оценкой, а не счётчиком биллинговых токенов провайдера.
9. При выборе режима UI синхронизирует безопасные defaults: Ask/Plan выключают и
   блокируют write; Agent/Debug предлагают write, Multitask оставляет явный выбор.
   Backend всё равно применяет mode policy независимо от JavaScript.
10. Multitask UI не блокирует composer из-за одной активной задачи, показывает
    отдельный pending bubble для каждой и позволяет отменить все активные tasks.
11. Обновить global/system prompts, основное/Web-ТЗ, README, changelog/status,
    Python/Web versions и production bundle.
12. Добавить API, policy, concurrency, context-meter и negative legacy-mode
    тесты; выполнить полный Python/TypeScript/package/live контур.

## Критерии приёмки

- production HTML содержит ровно пять mode values и ни одного старого;
- Ask/Plan не меняют workspace даже при `allow_write=true` в raw API;
- Agent/Debug получают write только из trusted boolean и не расширяют sandbox;
- два Multitask сообщения запускаются до завершения первого и имеют разные
  task/thread IDs;
- context meter обновляется после загрузки/ввода, а API не возвращает secret или
  необрезанную дополнительную историю;
- автоматическая summarization Deep Agents остаётся включена, архив SQLite не
  удаляется;
- все проверки и live smoke подтверждены текущими логами до Git publication.
