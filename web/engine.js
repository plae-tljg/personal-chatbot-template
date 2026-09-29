/**
 * engine.js — the same ladder as the Python runtime, in the browser.
 *
 * Why two implementations of one engine is not a mistake here: the runtime is a
 * small interpreter over rows, and the rows are data. So the rows can ship as
 * JSON and the whole bot runs client-side — no server, no hosting cost, and no
 * model on the request path, which was already true.
 *
 * What makes it safe is `pc export` + `web/parity.mjs`: the exported snapshot
 * carries `content/tests.yaml`, and the JS engine runs those same cases. If the
 * two engines disagree, CI says so. A port without that check is a fork.
 *
 * Ported from: personal_chatbots/textnorm.py, resolve.py, frame.py, engine.py
 */

const CJK = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff]/;
const CJK_OR_ASCII = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff]|[A-Za-z0-9_]+/g;
const CAMEL = /(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])/g;

/** Split into comparable tokens. Must match textnorm.tokenize exactly. */
export function tokenize(text) {
  const out = [];
  for (const match of String(text).matchAll(/\{[^}]*\}|\w+/gu)) {
    const token = match[0];
    if (token.startsWith("{")) {
      out.push(token.toLowerCase());
    } else if (CJK.test(token)) {
      out.push(...(token.toLowerCase().match(CJK_OR_ASCII) || []));
    } else {
      // split the original casing: lowercasing first destroys camel boundaries
      for (const part of token.split("_")) {
        for (const piece of part.replace(CAMEL, " ").split(/\s+/)) {
          if (piece) out.push(piece.toLowerCase());
        }
      }
    }
  }
  return out;
}

export const normalize = (text) => tokenize(text).join(" ");
const compact = (text) => String(text).toLowerCase().replace(/[^a-z0-9]+/g, "");

// ---------------------------------------------------------------------------
// resolution
// ---------------------------------------------------------------------------

class AliasIndex {
  constructor(entities) {
    this.byType = new Map();
    this.lengths = new Map();
    for (const entity of entities) {
      if (!this.byType.has(entity.type)) this.byType.set(entity.type, new Map());
      const bucket = this.byType.get(entity.type);
      for (const alias of entity.aliases || []) {
        const tokens = tokenize(alias);
        if (!tokens.length) continue;
        const key = tokens.join(" ");
        if (!bucket.has(key)) bucket.set(key, []);
        bucket.get(key).push(entity);
      }
    }
    for (const [type, bucket] of this.byType) {
      const sizes = new Set();
      for (const key of bucket.keys()) sizes.add(key.split(" ").length);
      this.lengths.set(type, [...sizes].sort((a, b) => b - a));
    }
  }

  matches(tokens, type) {
    const bucket = this.byType.get(type);
    if (!bucket) return [];
    const out = [];
    for (const size of this.lengths.get(type) || []) {
      for (let start = 0; start + size <= tokens.length; start++) {
        const entities = bucket.get(tokens.slice(start, start + size).join(" "));
        if (!entities) continue;
        for (const entity of entities) out.push({ start, end: start + size, entity });
      }
    }
    return out;
  }

  matchesAny(tokens) {
    const out = [];
    for (const type of this.byType.keys()) out.push(...this.matches(tokens, type));
    return out;
  }
}

/** Longest non-overlapping matches. Identical spans from different entities
 *  both survive, because that is ambiguity rather than a duplicate. */
function select(matches, taken) {
  const chosen = [];
  const sorted = [...matches].sort((a, b) => (b.end - b.start) - (a.end - a.start) || a.start - b.start);
  for (const match of sorted) {
    if (taken.some(([s, e]) => match.start < e && s < match.end)) continue;
    if (chosen.some((c) => match.start < c.end && c.start < match.end
      && !(match.start === c.start && match.end === c.end))) continue;
    chosen.push(match);
  }
  return chosen;
}

/** Resolve every declared slot, then return the question's skeleton. */
function resolve(tokens, slots, index) {
  const taken = [];
  const assignments = [];
  const chosen = {};
  for (const [slot, type] of Object.entries(slots)) {
    const candidates = select(index.matches(tokens, type), taken);
    if (!candidates.length) return null;
    const distinct = new Set(candidates.map((c) => c.entity.key));
    if (distinct.size > 1) return null; // two things could fill it: refuse
    const best = candidates[0];
    taken.push([best.start, best.end]);
    assignments.push([slot, best]);
    chosen[slot] = best.entity;
  }

  const byStart = new Map(assignments.map(([slot, m]) => [m.start, [slot, m.end]]));
  const parts = [];
  let cursor = 0;
  while (cursor < tokens.length) {
    const hit = byStart.get(cursor);
    if (hit) {
      parts.push(`{${hit[0]}}`);
      cursor = hit[1];
    } else {
      parts.push(tokens[cursor]);
      cursor += 1;
    }
  }
  return { skeleton: parts.join(" "), slots: chosen };
}

