type ModelDetail = { id?: unknown; date?: unknown; date_basis?: unknown };

export function fillModelChoices(
  select: HTMLSelectElement,
  models: string[],
  current: string,
  details: ModelDetail[] = [],
): void {
  const choices = [...new Set(models)].slice(0, 5);
  select.replaceChildren();
  for (const model of choices) {
    const meta = details.find((item) => String(item.id) === model);
    const date = String(meta?.date || "").slice(0, 10);
    const basis = String(meta?.date_basis || "unknown");
    const label = date
      ? `${model} · ${date} (${basis === "catalog_created" ? "каталог" : "релиз"})`
      : model;
    select.add(new Option(label, model));
  }
  if (current && !choices.includes(current)) {
    // Preserve the active model as status, never as a sixth selectable model.
    const status = new Option(`Сейчас: ${current} (вне списка последних)`, current);
    status.disabled = true;
    status.selected = true;
    select.prepend(status);
  } else if (current) {
    select.value = current;
  }
}
