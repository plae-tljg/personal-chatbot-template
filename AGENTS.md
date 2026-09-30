# AGENTS.md — guide for maintainers and their AI

Entry point for anyone (human or AI coding agent) working on
**personal-chatbots**. It states what the project is, the boundary you must not
cross, and the loop you are expected to run.

If you are adapting this template to a different owner, read `docs/TEMPLATE.md`
first — in particular the warning about `content/tests.yaml`, which asserts facts
about *this* owner and will fail once you change the sources.

## What this project is

A public chatbot that answers questions about one person's public work (43
repositories across four GitHub identities: `plae-tljg`, `LKM-Repo`,
`ellkaimu`, `plae-lkm`).

- The **runtime is deterministic**: it answers from SQLite rows built from
  `content/*.yaml` plus GitHub metadata. No model is called while serving, unless
  a human has enabled the opt-in `fallback` rung (rule 2).
- The **AI maintainer is you**, an external coding agent. You do not get a
  special API; you edit files, run commands, and commit.
- The **human publishes**. Your work reaches production by being merged.

## Architecture

```
OFFLINE -- you edit these, git versions them, nothing here runs
  content/bot.json        config: name, persona, level, ladder, sources
  content/knowledge.yaml  what the bot says   (patterns -> deterministic action)
  content/tests.yaml      what must stay true (frozen assertions)
  content/curation.yaml   judgement: hand-made entities, groupings, overrides
        |
        |  pc build     syncs offline -> online. idempotent. never touches live rows.
        v
ONLINE -- the runtime reads only this. you never edit it.
  data/bot.db
    entities / entity_links / documents(+FTS) / knowledge   <- synced from above
    messages                                                <- LIVE state
        |
        |  pc serve
        v
visitor                   answers + citations, zero tokens
```

The ladder is `knowledge -> entity -> search -> refuse`, with `fallback` available
but absent from `bot.json` by default.

**`content/` is not part of the runtime.** The runtime reads the database and
nothing else; `pc build` reads the YAML exactly once per build. If you edit the
database you will lose the change on the next build, and you will have broken
the only property that makes this project safe.

`pc build` is idempotent: `knowledge` is upserted by `slug`, and a slug you
remove becomes `archived` rather than deleted, because `messages.matched_slug`
history still points at it.

## How you are run

`.opencode/` wires this file to a real agent: `/maintain` runs one round with the
live inbox injected, `/review` looks without changing anything. The boundary in
`opencode.json` is the one this document describes, enforced by the tool rather
than by asking nicely:

```
edit:  "*": deny        <- the engine, the schema, the docs
       "content/*": allow
       "content/**": allow
```

So if a round needs a code change — a new `action.kind`, a new rung — you cannot
make it. Say so and stop; that is the boundary between "new knowledge is data"
and "new primitive is code". See `.opencode/README.md`.

## Commands

```bash
pc build                 # ingest + content/*.yaml -> data/bot.db (syncs; never touches live rows)
pc ask "what is dsh-review about?"   # one question, shows the rung + citation
pc inbox                 # unanswered questions, clustered by shape
pc test                  # run content/tests.yaml against the built db
pc stats                 # kappa, refusal rate, dead knowledge rows
pc sessions              # list chat sessions (one per visitor conversation)
pc sessions <id>         # replay one transcript
pc entities --summary    # what kinds of thing exist, and how many
pc entities repo kotlin  # search the vocabulary by name, alias or attribute
pc knowledge --grep repo # the rows the bot answers from, and their patterns
pc serve                 # FastAPI + the chatroom UI
```

`pc` is the console script from `pyproject.toml`. Without installing, the same
commands are `python -m personal_chatbots <command>`.

### Reading the data

**You may read the database as freely as you like.** `pc sql` runs any query:

```bash
pc sql                                   # list the tables and views, with row counts
pc sql "select key, name from entities where entity_type='repo' limit 10"
pc sql "select * from v_unresolved_inbox"
pc sql --json "select slug, hits from v_knowledge_coverage order by hits"
```

It opens the database with `mode=ro`, so **SQLite itself rejects a write** — not a
rule you are asked to respect, but a property of the connection. Read everything,
join anything, look at whatever helps you understand the data before proposing a
change to it.

`sqlite3` is denied only because *the CLI* can write, and a write to
`data/bot.db` is lost on the next build — a trap rather than a freedom. `pc sql`
is the same freedom without the trap.

Add `--offline --cache tests/fixtures` to `build` to work from the committed API
snapshot instead of the network. That is what CI does, and it is the fastest way
to iterate on content without touching GitHub:

```bash
python -m personal_chatbots build --offline --cache tests/fixtures
python -m unittest discover -s tests -t .      # the whole suite, no network
```

`tests/fixtures` carries a README for every repository, so an offline build has
the same 43 documents as a live one. CI and the published page therefore see the
data your change was tested against, not a thinner version of it.

Any read command also takes `--db`, which points at a database somewhere else:

```bash
python -m personal_chatbots release                       # dist/bot.db, no live state
python -m personal_chatbots --db dist/bot.db sql "select count(*) from documents"
```

Use it to inspect a released copy, or to answer a question from the fixture build
without disturbing `data/bot.db`.

