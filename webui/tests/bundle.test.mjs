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
  "job_progress",
  "chat-job-list",
  "files-back",
  "custom-provider-form",
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
console.log(`bundle_ok bytes=${total}`);
