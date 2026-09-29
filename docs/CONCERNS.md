# Concerns

The doubts that shaped the design, each with the answer we chose and the
condition that would make us change it. Written down because a design whose
concerns are unwritten gets re-litigated every week.

---

## C1 — AI cost: how many times do we wake the model?

**The concern.** Token price is the visible cost, but the controllable variable
is **how often the model is invoked at all**. If invocation count scales with
traffic or with data changes, the bill is unbounded and you find out too late.

**Where invocations come from, and the design's answer:**

| Source | Count scales with | v1 |
|---|---|---|
| **A. Runtime fallback** — model answers a visitor live | requests × (1 − κ) | **removed entirely** — not a disabled flag, no code path |
| **B. Maintenance round** — model edits knowledge | how often the *structure* must change | one batched round, on demand |
| **C. Build / ingest** — pull GitHub, rebuild | — | zero model calls |

So the goal reduces to one sentence:

> **Make AI invocations proportional to structural change, not to data change
> and not to traffic.**

Removing A is what makes the cost bounded. Batching B is what makes it small.
And the trick that makes B rare is C2.

**Reconsider if:** the refusal rate stays high after a few rounds *and* the
unanswered questions are genuinely open-ended (not "we lack a row"). Then add a
fallback rung — appended to `ladder` in `bot.json` — and measure it, rather than
guessing.

---

## C2 — Prices change; knowledge should not

**The concern (the electronics example).** A shop changes prices. If the price
is written into FAQ text, every price change means editing N answers, keeping
them consistent, and possibly waking a model. Data changed, so money was spent.
That is the wrong coupling.

**The answer: separate the volatile value from the stable phrasing.**

| Approach | What one price change costs | AI invocations |
|---|---|---|
| Price written into each FAQ answer | edit N answers, keep them consistent | up to N, or one big rewrite |
| Price on the product entity; answer uses `{price}` | edit 1 row | **0** |

```yaml
# content/knowledge.yaml        (stable: the phrasing)
- slug: product.price
  patterns: ["how much is {product}", "what does {product} cost"]
  slots: { product: product }
  action:
    kind: answer
    template: "{product} is {price}."
```

```
entities  (volatile: the numbers)
  type=product  key=sku:rtx-4070  attrs_json={"price_cents":59900,"stock":12}
```

The general rule, and it is a **cost rule rather than a style rule**:

> Anything that changes faster than the wording around it must not live in the
> wording. Let the tables own the numbers; let the AI own the phrasing.

Which values are "volatile" is a judgement, but the test is simple: *if it has
ever changed while the sentence stayed the same, it is a value.* Prices, stock,
versions, opening hours, availability, star counts, dates.

**The same rule covers the portfolio case.** Repository star counts and
"last pushed" dates change constantly. If they were baked into answer text, the
knowledge base would need maintenance every time someone starred a repo. They
live in `entities.attrs_json` instead, so the answers stay correct without
anyone touching them.

**Reconsider if:** a value changes so rarely and is so entangled with the
wording that parameterising it makes the sentences worse. Then inline it — and
accept that changing it later is a knowledge edit.

---

## C3 — Do we need a separate `products` table?

**The concern.** For anything commerce-like, "products" feels like it deserves
its own table. But a table per domain is exactly what makes a system stop being
AI-maintainable: every new domain needs a migration and a deploy.

**The answer: a product is `entity_type='product'`.** No new table.

`entities` + `entity_links` + `attrs_json` already express: identity, price,
category, attributes, relations, availability. The knowledge rows never change
when the storage does — because they reference `{product}` and `{price}`, not
values or tables.

**When a real `products` table does earn its place** — any of these, measured
rather than imagined:

1. the data is **not entity-shaped** (order lines, inventory ledger, price
   history over time — these are not "things", they are events);
2. you need **DB-level constraints** a validator cannot express (multi-column
   uniqueness, FK to non-entity tables, CHECK across rows);
