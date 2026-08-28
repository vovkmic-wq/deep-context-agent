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
  "/api/providers/priority",
  "dca_enter_send",
  "expected_sha256",
]) {
  if (!source.includes(required)) {
    throw new Error(`Production bundle is missing feature: ${required}`);
  }
}
console.log(`bundle_ok bytes=${total}`);
