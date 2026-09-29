#!/usr/bin/env node
/**
 * widget_harness.mjs — run a chatroom page's inline script without a browser.
 *
 * Written after `turnEl` threw `ReferenceError: r is not defined` in the served
 * chatroom. The throw happened while building the answer element, so the reply
 * never rendered and the "…" placeholder stayed on screen forever: a crash that
 * looked like the bot being slow.
 *
 * Nothing caught it. `node --check` sees valid syntax, the Python tests do not
 * touch the page, and the browser was the only place it showed up. So this does
 * the smallest thing that would have: extract the page's script, give it a DOM
 * stub, call the render functions with real answer shapes, and fail on a throw.
 *
 *   node tests/widget_harness.mjs <page.html> <tmp.mjs>
 */

import { readFileSync, writeFileSync, unlinkSync } from "node:fs";
import { pathToFileURL } from "node:url";
import path from "node:path";

const [pagePath, tmpPath] = process.argv.slice(2);
if (!pagePath || !tmpPath) {
  console.error("usage: widget_harness.mjs <page.html> <tmp.mjs>");
  process.exit(2);
}

// ---------------------------------------------------------------------------
// a DOM stub, just enough for the page to boot and render a turn
// ---------------------------------------------------------------------------

const node = (tag = "div") => {
  const el = {
    tagName: tag,
    children: [],
    dataset: {},
    style: {},
    className: "",
    _html: "",
    textContent: "",
    scrollTop: 0,
    scrollHeight: 0,
    classList: { add() {}, remove() {}, contains: () => false },
    get innerHTML() { return this._html; },
    set innerHTML(v) {
      this._html = String(v);
      // querySelectorAll is used to wire up suggestion buttons; a real parser
      // is not needed to prove the render did not throw.
      this._matches = [...String(v).matchAll(/data-ask="([^"]*)"/g)].map((m) => ({
        dataset: { ask: m[1] }, addEventListener() {},
      }));
    },
    addEventListener() {},
    appendChild(child) { this.children.push(child); return child; },
    replaceWith() {},
    remove() {},
    scrollIntoView() {},
    querySelector: () => null,
    querySelectorAll: (sel) => (sel.includes("data-ask") ? (el._matches || []) : []),
    focus() {},
    closest: () => null,
  };
  return el;
};

const byId = new Map();
const getEl = (id) => {
  if (!byId.has(id)) byId.set(id, node());
  return byId.get(id);
};

globalThis.document = {
  getElementById: getEl,
  createElement: node,
  title: "",
  addEventListener() {},
};
globalThis.window = { addEventListener() {}, location: { hash: "" } };
globalThis.location = globalThis.window.location;
globalThis.performance = globalThis.performance || { now: () => Date.now() };
if (!globalThis.crypto) globalThis.crypto = {};
if (!globalThis.crypto.randomUUID) {
  globalThis.crypto.randomUUID = () => "00000000-0000-4000-8000-000000000000";
}
globalThis.localStorage = {
  _d: new Map(),
  getItem(k) { return this._d.has(k) ? this._d.get(k) : null; },
  setItem(k, v) { this._d.set(k, String(v)); },
};

const page = readFileSync(pagePath, "utf8");
const script = [...page.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)]
  .map((m) => m[1])
  .filter((body) => body.includes("function turnEl") || body.includes("const turnEl"))
  .pop();

if (!script) {
  console.error(`no render script found in ${pagePath}`);
  process.exit(2);
}

// the page's own data, when it fetches one, and canned API responses
const dataPath = path.join(path.dirname(pagePath), "data.json");
let pageData = null;
try { pageData = JSON.parse(readFileSync(dataPath, "utf8")); } catch { /* served page has none */ }

globalThis.fetch = async (url) => {
  const u = String(url);
  let body = {};
  if (u.includes("data.json") && pageData) body = pageData;
  else if (u.includes("/api/config")) body = { name: "T", tagline: "", level: 1, ladder: ["knowledge", "refuse"], max_citations: 3 };
  else if (u.includes("/api/stats")) body = { knowledge: 1, entities: 1, messages: 0, kappa: 1, refused: 0, unresolved: 0, ladder: ["knowledge"] };
  else if (u.includes("/api/sessions")) body = { sessions: [], messages: [] };
  return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) };
};

// The two pages name their helpers differently; export whatever exists rather
// than pinning a name the page is free to change.
writeFileSync(tmpPath, script + `
;globalThis.__w = { turnEl };
for (const name of ["suggestions", "tryRow", "renderEmpty", "renderTranscript"]) {
  try { globalThis.__w[name] = eval(name); } catch { /* not in this page */ }
}
`);

let widget;
try {
  await import(pathToFileURL(tmpPath).href);
  widget = globalThis.__w;
} finally {
  try { unlinkSync(tmpPath); } catch { /* the import already read it */ }
}

if (!widget || typeof widget.turnEl !== "function") {
  console.error("the page script did not expose turnEl — did the render function get renamed?");
  process.exit(1);
}

// ---------------------------------------------------------------------------
// the shapes a real answer takes
// ---------------------------------------------------------------------------

const CASES = [
  ["a refusal", {
    text: "I don't have that.", source: "refuse", matched: "",
    citations: [], refs: {}, unresolved: true, suggestions: ["who is LKM"],
    latency_ms: 0.2,
  }],
  ["a fallback that failed", {
    // the exact shape that broke: a live answer carrying fallback_error
    text: "I don't have that.", source: "refuse", matched: "",
    citations: [], refs: {}, unresolved: true, suggestions: [],
    latency_ms: 700, fallback_error: "HTTP 403 Forbidden error code: 1010",
  }],
  ["a table answer with citations", {
    text: "dsh-review — a review tab.", source: "knowledge", matched: "repo.about",
    citations: [{ key: "a/b", label: "b", url: "https://x" }],
    refs: { "the second one": "a/b" }, unresolved: false,
    suggestions: ["who made b"], latency_ms: 0.3,
  }],
  ["a transcript row (no live-only fields)", {
    // replayed turns have no `unresolved` and no suggestions
    text: "old answer", source: "knowledge", matched: "repo.about",
    citations: [], refs: {},
  }],
  ["an empty answer", { text: "", source: "refuse", citations: [] }],
];

let failed = 0;
for (const [label, answer] of CASES) {
  try {
    const el = widget.turnEl("why?", answer, { live: true });
    const html = el.innerHTML || "";
    if (!html.includes("badge")) {
      console.error(`FAIL ${label}: rendered nothing (no badges in output)`);
      failed += 1;
    }
  } catch (err) {
    console.error(`FAIL ${label}: ${err.constructor.name}: ${err.message}`);
    failed += 1;
  }
}

console.log(`${CASES.length - failed}/${CASES.length} answer shapes render`);
process.exit(failed ? 1 : 0);