3. **volume or query shape** makes JSON access genuinely slow — e.g. faceted
   search across 50k SKUs with price-range filters. Measure first; SQLite's
   JSON1 is fast to a surprising size;
4. you have a **hard external schema** you do not control (an ERP export).

And when you do add it, the promotion is mechanical:

```
entity_type='product' rows  ->  products table
entities stays as the identity spine; products hangs off it
knowledge.yaml:  unchanged   (this is the payoff of C2)
```

The parameterisation is what makes the future schema change free.

**Reconsider if:** you catch yourself writing a second `entity_type` that needs
the *same* five typed columns. Two domains wanting identical typed columns is a
schema trying to be born.

---

## C4 — What goes in a JSON file, what in YAML, what in a table?

**The concern.** Everything could be a table. Most things should not be.
Tables bring validation, review, rollback, and migration — four costs. Pay them
only where the thing actually needs them.

**The four homes, by who changes it and what a change means:**

| Home | What lives there | Who changes it | A change means |
|---|---|---|---|
| `content/bot.json` | config: name, persona, ladder, sources | you | a deploy |
| `content/*.yaml` | knowledge, tests, curation | you **and the AI** | a PR, tests must pass |
| `data/bot.db` (built) | entities, links, documents, knowledge | `pc build` | nothing — it is a projection |
| `data/bot.db` (live) | `messages` | the runtime | nothing — append-only |

The line between the first two rows and the last two is the important one:

- **Configuration** is changed by a human, has no test, and needs no review —
  so it is a file, and a `settings` table would be pure ceremony. One bot, so no
  `bots` table either; when a second bot exists, `Scope` in code absorbs it.
- **Knowledge** is changed by an AI, absolutely needs review and tests, and
  benefits from history — so it is git-tracked files with a PR gate.
- **Facts** are produced by a machine, are large, and are queried — so they are
  built into SQLite.
- **State** is produced by visitors and only appended — so it is one table.

Preference order when you need to store something new:

```
config field  >  content file  >  existing table  >  new table
```

**Reconsider if:** the config file starts needing review, or knowledge starts
needing to change per request. Those are signals the boundary moved.

---

## C5 — What exactly is the maintenance AI?

**The concern.** The reference project outsourced maintenance to a mature
external agent (Hermes). What runs *here*, and what is its boundary?

**The answer: an ordinary coding agent** — openclaw, Hermes, DeepSeek Harness,
Claude Code, or a human with the same instructions.

This is a load-bearing simplification. A coding agent already has:

| It already has | So we do not build |
|---|---|
| a sandbox (the repo) | a tool-calling protocol |
| iteration (edit → run → read error → fix) | a retry loop |
| a diff view and history | `revisions` + a review console |
| branching | staging environments |
| a review gate (PR) + CI | `proposals` / `reviews` / `test_results` tables |
| `git revert` | a rollback command |

`proposals`, `proposal_items`, `reviews`, `revisions`, `tests`, `test_results`,
and `runs` — seven tables from the earlier draft — all existed to rebuild what
git already does. They are gone.

What replaces them, and where it lives:

| Rebuilt thing | Its replacement |
|---|---|
| proposal + items | a branch and a PR |
| review decisions | the PR review |
| revisions / rollback | git history, `git revert` |
| test suite in tables | `content/tests.yaml` |
| CI run log | GitHub Actions (or `pc test` locally) |
| run ledger / cost | the PR description + your own token dashboard |

The **IO contract** is similarly small: the agent reads `pc inbox` and the
content files, and writes content files. There is no JSON schema for model
output to validate, because the validator is `pc build` — malformed YAML fails
loudly at build time, in front of the agent, before anything reaches a PR.

**The cost of this choice:** you need a real reviewer for the PR (you), and the
agent needs shell access to the repo. For a personal project that is free. If a
non-git reviewer ever needs to approve changes, *that* is when a review console
and a `proposals` table earn their place.

---

