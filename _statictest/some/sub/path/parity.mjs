#!/usr/bin/env node
/**
 * parity.mjs — check the browser engine against the Python one.
 *
 * Two implementations of one interpreter is a fork unless something forces them
 * to agree. This is that something: it loads the same exported snapshot, runs the
 * same `content/tests.yaml` the Python runtime runs, and exits non-zero on any
 * disagreement. CI calls it.
 *
 *     python -m personal_chatbots export && node web/parity.mjs
 *
 * A port without this check drifts within a month — someone adds an ordinal to
 * vocabulary.py, or changes the ambiguity rule in resolve.py, and the browser
 * quietly keeps the old behaviour with no test able to notice.
 */

import { readFileSync } from "node:fs";
import { createBot } from "./engine.js";

const data = JSON.parse(readFileSync(new URL("./data.json", import.meta.url), "utf8"));
const bot = createBot(data);

let failed = 0;
for (const result of bot.selfCheck()) {
  const mark = result.passed ? "ok  " : "FAIL";
  console.log(`${mark} ${result.slug}`);
  if (!result.passed) {
    console.log(`       ${JSON.stringify(result.got.text.slice(0, 70))}  [${result.got.source}]`);
    for (const note of result.notes) console.log(`       - ${note}`);
    failed += 1;
  }
}

const total = bot.selfCheck().length;
console.log(`\n${total - failed}/${total} cases agree with the Python runtime`);

// A few extra questions with no frozen case, to catch drift the suite misses.
const probe = [
  ["what is dsh-review about?", "knowledge"],
  ["dsh-review", "entity"],
  ["do you do weddings?", "refuse"],
  ["what is Finance-Management about?", "refuse"],
];
for (const [question, expected] of probe) {
  const got = bot.ask(question).source;
  if (got !== expected) {
    console.log(`FAIL probe ${JSON.stringify(question)}: expected ${expected}, got ${got}`);
    failed += 1;
  }
}
console.log(`${probe.length} probe questions agree`);

process.exit(failed ? 1 : 0);