## Levels — what kind of change is this?

Read **`docs/LEVELS.md`** before deciding how to implement anything. The short
version: five concepts on two axes, and picking the wrong one is how a small
system becomes unmaintainable.

| Concept | Cross-turn | Stored state | Mechanism |
|---|---|---|---|
| **Fact** — "the bot should know X" | no | no | `knowledge.yaml` / `curation.yaml` |
| **Reference** — "it", "the second one", "and the third?" | **yes** | **none — derived** | the frame, reading the transcript |
| **Flow** — "it should collect / walk me through this" | yes | **yes** | `flows.yaml` + `flow_states` (L2) |
| **Task** — "someone should be told" | no | **yes** | `tasks` (L2) |
| **Composite** — "one question with several parts" | no | no | a ladder rung, **no tables** (L3) |

Reference already exists and needs nothing from you: a referring expression is
rewritten into the entity it points at, from the previous answer's citations.
Before reaching for a Flow, check whether a frame lookup is enough — it usually
is, and it costs no live state. **A Flow is for a value that must survive a
turn ("how many?" → "3"), not for a topic that changes.**

The project ships at **L1 (Facts)**; `runtime.level` in `content/bot.json` says
where it is. Going up a level adds **live tables** — `flow_states`, `tasks` —
which are never rebuilt, unlike everything else.

If a request needs a level above the current one, **say so in the PR description
and implement nothing**, rather than half-building it. Raising the level is the
human's decision, because it adds a permanent maintenance surface.

Never fake a higher level with a lower mechanic. The four classic versions:

- stuffing conversation state into a `knowledge` template;
- building a Flow when a frame lookup would do (a subject that changes is a
  reference, not a step);
- inserting a `messages` row and calling it a task;
- splitting a compound question with a regex inside an answer template.

## The loop you run

Full procedure: **`skills/maintain-round.md`**. Summary:

1. `pc inbox` — read the questions the structure could not answer.
2. Read the relevant `content/*.yaml` and the built entities/documents.
3. Edit `content/knowledge.yaml` (or `curation.yaml`) to fix the gap.
4. **Add at least one case to `content/tests.yaml`** for every behaviour change.
5. `pc build && pc test` — iterate until green.
6. Commit on a branch and open a PR with a short rationale.

Step 4 is not optional. A change that is not pinned by a test will be undone by
a later change, and nobody will notice until a visitor does.

`.github/workflows/ci.yml` is the gate, and it is the *only* regression mechanism
this project has. It builds from `tests/fixtures` (no network), runs the seeded
suite, runs the unit tests, and asserts that a second build changes nothing.
There is no `test_results` table and no run ledger, because git provides the
baseline: green on `main`, red on your branch.

## Hard rules

1. **Never write to `data/bot.db` directly.** It is a build artifact.
2. **Do not put a model on the request path by accident.** The default ladder is
   `knowledge -> entity -> search -> refuse` (a reference rewrite runs before it),
   and `refuse` is a feature. A `fallback` rung exists and is opt-in: it is absent
   from `runtime.ladder` in `bot.json`, and enabling it is a human decision, not
   something a round does. See `docs/CONCERNS.md` C1.
3. **Never put a value in an answer template.** `{price}`, `{version}`,
   `{stars}`, `{hours}` must resolve from an entity. This is the cost rule
   (`docs/CONCERNS.md` C2), not style.
4. **Never copy a row to cover a variation.** Add a pattern to the existing row
   instead. A dozen rows cover 42 repositories; keep it that way.
5. **Never delete a test.** If its expectation is wrong, set
   `status: quarantined` and say why in `note`.
6. **Never invent a repository, metric, or URL.** Use only what ingest produced
   or what is already in `curation.yaml`.
7. **Do not add a table** without reading `docs/CONCERNS.md` C3 and C4 first.
   Preference order: config file > content file > existing table > new table.
8. **Never put live state into a built table.** Live tables are `messages` (and
   `flow_states`, `tasks` at L2). `pc build` rebuilds everything else, so state
   stored there is state destroyed.
9. **Stay at the current level unless the human raised it** (`docs/LEVELS.md` §8).
10. **Never cap the stored citations.** `messages.citations_json` is the record an
    answer was built from *and* the list a visitor can refer back to ("the second
    one"). Truncating it for display silently shortens what they can point at.

## Conventions

- Knowledge `slug` is a stable handle (`repo.about`). Revise by slug; never
  rename one that traffic has already matched.
- Assertions stay coarse. `answer_contains` is a substring list, never an exact
  string. A suite that fails on a rephrase is one people learn to ignore.
- Write rationales in the language of the question, one or two sentences.
- Silence is an acceptable outcome of a round. If the evidence does not support
  a claim, propose nothing for that question.
- When you cannot answer because you were not given a document, say so
  explicitly in the PR description. That sentence is how the human knows to
  widen ingest.

## When something looks wrong

- A knowledge row never fires → check its `patterns` and `match_json` before
  adding a new row.
- A row steals questions → add a confusable negative test, then tighten
  `match_json.exclude`.
- Two entities match one alias → **refuse**, do not pick a favourite. Ambiguity
  is recorded in `tests.yaml` (`ambiguity.*`).
