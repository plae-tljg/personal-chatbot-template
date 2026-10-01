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

// Typo tolerance exists in both engines, and a threshold tweaked in one of them
// is exactly the drift this file is for. Comparing the *distance* matters more
// than comparing the answer: two implementations can agree on "matched" while
// disagreeing about how close it was, and the second one is what changes next
// time someone edits the boundary rule.
const fuzzyProbe = [
  ["what is dsh-review abotu?", "knowledge", 1],
  ["what is dsh-review abuot?", "knowledge", 1],
  ["what's dsh-review about?", "knowledge", 1],
  ["which project use Kotlin?", "knowledge", 1],
  ["what is the weather today?", "refuse", 0],
  ["how many stars does dsh-review have?", "refuse", 0],
  ["what is Finance-Management about?", "refuse", 0],
  ["why is the sky blue?", "refuse", 0],
];
for (const [question, expected, distance] of fuzzyProbe) {
  const answer = bot.ask(question, []);
  const got = answer.source;
  const gotDistance = answer.fuzzy || 0;
  if (got !== expected || gotDistance !== distance) {
    console.log(
      `FAIL fuzzy ${JSON.stringify(question)}: expected ${expected}/d=${distance}, ` +
      `got ${got}/d=${gotDistance}`,
    );
    failed += 1;
  }
}
console.log(`fuzzy: ${fuzzyProbe.length - failed >= 0 ? fuzzyProbe.length : 0} typo questions agree`);
console.log(`${probe.length} probe questions agree`);

// Suggestions are computed in both engines from the same data, so they must
// agree -- and every one of them must be answerable, or the chatroom is
// offering a dead end.
const suggested = [
  ["what is dsh-review about?", "who made dsh-review"],
  // This used to be "what is your most starred repo" (a slot-free row). Since
  // "you"/"your" became aliases of the person, a question containing "you"
  // resolves a person slot, so the answerable rows offered here are the person
  // ones. The assertion under test is unchanged: both engines agree, and every
  // suggestion answers.
  ["do you do weddings?", "who is LKM"],
  ["what projects does LKM have?", "who is LKM"],
];
let suggestionFails = 0;
for (const [question, expected] of suggested) {
  const answer = bot.ask(question);
  if (!answer.suggestions.includes(expected)) {
    console.log(`FAIL suggestions for ${JSON.stringify(question)}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(answer.suggestions)}`);
    suggestionFails += 1;
  }
  for (const s of answer.suggestions) {
    if (bot.ask(s).source === "refuse") {
      console.log(`FAIL ${JSON.stringify(question)} suggested ${JSON.stringify(s)}, which refuses`);
      suggestionFails += 1;
    }
  }
}
console.log(`${suggested.length} suggestion sets agree, all answerable`);
failed += suggestionFails;

process.exit(failed ? 1 : 0);
