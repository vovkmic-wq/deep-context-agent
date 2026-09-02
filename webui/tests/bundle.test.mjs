import { readFile, stat } from "node:fs/promises";
import { resolve } from "node:path";

const root = resolve(import.meta.dirname, "..", "..");
const bundle = resolve(root, "src", "context_agent", "static", "app.js");
const html = resolve(root, "src", "context_agent", "static", "index.html");
const css = resolve(root, "src", "context_agent", "static", "styles.css");

for (const path of [bundle, html, css]) {
  const info = await stat(path);
  if (!info.isFile() || info.size === 0) throw new Error(`Missing bundle: ${path}`);
}
const total = (await stat(bundle)).size + (await stat(css)).size;
if (total > 500 * 1024) throw new Error(`JS+CSS exceeds 500 KiB: ${total}`);
const source = await readFile(bundle, "utf8");
if (!source.includes("/api/runtime") || !source.includes("x-csrf-token")) {
  throw new Error("Production bundle is missing required API/security wiring");
}
for (const required of [
  "/api/context/index",
  "/api/tasks/",
  "/api/providers/priority",
  '"/api/providers"',
  "dca_enter_send",
  "dca_chat_execution",
  "dca_chat_mode",
  "job_progress",
  "job_heartbeat",
  "job_deadline",
  "chat-job-list",
  "files-back",
  "custom-provider-form",
  "context-meter",
  "expected_sha256",
]) {
  if (!source.includes(required)) {
    throw new Error(`Production bundle is missing feature: ${required}`);
  }
}
const htmlSource = await readFile(html, "utf8");
if (htmlSource.includes('data-panel="audits"') || htmlSource.includes('id="audit-form"')) {
  throw new Error("Separate Autopilot tab must not be present");
}
if (!htmlSource.includes('id="chat-execution"')) {
  throw new Error("Chat execution mode selector is missing");
}
for (const mode of ["agent", "ask", "plan", "debug", "multitask"]) {
  if (!htmlSource.includes(`data-mode="${mode}"`)) {
    throw new Error(`Chat mode card is missing: ${mode}`);
  }
  if (!htmlSource.includes(`<option value="${mode}">`)) {
    throw new Error(`Chat mode option is missing: ${mode}`);
  }
}
for (const legacyMode of [
  "general",
  "audit",
  "coder",
  "tester",
  "reviewer",
  "debugger",
  "refactor",
  "security",
  "docs",
  "architect",
  "research",
]) {
  if (
    htmlSource.includes(`data-mode="${legacyMode}"`) ||
    htmlSource.includes(`<option value="${legacyMode}">`)
  ) {
    throw new Error(`Legacy chat mode is still present: ${legacyMode}`);
  }
}
if (!htmlSource.includes('id="context-meter"')) {
  throw new Error("Context usage meter is missing");
}
console.log(`bundle_ok bytes=${total}`);
