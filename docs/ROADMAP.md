# Roadmap

Current state: **v1 is built and running.**

```
python -m personal_chatbots build --offline --cache tests/fixtures
python -m personal_chatbots ask "what is dsh-review about?"
python -m personal_chatbots test          # 8/8 seeded cases
python -m unittest discover -s tests -t . # 82 tests
python -m personal_chatbots serve
python -m personal_chatbots sessions      # list or replay conversations
```

| Built | Where |
|---|---|
| 5-table schema with guard views | `db/schema.sql` |
| Ingest for four GitHub accounts, idempotent, cache-backed | `personal_chatbots/ingest.py` |
| Content loader + validator, fails loudly | `personal_chatbots/content.py` |
| Alias index and slot resolution, ambiguous -> no match | `personal_chatbots/resolve.py` |
| The 4-rung ladder and its actions | `personal_chatbots/engine.py` |
| Idempotent sync, archive-not-delete | `personal_chatbots/build.py` |
| Test runner over `content/tests.yaml` | `personal_chatbots/runner.py` |
| CLI: build / ask / inbox / test / stats / serve | `personal_chatbots/cli.py` |
| FastAPI + chatroom UI: session sidebar, resumable transcripts, rung and citation badges | `personal_chatbots/serve.py`, `personal_chatbots/static/index.html` |
| Session endpoints with a schema-version guard (stale db -> 503, not 500) | `personal_chatbots/serve.py`, `personal_chatbots/store.py` |
| CI: build offline from fixtures, run the suite, assert idempotency | `.github/workflows/ci.yml` |

Level 2 now has direct evidence behind it: replay any session whose turns moved
between topics (`pc sessions <id>`) and watch the follow-up land in the inbox.

### Fixed during the live-server pass

Two bugs that only appeared against a running server, both now covered by tests:

- **A stale database returned 500.** The DB predated the new `v_sessions` view, so
  every session endpoint died inside a query. `Store.require_schema()` now stamps
  and checks `PRAGMA user_version`, and the API answers **503 with the fix in the
  message** ("Run `pc build`") instead of a traceback.
- **The Store was thread-bound.** FastAPI runs sync endpoints in a worker pool, so
  the cached connection was used from whichever thread took the request, raising
  `SQLite objects created in a thread can only be used in that same thread`.
  Intermittent -- roughly one suite run in ten. Fixed with
  `check_same_thread=False`, WAL, and a write lock; `ConcurrencyTest` fails 8/8
  without the fix, so it is not a vacuous test.

What the numbers currently look like: 42 repositories across four accounts,
174 live entities, 10 knowledge rows, 244 links, 10 READMEs, 0.1-1 ms per answer,
0 tokens.

The guiding rule stays: **only build what is needed now.** If adding something
later is a feature, defer it. If adding it later is a rewrite, build it now.

---

## The static build

`web/` runs the same ladder in the browser, from a JSON snapshot. This is what
"mostly static webpage" turns out to cost: one export command and a file host.

The interesting part is not the port, it is that the port is *checked*.
`pc export` ships `content/tests.yaml` inside `data.json`, `web/parity.mjs` runs
those cases against the JS engine, and CI fails on any disagreement. Without
that, two implementations of one interpreter drift within a month — someone adds
an ordinal to `vocabulary.py`, and the browser quietly keeps the old behaviour
with nothing able to notice.

## What running the agent for real found

Six rounds were run through `opencode` with `opencode.json` enforcing the
boundary. The wiring works; the findings are about the gap between "the agent is
allowed to do this" and "the agent can":

| Finding | Fix |
|---|---|
| The agent tried to load `maintain-round` as an opencode **skill**; `skills/*.md` is not a skill, it is a file | frontmatter + `.opencode/skills/maintain-round/SKILL.md` symlink; opencode now discovers it |
| It reached for raw `sqlite3` to inspect the vocabulary | `pc entities` and `pc knowledge`; `sqlite3` explicitly denied |
| `pc entities topic` capped at 40 of 115 topics, so it could not see a whole type | default limit raised to 200; an empty result now explains itself |
| The skill said "your tools are the `pc` commands" — but `pc` only exists after `pip install -e .` | the skill uses `python -m personal_chatbots`, which needs no install |
| It tried `ls -R src`; there is no `src/` | the skill says so |

The general lesson is worth keeping: **a boundary is only usable if the allowed
surface is sufficient.** Every refusal sent the agent looking for a way around
rather than a way through, which is the opposite of what a permission rule is
for. Each gap above was found by watching the agent get stuck, not by reasoning
about the config.