function singleEntity(tokens, index) {
  const matches = select(index.matchesAny(tokens), []);
  const distinct = new Map();
  for (const match of matches) distinct.set(match.entity.key, match.entity);
  return distinct.size === 1 ? [...distinct.values()][0] : null;
}

function leftover(tokens, entity, index) {
  const matches = index.matches(tokens, entity.type).filter((m) => m.entity.key === entity.key);
  if (!matches.length) return tokens.filter((t) => !t.startsWith("{"));
  const best = matches.reduce((a, b) => (b.end - b.start > a.end - a.start ? b : a));
  return [...tokens.slice(0, best.start), ...tokens.slice(best.end)].filter((t) => !t.startsWith("{"));
}

// ---------------------------------------------------------------------------
// the frame: cross-turn, derived, not stored
// ---------------------------------------------------------------------------

const ORDINALS = {
  first: 0, "1st": 0, second: 1, "2nd": 1, third: 2, "3rd": 2, fourth: 3, "4th": 3,
  fifth: 4, "5th": 4, sixth: 5, "6th": 5, seventh: 6, "7th": 6, eighth: 7, "8th": 7,
  ninth: 8, "9th": 8, tenth: 9, "10th": 9, last: -1,
  一: 0, 二: 1, 三: 2, 四: 3, 五: 4,
};
const PRONOUNS = new Set(["it", "that", "this", "that one", "this one", "the same", "the same one"]);

function ordinalIndex(tokens) {
  const words = tokens.filter((w) => w !== "the" && w !== "one");
  if (words.length !== 1) return null;
  return Object.prototype.hasOwnProperty.call(ORDINALS, words[0]) ? ORDINALS[words[0]] : null;
}

const PHRASES = (() => {
  const set = new Set(PRONOUNS);
  for (const word of Object.keys(ORDINALS)) {
    set.add(word);
    set.add(`the ${word}`);
    set.add(`${word} one`);
    set.add(`the ${word} one`);
  }
  return [...set].map((p) => p.split(" ")).sort((a, b) => b.length - a.length || (a < b ? -1 : 1));
})();

/** Build a frame from the previous answers. `turns` are {citations:[{key}]}. */
export function frameFrom(turns, index, window = 6) {
  const frame = { subject: null, items: [] };
  const answers = turns.filter((t) => t.role === "assistant").slice(-window);
  let blocked = false;
  for (const row of [...answers].reverse()) {
    const keys = (row.citations || []).map((c) => c.key).filter(Boolean);
    const entities = keys.map((k) => index.entityByKey.get(k)).filter(Boolean);
    if (!entities.length) continue;
    if (entities.length >= 2) {
      if (!frame.items.length) frame.items = entities;
      blocked = true;
    } else if (!frame.subject && !blocked) {
      frame.subject = entities[0];
    }
    if (frame.items.length && frame.subject) break;
  }
  return frame;
}

/** Rewrite referring expressions into the entities they point at. */
export function applyFrame(tokens, frame) {
  if (!frame || (!frame.subject && !frame.items.length)) return { tokens: [...tokens], refs: {} };

  const found = [];
  let cursor = 0;
  while (cursor < tokens.length) {
    let hit = null;
    for (const phrase of PHRASES) {
      const end = cursor + phrase.length;
      if (end > tokens.length) continue;
      if (phrase.some((w, i) => tokens[cursor + i] !== w)) continue;
      const ordinal = ordinalIndex(phrase);
      const entity = ordinal !== null
        ? (ordinal < 0 ? frame.items[frame.items.length + ordinal] : frame.items[ordinal])
        : frame.subject;
      if (!entity) continue;
      hit = { start: cursor, end, phrase: phrase.join(" "), entity };
      break;
    }
    if (!hit) { cursor += 1; continue; }
    found.push(hit);
    cursor = hit.end;
  }

  const rewritten = [...tokens];
  for (const ref of [...found].sort((a, b) => b.start - a.start)) {
    rewritten.splice(ref.start, ref.end - ref.start, ...tokenize(ref.entity.name));
  }
  const refs = {};
  for (const ref of found) refs[ref.phrase] = ref.entity.key;
  return { tokens: rewritten, refs };
}

// ---------------------------------------------------------------------------
// rendering
// ---------------------------------------------------------------------------

const stringify = (v) => {
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (Array.isArray(v)) return v.map(stringify).join(", ");
  return v === null || v === undefined ? "" : String(v);
};

