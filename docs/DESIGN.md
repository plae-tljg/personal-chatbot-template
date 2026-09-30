# Design

Status: v1 runs. The engine, CLI, HTTP widget, fixture-backed CI and 63
tests are in place; the maintenance loop itself is next (`docs/ROADMAP.md`).

Companion docs: `docs/CONCERNS.md` (the doubts and the answers),
`docs/LEVELS.md` (the four concepts and the three levels -- read it before
adding behaviour), `AGENTS.md` and `skills/maintain-round.md` (what the
maintenance agent does), `docs/INGEST_GITHUB.md` (how entities get built).

---

## 1. What this is

A public chatbot answering questions about one person's public work — 43
repositories across four GitHub identities. The runtime answers from SQLite rows
built out of `content/*.yaml` plus GitHub metadata. No model runs while serving.

```text
Structure + AI = Product

the engine executes rows          deterministic, free, reproducible
the AI maintains the files        in a git round, offline, batched
the tests protect past gains      a regression fails CI
the human merges                  the PR is the publish gate
```

The same pattern as OpenGallery, on real public data instead of a synthetic museum.

---

## 2. Four things, four homes

Not "which one is the source of truth" — they are different things, and each has
exactly one home.

| Thing | Lives in | Form | Written by | `pc build` does |
|---|---|---|---|---|
| **Config** | `content/bot.json` | offline | human | re-reads it |
| **Content** | `content/*.yaml` | **offline** | human **and AI** (via PR) | **syncs into the db** |
| **Data** | `data/bot.db` | **online** | `pc build` | rebuilds it |
| **State** | `data/bot.db` | **online** | the runtime | **never touches it** |

The load-bearing line is between *offline* and *online*:

> **`content/` is not part of the runtime.** The runtime reads the database and
> nothing else. The YAML is read exactly once per build, by `pc build`.

The YAML is not a seed and not a second truth: it is **the whole knowledge
base**, in its **editable form** — the form a human can read, an AI can edit, and git can diff. The
database is the same knowledge in its **runtime form** — indexed, joinable,
queryable in microseconds.

Nothing is learned at runtime. The database does not accumulate knowledge as
visitors ask questions — it is a projection of `content/`, rebuilt on every
build. If it is not in the YAML, the bot does not know it, and no amount of
traffic will change that. (What traffic produces is the *inbox*: evidence of what
to add.)

`pc build` syncs them, idempotently:

- **`knowledge`** — upsert by `slug`. A slug that disappears from the YAML
  becomes `status='archived'`, never a `DELETE`: `messages.matched_slug` history
  still points at it, and a visitor's past answer must stay explicable.
- **`entities` / `entity_links` / `documents`** — rebuilt from ingest +
  `curation.yaml`; rows that vanish upstream are archived, not deleted.

No counters are stored on synced rows (C6): a statistic that lives in a rebuilt
table is a statistic you lose.

Two consequences worth stating:

- **A rebuild is always safe.** `pc build` can run at any time, including during
  a production day, because it syncs authored rows and never touches live ones.
- **The AI has no ambient authority.** It edits offline files in a branch. It
  cannot write the database, and it cannot merge.

This is the practical meaning of "the AI lives in the system": its memory is the
repo, its inbox is the `messages` table, its work is a diff, and a human holds
the merge button. The review gate is git, not something we built.

---

## 3. The runtime ladder

Before the ladder runs, referring expressions are rewritten into the entities
they point at, using the previous answer's citations (`frame.py`,
`docs/LEVELS.md` §1). It is a rewrite rather than a rung, so everything below is
unchanged by it.

```
0. reference   "the second one" -> that entity, from the last answer's citations
1. knowledge   pattern match, slots resolve to live entities   -> answer + citations
2. entity      exact name / alias hit                          -> card + citations
3. search      FTS5 over documents                             -> passage + citation
4. fallback    optional: a model answers the visitor, offline     -> model call
5. refuse      honest "I don't know", recorded as unresolved   -> the learning signal
```

Rung 4 is **not in the default ladder**. Adding `"fallback"` to
`runtime.ladder` in `bot.json` is what turns it on (C1). It answers the visitor
and still records the turn as unresolved, because those are two different jobs.

Rules:

- **A rung never guesses.** If a slot matches two entities, the rung does not
  match. Ambiguity falls to refusal rather than picking a favourite.
- **The ladder is config**, in `bot.json` (`runtime.ladder`). Appending a rung
  is an append to that list — that is the seam.
- **v1 has no model rung.** Not a flag that is off: no code path. One fewer
  rung, one fewer config, one fewer failure mode, and the refusal rate stays an
  honest number instead of a hidden one (C1).
- **Refusal is a feature.** It is what makes the other answers credible, and it
  is what makes the inbox truthful.

---

## 4. Five tables

Four are build artifacts; one is live state.

| Table | Kind | Why it exists |
|---|---|---|
| `entities` | built | the nouns: `repo`, `account`, `person`, `language`, `topic`, `project`, `product`. Volatile values (`stars`, `price_cents`) live in `attrs_json` |
| `entity_links` | built | the graph: `owned_by`, `uses_language`, `tagged`, `includes`, `forked_from`. One table instead of `repo_topics` + `repo_languages` + `account_repos` |
| `documents` (+FTS) | built | READMEs and notes: the cheapest answer rung, and the only thing the AI may read |
| `knowledge` | built | loaded from `content/knowledge.yaml`: patterns, slots, action, citations |
| `messages` | **live** | every turn, with `unresolved = 1` on the questions the structure could not answer |