Two rounds with a small free model did reconnaissance correctly and then never
committed to an edit. That is a model-capability finding, not a design one —
which is itself the argument for keeping the model choice swappable and the
loop's output reviewable.

## Next: the first real maintenance round

The engine is done; the loop has not yet been run for real. That is the next
step, and it is the one that proves the claim:

1. Ask the bot a dozen questions a visitor would actually ask.
2. `pc inbox` — read what it could not answer.
3. Edit `content/`, add cases to `content/tests.yaml`, `pc build && pc test`.
4. Open a PR. Merge. Measure kappa before and after.

Useful sanity checks while doing it:

- `pc stats` reports kappa, the resolution mix, and rows that never matched.
- Five rows currently never match (`contact`, `meta.provenance`, `owner.intro`,
  `repo.list`, `repo.ranked`) because nothing has asked them yet. That is what
  an accurate dead-row report looks like before there is traffic, and it is why
  the report is a prompt to ask better questions rather than to delete rows.

### Known limitations to fix in v1.5

- **Only ten READMEs are fetched** (`build.readme_limit`), because the
  unauthenticated API allows 60 requests/hour. The search rung therefore covers
  ten repositories, not 42. Setting `GITHUB_TOKEN` raises the limit to 5000/hour;
  the cap should then follow the token's presence rather than a constant.
- **Single- and two-character names get no alias** (`C`, `C++`). Matching is
  token-based, so the three-character floor is more conservative than it needs to
  be. Until it is revisited, such names need a curation alias.
- **No `pc propose`** — nothing writes proposals, because proposals are PRs.

---

## v1.5 — when a trigger fires, not before

Each item has an explicit trigger in `docs/CONCERNS.md`. Do not do them early.

| Item | Trigger |
|---|---|
| `curation.yaml` alias/grouping expansion | inbox shows near-misses from naming |
| `entity_metrics` table | a query sorting on `json_extract` actually hurts |
| `products` table | data stops being entity-shaped, or needs DB constraints (C3) |
| A model fallback rung | refusal rate stays high *and* the gaps are open-ended (C1) |
| Review console + `proposals` table | a reviewer who does not use git (C5) |
| `vocab_terms` table | the AI needs to invent new entity types |
| Bilingual knowledge rows | a real sentence cannot be shared across locales (C9) |
| Cached stats tables | `messages` gets too big to aggregate on the fly (C6) |

---

## v2 — the parts worth writing about

- **Shadow evaluation.** Run a candidate knowledge row against the last N real
  questions before merging: would it have fired, and would it have been right?
  The natural next step after tests-as-files (C7).
- **Measured Δκ per round.** Refusal rate before and after each merged PR,
  which turns the cost model into an observed number.
- **Prompt-injection tests in ingest.** READMEs are untrusted input; a repo
  description saying "ignore previous instructions" must not reach the agent as
  an instruction (C8).
- **Lead capture.** "Interested in working together" becomes a flow that queues
  a notification behind human approval — the part that serves the business goal.
- **Static-DB deployment.** Ingest and review locally, publish a read-only
  SQLite artifact, serve it anywhere.

---

## Deliberately not planned

- **A model on the request path** (C1).
- **Multi-bot / multi-tenant.** `Scope` is the seam; a `bots` table appears when
  a second bot exists (C4).
- **A review console.** Git is the review console (C5).
- **Vector search.** FTS5 plus entity links covers 42 repos.
- **Reserved empty tables.** The whole point of the deferred list.

---

## Relationship to the other projects

| project | role |
|---|---|
| `~/Music/OpenGallery` | synthetic demo; same pattern, bespoke proposals table |
| `~/Music/personal-chatbots` | same pattern on real data, with git as the review gate |
| `~/Music/MaaFWPhoneAI` | same pattern outside text, on GUI pipelines |
| `~/Music/blogs` season 2 | *Wake the AI Less* — the article this feeds |

Article hooks, cheapest-first:

1. **"The AI proposes, the tests decide."** Tests as files, and why a
   self-maintaining system needs a merge gate more than a better prompt.
2. **"Keep the values out of the sentences."** The price example (C2) as a cost
   argument rather than a style rule — the most reusable idea here.
3. **"You don't need a proposal table, you need a PR."** What changed when the
   maintainer turned out to be an ordinary coding agent (C5).
