"use strict";

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type Payload = Record<string, Json>;

let csrfToken = "";
let activeChatTask = "";

function element<T extends HTMLElement>(id: string): T {
  const node = document.getElementById(id);
  if (!node) throw new Error(`Missing UI element: ${id}`);
  return node as T;
}

function value(id: string): string {
  return element<HTMLInputElement | HTMLTextAreaElement>(id).value;
}

function checked(id: string): boolean {
  return element<HTMLInputElement>(id).checked;
}

function showToast(message: string): void {
  const node = element<HTMLDivElement>("toast");
  node.textContent = message;
  node.classList.add("visible");
  window.setTimeout(() => node.classList.remove("visible"), 3_500);
}

async function api<T extends Payload>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("content-type", "application/json");
  if (options.method && options.method !== "GET") {
    headers.set("x-csrf-token", csrfToken);
  }
  const response = await fetch(path, { ...options, headers });
  const body = (await response.json().catch(() => ({}))) as T & {
    error?: { message?: string };
  };
  if (!response.ok) {
    throw new Error(body.error?.message || `HTTP ${response.status}`);
  }
  return body;
}

function card(title: string, body: string): HTMLElement {
  const node = document.createElement("article");
  node.className = "card";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const text = document.createElement("p");
  text.textContent = body;
  node.append(heading, text);
  return node;
}

function streamTask(
  taskId: string,
  handler: (name: string, data: Payload) => void,
): void {
  const stream = new EventSource(`/api/events/${encodeURIComponent(taskId)}`);
  const terminal = new Set(["completed", "cancelled", "failed"]);
  for (const name of [
    "started",
    "message",
    "audit_progress",
    "result",
    "completed",
    "cancelled",
    "failed",
  ]) {
    stream.addEventListener(name, (rawEvent) => {
      const event = rawEvent as MessageEvent<string>;
      handler(name, JSON.parse(event.data) as Payload);
      if (terminal.has(name)) stream.close();
    });
  }
  stream.onerror = () => {
    stream.close();
    showToast("Поток событий прерван");
  };
}

function text(data: Json | undefined): string {
  return data === undefined || data === null ? "" : String(data);
}

async function refreshAudits(): Promise<void> {
  const rows = element<HTMLTableSectionElement>("audit-rows");
  rows.replaceChildren();
  const result = await api<{ items: Payload[]; request_id: string }>(
    "/api/audits",
  );
  for (const item of result.items) {
    const row = document.createElement("tr");
    const updated = new Date(Number(item.updated_at) * 1_000).toLocaleString();
    for (const entry of [item.id, item.mode, item.status, updated]) {
      const cell = document.createElement("td");
      cell.textContent = text(entry);
      row.append(cell);
    }
    rows.append(row);
  }
}

async function bootstrap(): Promise<void> {
  const runtime = await api<Payload>("/api/runtime");
  csrfToken = text(runtime.csrf_token);
  element("runtime-badge").textContent = `${text(runtime.provider)}/${text(runtime.model)}`;
  const metrics = element("overview-grid");
  for (const [name, metricValue] of [
    ["Версия", runtime.version],
    ["Режим аудита", runtime.audit_mode_default],
    ["Задачи", runtime.active_tasks],
    ["Workspace", runtime.workspace],
  ] as Array<[string, Json]>) {
    const node = document.createElement("div");
    node.className = "metric";
    const strong = document.createElement("strong");
    strong.textContent = text(metricValue);
    node.append(strong, document.createTextNode(name));
    metrics.append(node);
  }
  element("health").textContent = JSON.stringify(await api("/api/health"), null, 2);
  const settings = await api<{ values: Payload; request_id: string }>(
    "/api/settings",
  );
  element("settings-output").textContent = JSON.stringify(settings.values, null, 2);
  await refreshAudits();
}

document.querySelectorAll<HTMLButtonElement>("nav button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach((item) => {
      item.removeAttribute("aria-current");
    });
    document.querySelectorAll(".panel").forEach((panel) => {
      panel.classList.remove("active");
    });
    button.setAttribute("aria-current", "page");
    element(text(button.dataset.panel)).classList.add("active");
  });
});