## C6 — Rebuilding the database must not destroy work

**The concern.** `pc build` writes into the same database the runtime reads. If
someone hand-edited a row, or if a counter lived in a synced table, a build
could eat it.

**The answer — one invariant:** *synced tables are stateless; state lives only in
the live tables.*

- Anything authored by a human or the AI lives in `content/`, and is applied by
  `pc build` — **`knowledge` by upsert on `slug`**, entities and documents by
  rebuild from ingest + `curation.yaml`.
- **Nothing is deleted by a sync.** A knowledge slug removed from the YAML, or a
  repository that vanished upstream, becomes `status='archived'`. Old answers and
  citations stay valid.
- `knowledge.hits` / `misses` do not exist. Statistics are computed from
  `messages` on demand (`v_knowledge_coverage`). There is no counter to lose.
- `messages.matched_slug` stores the knowledge **slug**, not a row id, so a sync
  that renumbers ids does not orphan the history.
- Ingest is idempotent, so running it twice changes nothing.

**Reconsider if:** a statistic becomes too expensive to compute on the fly
(millions of messages). Then cache it — in a live table that `pc build` never
touches.

---

## C7 — Overfitting: rules too strict, too broad, or tuned to three questions

**The concern.** The AI sees three unanswered questions, writes three narrow
patterns, and now the bot answers exactly those three phrasings. The test suite
says everything is green because the tests were written from the same three
questions.

**The answer — four test kinds, three of which the AI does not get to choose:**

| Test kind | Guards against | Who writes it |
|---|---|---|
| positive (must answer) | **too strict** — a row that never fires | AI, from traffic |
| confusable negative (must refuse) | **too broad** — steals other questions | human, at seed time |
| refusal (must refuse) | fabrication | human, at seed time |
| ambiguity (must refuse, not guess) | picking a favourite between two close entities | human, at seed time |

The confusable/refusal/ambiguity cases are seeded **before** the AI starts
editing, so they are not shaped by the same evidence the AI is fitting to. That
is the only real defence available at this size.

Two more habits: assertions stay coarse (substring, never exact), and
`v_dead_knowledge` reports rows that never fire so a too-strict row is visible
rather than silently useless.

**Reconsider if:** regressions still slip through. The next step is running a
candidate row against the last N real questions before merging — shadow
evaluation — not a bigger test file.

---

## C8 — Fabrication

**The concern.** The AI invents a repository, a star count, or a URL, and the
answer looks confident.

**The answer.** The AI cannot add an entity at all: entities come from ingest or
`curation.yaml`, and `curation.yaml` changes are visible in the diff as
*additions of new keys*. A knowledge row that references an entity which does
not exist fails `pc test` immediately (every declared slot must resolve). Star
counts and URLs are resolved at answer time from `entities`, never typed into a
template — so a wrong number is an ingest bug, not a model invention.

Plus: READMEs are untrusted input. A repository description saying "ignore
previous instructions" is text, not an instruction, and the agent should treat
it that way. This is worth an explicit test once ingest is live.

---

## C9 — Bilingual answers

**The concern.** The audience is Chinese and English. Two full knowledge sets
would double the maintenance surface — the exact thing C1 is trying to avoid.

**The answer.** `knowledge.locale` with `''` meaning language-neutral. Patterns
match against normalised input, so one row can serve both languages when the
phrasing allows; a template diverges only where the wording genuinely differs.
Start with language-neutral rows plus a translated template where needed, and
only split into per-locale rows when a real sentence cannot be shared.

---

## C10 — Scope and provenance

**The concern.** A public example is only trustworthy if a reader can tell what
is in it and where it came from. Anything that cannot be traced is a liability:
for the reader, who cannot check it, and for the author, who has to maintain it.

**The answer — one rule, applied everywhere:** every row in this database is
either public GitHub metadata or hand-written judgement about public work.

- ingest reads four public GitHub accounts and nothing else;
- `curation.yaml` is editorial judgement about those public repositories, and it
  is reviewed like any other content;