The live/built split is the load-bearing invariant, and every level in
`docs/LEVELS.md` extends it rather than breaking it: L2 adds `flow_states` and
`tasks` as live tables, and `pc build` never touches them. Live state in a built
table is state destroyed.

Kept deliberately absent, with reasons in `docs/CONCERNS.md`:

- **`products`** — a product is `entity_type='product'` (C3)
- **`settings`** — a JSON file; one bot (C4)
- **`proposals` / `reviews` / `revisions`** — that is a PR (C5)
- **`tests` / `test_results`** — that is `content/tests.yaml` + git (C5)
- **`runs`** — that is the PR description and CI (C1, C5)
- **`hits` / `misses` columns** — computed from `messages`; a rebuild must not
  lose them (C6)
- **`flows` / `flow_states` / `tasks`** — L2 appends, not v1 (C12, `docs/LEVELS.md`)

---

## 5. entities vs products: the promotion ladder

A shop's `products` table, a museum's `ticket_types`, this project's `repo` — all
the same shape, and none of them needs its own table on day one.

```
attrs_json        ->   entity_links / typed columns   ->   typed table
(carried values)       (things you query or traverse)      (not entity-shaped,
   AI may edit              AI may edit                      DB constraints,
                                                             or genuinely huge)
                                                                 code change
```

- **Flexible by default.** A new kind of thing is a row plus a word in the
  vocabulary. No migration.
- **Promote on evidence, not taste.** Promote when a query actually hurts, or
  when the data stops being entity-shaped (order lines, ledgers, price history).
- **Promoting is cheap if you parameterised.** Because `knowledge.yaml` says
  `{price}` and never a number, moving price from `attrs_json` to a `products`
  table changes zero knowledge rows (C2, C3).

Where the generic design is honestly worse: no `NOT NULL` on domain fields
(validation is `pc build`), slower ad-hoc SQL for analytics, and `attrs_json`
keys rot if nobody curates them. `curation.yaml` is where that curation lives,
and it is reviewed like everything else.

---

## 6. One round

Run by the maintenance agent (`skills/maintain-round.md`), reviewed by a human:

```
pc inbox                       read the questions the structure could not answer
read content/*.yaml            what the bot says today
edit knowledge.yaml            add a pattern, revise a template, or add a row
edit curation.yaml             fix an alias, grouping, or summary
add to tests.yaml              pin the fix with the visitor's exact wording
pc build && pc test            iterate until green
open a PR                      rationale + what could not be done, and why
       |
       v
human review  ->  merge  ->  deploy rebuilds the db
```

The baseline for "regression" is git: a test that passes on `main` and fails on
the branch. That is the entire regression-protection mechanism, and it needs no
tables.

Why each behaviour change must carry a test: an unpinned change is undone by the
next change, and nobody notices until a visitor does. Tests are how improvements
**accumulate** instead of merely happening.

---

## 7. Code seams

Extensibility is a property of code, not of the table list. These are the places
designed to absorb change:

| Seam | v1 | Absorbs |
|---|---|---|
| `Store` | all SQL in one module | adding a table or column |
| `Scope` | one default scope threaded everywhere | multi-bot |
| `Vocabulary` | a dict of entity types / link types / action kinds | new domains; later, a `vocab_terms` table |
| `LADDER` | the list from `bot.json` | a rule rung, a flow rung, a model fallback rung |
| `build()` | ingest + content → db, idempotent | new sources, new content files |
| `Runner` | run `tests.yaml` against a built db | hosted CI, shadow evaluation |

That is the whole answer to "can it be extended later": leave seams in code, not
empty tables in the database.

---

## 8. Boundaries

- **Not RAG.** FTS is rung 3 of a ladder with a deterministic floor. Most
  questions never reach it.
- **Not text-to-SQL.** The AI writes rows, and only offline.
- **Not autonomous.** The PR gate is the design, not scaffolding.
- **Not no-code.** New `action_kind`s are new Python. The claim is narrower:
  *new knowledge is data; new primitives are code, and they should be rare.*
- **Public data only.** Ingest reads public GitHub metadata; everything else is
  hand-written judgement about public work (C10).

---

## 9. Relation to the other instances

| | OpenGallery | personal-chatbots | MaaFwPhoneAI |
|---|---|---|---|
| Domain | fictional museum | LKM's public work | Android GUI automation |
| Data | synthetic | real, 4 accounts | pipeline graphs |
| Knowledge unit | `faq` + entity tables | `content/knowledge.yaml` → `knowledge` | `pipelines` + `elements` |
| Runtime | stdlib CLI | FastAPI + SQLite | MaaFramework runner |
| Review gate | proposals table | **a PR** | proposals table |
| Extra mechanism | — | tests gate the merge | postconditions + 0-token replay |

The interesting progression for the article: OpenGallery proved the pattern on
neutral data with a bespoke review table. This project makes the same claim with
**git as the review system** — the more honest engineering answer, and the
stronger one: an AI-maintained system does not need new infrastructure, it needs
the existing infrastructure applied to data instead of code.