element<HTMLFormElement>("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await api<Payload>("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        query: value("chat-query"),
        thread_id: value("thread-id"),
        auto_context: checked("auto-context"),
      }),
    });
    activeChatTask = text(result.task_id);
    const pending = document.createElement("div");
    pending.className = "message";
    pending.textContent = "Агент работает…";
    element("chat-output").append(pending);
    streamTask(activeChatTask, (name, data) => {
      if (name === "message") pending.textContent = text(data.text);
      if (name === "failed") pending.textContent = text(data.message);
      if (name === "cancelled") pending.textContent = "Отменено";
    });
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Ошибка чата");
  }
});

element("cancel-chat").addEventListener("click", async () => {
  if (!activeChatTask) return;
  try {
    await api(`/api/chat/${encodeURIComponent(activeChatTask)}/cancel`, {
      method: "POST",
    });
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Ошибка отмены");
  }
});

element<HTMLFormElement>("context-form").addEventListener(
  "submit",
  async (event) => {
    event.preventDefault();
    const output = element("context-output");
    output.replaceChildren();
    try {
      const query = encodeURIComponent(value("context-query"));
      const limit = Number(value("context-limit"));
      const result = await api<{ items: Payload[]; request_id: string }>(
        `/api/context/search?query=${query}&limit=${limit}`,
      );
      for (const item of result.items) {
        output.append(
          card(
            `${text(item.source)} · chunk ${text(item.chunk_index)}`,
            text(item.content),
          ),
        );
      }
      if (!result.items.length) {
        output.append(card("Нет результатов", "Попробуйте более точный запрос."));
      }
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Ошибка поиска");
    }
  },
);

element("index-workspace").addEventListener("click", async () => {
  try {
    const result = await api<Payload>("/api/context/index", {
      method: "POST",
      body: JSON.stringify({ path: "/workspace" }),
    });
    streamTask(text(result.task_id), (name, data) => {
      if (name === "result") showToast(JSON.stringify(data.result));
    });
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Ошибка индексации");
  }
});

element<HTMLFormElement>("audit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const result = await api<Payload>("/api/audits", {
      method: "POST",
      body: JSON.stringify({
        objective: value("audit-objective"),
        thread_id: value("audit-thread"),
        allow_write: checked("audit-write"),
        batch_size: Number(value("audit-batch-size")),
        include_patterns: value("audit-include")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
        exclude_patterns: value("audit-exclude")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      }),
    });
    const output = element("audit-progress");
    output.textContent = "Аудит запущен…";
    streamTask(text(result.task_id), (name, data) => {
      if (name === "audit_progress") {
        output.textContent = `Пачка ${text(data.batch_number)}: ${text(data.reviewed)}/${text(data.total)}; pending ${text(data.pending)}; excluded ${text(data.excluded)}`;
      }
      if (name === "result") output.textContent = text(data.result);
      if (name === "completed") void refreshAudits();
      if (name === "failed") output.textContent = text(data.message);
    });
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Ошибка аудита");
  }
});

element("refresh-audits").addEventListener("click", () => {
  void refreshAudits().catch((error: Error) => showToast(error.message));
});

element<HTMLFormElement>("files-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const output = element("files-output");
  output.replaceChildren();
  try {
    const result = await api<{ items: Payload[]; request_id: string }>(
      `/api/files?path=${encodeURIComponent(value("files-path"))}`,
    );
    for (const item of result.items) {
      output.append(
        card(
          text(item.path),
          item.type === "file" ? `${text(item.size)} байт` : "Каталог",
        ),
      );
    }
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Ошибка файлов");
  }
});

element("load-providers").addEventListener("click", async () => {
  const output = element("providers-output");
  output.replaceChildren();
  try {
    const result = await api<{ items: Payload[]; request_id: string }>(
      "/api/providers",
    );
    for (const item of result.items) {
      output.append(
        card(
          `${Number(item.priority) + 1}. ${text(item.provider)}/${text(item.model)}`,
          `${text(item.base_url)} · ключ ${text(item.api_key)}`,
        ),
      );
    }
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Ошибка провайдеров");
  }
});

element("live-doctor").addEventListener("click", async () => {
  if (!window.confirm("Live-check вызывает платный API. Продолжить?")) return;
  try {
    const result = await api<Payload>("/api/providers/doctor", {
      method: "POST",
      body: JSON.stringify({ live: true }),
    });
    streamTask(text(result.task_id), (name, data) => {
      if (name === "result") showToast(JSON.stringify(data.result));
      if (name === "failed") showToast(text(data.message));
    });
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Ошибка live-check");
  }
});

void bootstrap().catch((error: Error) => {
  element("runtime-badge").textContent = "Ошибка";
  showToast(error.message);
});