const render = (template, values) =>
  String(template).replace(/\{[^}]*\}/g, (whole) =>
    Object.prototype.hasOwnProperty.call(values, whole) ? values[whole] : whole);

function entityValues(slot, entity) {
  const values = {
    [`{${slot}}`]: entity.name,
    [`{${slot}.key}`]: entity.key,
    [`{${slot}.name}`]: entity.name,
    [`{${slot}.summary}`]: entity.summary || "",
    [`{${slot}.url}`]: entity.url || "",
    [`{${slot}.entity_type}`]: entity.type,
  };
  for (const [k, v] of Object.entries(entity.attrs || {})) values[`{${slot}.attrs.${k}}`] = stringify(v);
  return values;
}

function rowValues(entity) {
  const values = {
    "{key}": entity.key, "{name}": entity.name, "{summary}": entity.summary || "",
    "{url}": entity.url || "", "{entity_type}": entity.type,
  };
  for (const [k, v] of Object.entries(entity.attrs || {})) values[`{attrs.${k}}`] = stringify(v);
  return values;
}

const cite = (e) => ({ key: e.key, label: e.name, url: e.url || "" });

// ---------------------------------------------------------------------------
// the bot
// ---------------------------------------------------------------------------

const STOPWORDS = new Set(`a an and are about do does for from how i in is it me my of on or tell that the
  to what when where which who why you your have has can could would`.split(/\s+/));
const FILLER = new Set(["what", "is", "who", "about", "the", "tell", "me", "a", "an", "of",
  "do", "you", "know", "and", "also", "then", "next", "please"]);

