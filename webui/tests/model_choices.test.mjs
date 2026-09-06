import assert from "node:assert/strict";
import { build } from "esbuild";
import { createContext, runInContext } from "node:vm";
import { resolve } from "node:path";

const result = await build({
  entryPoints: [resolve(import.meta.dirname, "../src/model_choices.ts")],
  bundle: true, write: false, format: "iife", globalName: "Choices",
});
class Option {
  constructor(text, value) { Object.assign(this, { text, value, disabled: false }); }
}
const context = createContext({ Option });
runInContext(result.outputFiles[0].text, context);
const select = {
  options: [], value: "",
  replaceChildren() { this.options = []; this.value = ""; },
  add(option) { this.options.push(option); },
  prepend(option) { this.options.unshift(option); if (option.selected) this.value = option.value; },
};
context.Choices.fillModelChoices(select, ["a", "b", "c", "d", "e", "f"], "f");
assert.equal(select.options.filter((item) => !item.disabled).length, 5);
assert.equal(select.value, "f");
assert.equal(select.options[0].disabled, true);
context.Choices.fillModelChoices(select, ["a", "b", "a"], "b", [
  { id: "a", date: "2026-01-01", date_basis: "catalog_created" },
]);
assert.equal(select.options.length, 2);
assert.equal(select.value, "b");
assert.match(select.options[0].text, /каталог/);
console.log("model_choices_ok");