- the test fixtures are verbatim snapshots of public READMEs
  (`tests/fixtures/README.md`);
- there is no private, client, or employer data anywhere in the repository, and
  no metric that cannot be recomputed from the files.

If an idea here generalises beyond this project, it is written as a technique
with a public example. That is the whole of the provenance story, and it is
checkable: `git log` and `pc entities` show where every row came from.

---

## C11 — Knowledge in a file, or in the database?

**The concern.** `content/knowledge.yaml` is the same idea as the reference
project's `faq` table, and the database has a `knowledge` table that the runtime
reads. So which one is the real one? Is the YAML a runtime input?

**The answer: no flip, no "source of truth" contest — they are one thing in two
forms, divided by offline and online.**

| | Offline | Online |
|---|---|---|
| Where | `content/*.yaml` | `data/bot.db` |
| Who touches it | a human, and the AI via PR | `pc build` writes; the runtime reads |
| Why that form | a human can read it, an AI can edit it, git can diff it | indexed, joinable, queryable in microseconds |
| When it matters | during a maintenance round | during a request |

**`content/` is not part of the runtime.** The runtime reads the database and
nothing else. The YAML is read exactly once per build, by `pc build`. It is not
a second runtime input and not a fallback path.

`pc build` syncs, idempotently:

- `knowledge` — **upsert by `slug`**. A slug removed from the YAML becomes
  `status='archived'`, never a `DELETE`, because `messages.matched_slug` history
  still points at it and a visitor's past answer must stay explicable.
- `entities` / `entity_links` / `documents` — rebuilt from ingest +
  `curation.yaml`; rows that vanish upstream are archived.

No counters live on synced rows (C6): a statistic stored in a synced table is a
statistic you lose on the next build.

Why authored knowledge takes file form rather than living only in the table:
the thing that must be reviewed and versioned is the **change**, and a YAML diff
is the only review UI that shows the exact sentence a visitor will read. It also
lets the maintainer be an ordinary coding agent (C5) instead of requiring a
bespoke admin API.

**Reconsider if:** any of these fires —

- the row count makes a diff unreviewable (roughly, several hundred rows in one
  file). Fix that first by splitting into `knowledge/*.yaml` per topic; only
  after that consider moving authorship into a table.
- a reviewer appears who does not use git. Then you need a console, and *that*
  is when `proposals` + `reviews` earn their place (C5).
- the bot must learn a row **without a merge** — answering a new question within
  a minute of hearing it. That is a real architectural change, not a storage
  change: it trades the review gate for latency.

## C12 — When does the bot need state?

**The concern.** A FAQ bot has no notion of "proceeding". The moment a
conversation must remember something — "what projects do you have" → "how much
to hire you" → leave a contact — you need flows, state machines, and somewhere
for the resulting task to land.

**The answer** is in **`docs/LEVELS.md`**: it is a difference of *kind*, not of
degree. Four concepts on two axes (cross-turn state, outside effect): **Fact**,
**Flow**, **Task**, **Composite**. Three levels: **L1 Facts**, **L2 Flow +
Task**, **L3 Composite**.

Three things worth stating here:

1. **The project is at L1, and L1 is a complete product.** It answers, cites,
   and refuses honestly.
2. **L2 is an append, not a rewrite** — two live tables, one ladder rung, three
   step primitives. The seams already exist (`messages.session_id`, the
   `ladder` list, `action.kind` as a code boundary).
3. **Composite (one sentence containing several questions) is not a level-2
   flow.** It is a different axis: a flow branches on history, a composite
   branches on sentence structure. It needs an algorithm and a rung — **no
   tables at all**. Conflating the two names causes real design mistakes.

**Reconsider if:** the inbox shows that visitors repeatedly get stuck in a
multi-turn exchange — that is the evidence that L2 is worth its live tables.
Wanting the demo to look impressive is a legitimate second reason, but it should
be an explicit one.