export function createBot(data) {
  const index = new AliasIndex(data.entities);
  // key -> entity, for resolving the citations a frame reads back
  index.entityByKey = new Map(data.entities.map((e) => [e.key, e]));
  const byType = new Map();
  for (const e of data.entities) {
    if (!byType.has(e.type)) byType.set(e.type, []);
    byType.get(e.type).push(e);
  }
  const bot = data.bot;
  const knowledge = data.knowledge;

  function runAction(row, slots) {
    const action = row.action || {};
    if (action.kind === "refuse") return { text: action.template || bot.refuse_template, citations: [] };

    if (action.kind === "answer") {
      const values = {};
      const cited = [];
      for (const [name, entity] of Object.entries(slots)) {
        Object.assign(values, entityValues(name, entity));
        cited.push(entity);
      }
      const citations = cited.map(cite);
      for (const extra of row.citations || []) {
        const url = render(extra, values);
        if (url && !citations.some((c) => c.url === url)) citations.push({ key: url, label: url, url });
      }
      return { text: render(action.template, values), citations };
    }

    if (action.kind === "list") {
      let entities;
      if (action.link_type) {
        const slotName = Object.keys(row.slots || {})[0];
        const from = slots[slotName];
        if (!from) return { text: "", citations: [] };
        const links = data.links || [];
        const want = action.direction === "out" ? "from" : "to";
        const other = want === "from" ? "to" : "from";
        const ids = links
          .filter((l) => l.type === action.link_type && l[want] === from.key)
          .map((l) => l[other]);
        entities = ids.map((k) => index.entityByKey.get(k)).filter((e) => e && e.type === action.entity_type);
      } else {
        entities = [...(byType.get(action.entity_type) || [])];
        const order = String(action.order_by || "id");
        const desc = /\sdesc$/i.test(order);
        const column = order.split(/\s+/)[0];
        const value = (e) => (column.startsWith("attrs.")
          ? (e.attrs || {})[column.slice(6)]
          : (column === "name" ? e.name : e.key));
        entities.sort((a, b) => {
          const x = value(a); const y = value(b);
          if (x === y) return 0;
          if (x === undefined || x === null) return 1;
          if (y === undefined || y === null) return -1;
          const cmp = typeof x === "number" && typeof y === "number" ? x - y : String(x) < String(y) ? -1 : 1;
          return desc ? -cmp : cmp;
        });
      }
      const limit = Number(action.limit || 0);
      if (limit) entities = entities.slice(0, limit);
      if (!entities.length) return { text: "", citations: [] };
      const lines = entities.map((e) => "• " + render(action.template || "{name}", rowValues(e)));
      return { text: lines.join("\n"), citations: entities.map(cite) };
    }

    return { text: "", citations: [] };
  }

  function rungKnowledge(tokens) {
    for (const row of knowledge) {
      if (row.locale && row.locale !== bot.default_locale) continue;
      const resolved = resolve(tokens, row.slots || {}, index);
      if (!resolved) continue;
      const joined = tokens.join(" ");
      const require = (row.match?.require || []).map(normalize).filter(Boolean);
      const exclude = (row.match?.exclude || []).map(normalize).filter(Boolean);
      if (require.some((w) => !joined.includes(w))) continue;
      if (exclude.some((w) => joined.includes(w))) continue;
      const patterns = new Set((row.patterns || []).map(normalize));
      if (!patterns.has(resolved.skeleton)) continue;
      const result = runAction(row, resolved.slots);
      if (!result.text) continue;
      return {
        text: result.text, source: "knowledge", citations: result.citations,
        matched: row.slug, slots: Object.fromEntries(
          Object.entries(resolved.slots).map(([k, v]) => [k, v.key])),
      };
    }
    return null;
  }

  function rungEntity(tokens) {
    const entity = singleEntity(tokens, index);
    if (!entity || !entity.summary) return null;
    const rest = new Set(leftover(tokens, entity, index));
    for (const token of rest) if (!FILLER.has(token)) return null;
    return {
      text: `${entity.name} — ${entity.summary}`, source: "entity",
      citations: [cite(entity)], matched: entity.key, slots: {},
    };
  }

  function rungSearch(tokens) {
    const entity = singleEntity(tokens, index);
    if (!entity) return null;
    const words = leftover(tokens, entity, index)
      .filter((w) => w.length >= 3 && !STOPWORDS.has(w) && !FILLER.has(w))
      .slice(0, 3);
    if (!words.length) return null;
    let best = null;
    for (const doc of data.documents) {
      if (doc.entity_key !== entity.key) continue;
      const haystack = `${doc.title}\n${doc.body}`;
      if (!words.every((w) => haystack.includes(w))) continue;
      best = doc;
      break;
    }
    if (!best) return null;
    const body = best.body.split(/\s+/).filter(Boolean).join(" ");
    return {
      text: `From the ${best.title} README: ${body.slice(0, 280)}${body.length > 280 ? "…" : ""}`,
      source: "search", citations: [cite(entity)], matched: best.slug, slots: {},
    };
  }

  const rungRefuse = () => ({ text: bot.refuse_template, source: "refuse", citations: [], matched: "", slots: {} });

  const RUNGS = { knowledge: rungKnowledge, entity: rungEntity, search: rungSearch, refuse: rungRefuse };

  /** Answer one question. `turns` is the session so far, used only to rewrite
   *  referring expressions — no rung below reads it, which is why context cost
   *  no state. */
  function ask(question, turns = []) {
    const started = performance.now();
    const frame = turns.length ? frameFrom(turns, index) : null;
    const { tokens, refs } = applyFrame(tokenize(question), frame);

    let answer = null;
    for (const name of bot.ladder) {
      const rung = RUNGS[name];
      if (!rung) throw new Error(`bot.json: rung ${name} is not implemented in the browser engine`);
      answer = rung(tokens);
      if (answer && (answer.text || answer.source === "refuse")) break;
    }
    if (!answer || !(answer.text || answer.source === "refuse")) answer = rungRefuse();
    return { ...answer, refs, latency_ms: Math.round((performance.now() - started) * 1000) / 1000, tokens: 0 };
  }

  /** Run the exported content/tests.yaml — the same cases Python runs. */
  function selfCheck() {
    const results = [];
    for (const test of data.tests || []) {
      if ((test.status || "active") !== "active") continue;
      const answer = ask(test.question);
      const notes = [];
      const expect = test.expect || {};
      const text = answer.text.toLowerCase();
      if ("refuses" in expect && (answer.source === "refuse") !== !!expect.refuses) {
        notes.push(`expected refuses=${!!expect.refuses}, got ${answer.source}`);
      }
      if (expect.source && answer.source !== expect.source) {
        notes.push(`expected source=${expect.source}, got ${answer.source}`);
      }
      for (const [slot, key] of Object.entries(expect.resolves || {})) {
        if (answer.slots[slot] !== key) notes.push(`expected ${slot} -> ${key}, got ${answer.slots[slot]}`);
      }
      for (const needle of expect.answer_contains || []) {
        if (!text.includes(String(needle).toLowerCase())) notes.push(`missing ${JSON.stringify(needle)}`);
      }
      for (const needle of expect.answer_excludes || []) {
        if (text.includes(String(needle).toLowerCase())) notes.push(`unexpected ${JSON.stringify(needle)}`);
      }
      for (const needle of expect.cites || []) {
        const n = String(needle).toLowerCase();
        if (!answer.citations.some((c) =>
          c.key.toLowerCase().includes(n) || (c.url || "").toLowerCase().includes(n)
          || (c.label || "").toLowerCase().includes(n))) notes.push(`no citation matching ${JSON.stringify(needle)}`);
      }
      results.push({ slug: test.slug, passed: notes.length === 0, notes, got: answer });
    }
    return results;
  }

  return { ask, selfCheck, index, data };
}

export { compact };
