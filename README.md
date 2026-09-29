# personal-chatbots

A public "chat with my works" bot, and **a template for AI-maintained projects**.
The runtime answers from SQLite rows synced out of `content/*.yaml` plus GitHub
metadata. A coding agent maintains the content. Git is the review gate. **No model
runs while serving** unless you add one.

> **Reading this to borrow the idea?** `docs/IDEAS.md` is the index — 32 ideas,
> one line each, and where each is argued. `docs/TEMPLATE.md` says which five
> files hold everything specific to this instance, and which one will bite you
> first. It is a reference to clone and read, not a GitHub template.

**Status: v1 runs.** 42 repositories across four GitHub identities, 174 entities,
11 knowledge rows, 16 seeded cases, 103 unit + invariant tests, and a browser
engine that CI checks agrees with the Python one. Answers land in
0.1–1 ms at zero tokens.

## Quickstart

```bash
git clone <this repo> personal-chatbots && cd personal-chatbots

python3 -m venv .venv && source .venv/bin/activate
pip install -U pip setuptools        # see the note below if you skip this
pip install -e ".[test]"

# 1. build the database. Needs no network: it uses the committed API snapshot.
python -m personal_chatbots build --offline --cache tests/fixtures

# 2. ask it something
python -m personal_chatbots ask "what is dsh-review about?"
python -m personal_chatbots ask "what is the Finance Management project?"

# 3. ask something it does not know — the refusal is the point
python -m personal_chatbots ask "how much does LKM charge?"
python -m personal_chatbots inbox          # ...and there it is

# 4. run the checks
python -m personal_chatbots test           # the 8 frozen cases
python -m unittest discover -s tests -t .  # 102 unit + invariant tests

# 5. open the chatroom
python -m personal_chatbots serve          # http://127.0.0.1:8080
```

With `pip install -e ".[test]"`, every command above is also available as plain `pc`:

```bash
pc build --offline --cache tests/fixtures
pc ask "which projects use Kotlin?"
pc serve
```

**If `pip install -e ".[test]"` fails** with *"its build backend is missing the
'build_editable' hook"*, your setuptools is older than 64 — Debian and Ubuntu
ship 59. Either `pip install -U setuptools`, or skip the install entirely:

```bash
pip install pyyaml                   # the only hard dependency for the loop
python -m personal_chatbots build --offline --cache tests/fixtures
```

`python -m personal_chatbots` needs no install step, which is why every command
in `AGENTS.md` and the skills uses that form rather than `pc`.

To pull fresh data from GitHub instead of the snapshot, drop `--offline
--cache tests/fixtures`. That needs no token for four accounts, and
`GITHUB_TOKEN` raises the rate limit when you want more READMEs
(`build.readme_limit`, currently 10).

### Why this exists

`examples/hardcoded_bot.py` is the thing this project is a reaction to: a small
shop bot with prices and FAQs inside one Python file, `if phrase in question`
matching, and an agent fallback. It is the *reasonable* version of that design,
not a strawman — and it still cannot be changed safely:

```bash
python examples/hardcoded_bot.py --demo   # a price change contradicting itself
python examples/compare.py                # the five properties, measured
```

The claim is not that a bot without RAG is bad. Both bots keep the model out of
the fast path, which is the right call in both designs. The difference is that
one of them can be changed by an agent and **checked**, and the other can only
be changed by an agent and **hoped for**.

---

## What it does

```
$ pc ask "what is the Finance Management project?"
• Finance-Management-App — App to do finance management, completely offline
• Finance-Management-Web — offline finance management web, simply sql.js
• Finance_Lux_Web —

  [knowledge:project.about]  0.21 ms  · 0 tokens
  cites: plae-tljg/Finance-Management-App, ellkaimu/Finance-Management-Web, plae-lkm/Finance_Lux_Web

$ pc ask "how much does LKM charge?"
I don't have that in my tables. I've written the question down.

  [refuse:-]  0.08 ms  · 0 tokens
```

The refusal is the feature. It is what makes the other answers credible, and it
is the only learning signal the system has — `pc inbox` reads it.

## The chatroom

`pc serve` opens a two-pane chat client: a sidebar of conversations, and the
transcript of the selected one. Sessions are resumable and the URL carries the
id (`#/s/<id>`), so a conversation can be refreshed, bookmarked, or reopened
later. Each answer shows which rung produced it and where the facts came from.

Sessions are **a view over `messages`, not a table** — title, turn count and
timestamps are all derivable, and a second copy of that truth would be one more
thing to keep in sync. A `sessions` table earns its place the day per-session
metadata (locale, referrer, a user-chosen title) is needed.

| Endpoint | Purpose |
|---|---|
| `POST /api/ask` | answer one question; returns `session_id` and `turn` |
| `GET /api/sessions` | sidebar data: title, turns, unresolved count, last activity |
| `GET /api/sessions/{id}` | the full transcript, citations included |
| `DELETE /api/sessions/{id}` | remove one visitor's transcript |
| `GET /api/stats` | kappa, entities, knowledge rows, ladder |

Two honest notes about what "multi-round" means at level 1:

1. **Turns are stored and replayed faithfully.** Reopening a session shows
   exactly what was said, with the same citations and the same rung.
2. **References carry, values do not.** A referring expression resolves against
   earlier turns — after a list, "tell me about the second one", "and the
   third?", and "it" all work. But a question needing a value collected across
   turns ("how many?" → "3") still refuses, because that is a Flow and it needs
   live state. `tests/test_sessions.py` pins the boundary deliberately, and the
   refusal lands in `pc inbox` — the evidence that level 2 (`docs/LEVELS.md`) is
   worth its tables.

Transcripts are the visitor's own data, so `DELETE` really deletes. The
"archive, never delete" rule protects *knowledge*, where a past answer must stay
explicable.

## It also runs as a static page

The ladder is a small interpreter over rows, and the rows are data — so the rows
ship as JSON and the whole bot runs client-side.

```bash
python -m personal_chatbots export
python -m http.server -d web 8080
```

No server process, no database, no keys, nothing on the request path that can
fail or be billed. Open `web/index.html` and press **run the frozen cases here**:
the same eight assertions the Python runtime runs pass in your browser.

Two implementations of one interpreter would normally drift. What stops it is
that `pc export` ships `content/tests.yaml` inside `data.json`, so the browser
engine can run the same cases, and CI checks it:

```bash
python -m personal_chatbots export && node web/parity.mjs
# 8/8 cases agree with the Python runtime
```

`web/README.md` has the honest list of what differs (sessions live in
`localStorage`, search has no FTS index, `data.json` is a build artifact).
`docs/STATIC_SITE.md` is the integration guide for putting it on an existing
static site — one command copies the compiled artifacts, with a `verify` for CI.

## Four things, four homes

| Thing | Lives in | Form | Written by | `pc build` does |
|---|---|---|---|---|
| Config | `content/bot.json` | offline | you | re-reads it |
| Content | `content/*.yaml` | **offline** | you **and the AI** (via PR) | **syncs into the db** |
| Data | `data/bot.db` | **online** | `pc build` | rebuilds it |
| State | `data/bot.db` — `messages` | **online** | the runtime | never touches it |

**`content/` is not part of the runtime.** The runtime reads the database and
nothing else; the YAML is read once per build. It is not a seed that the system
grows past either — **it is the entire knowledge base**, in the form a human can
read, an AI can edit, and git can diff. Nothing is learned at runtime.

Syncing is idempotent: `knowledge` is upserted by `slug` (a removed slug becomes
`archived`, never deleted, because past answers still reference it), and
entities/documents are rebuilt from ingest + `curation.yaml`.

## The runtime ladder (level 1)

```
reference -> knowledge  ->  entity  ->  search  ->  refuse
```

`reference` is a **rewrite, not a rung**: before matching, "the second one" or
"it" becomes the entity it points at, read from the previous answer's citations.
So the four rungs below are untouched by it, and a follow-up works with no state
machine and no new table.

- **knowledge** — pattern match; slots resolve to live entities; zero tokens.
- **entity** — a bare name, answered with its card.
- **search** — FTS5 (trigram), *scoped to the one entity the question names*.
  An unscoped full-text guess answers questions it has no business answering.
- **refuse** — honest "I don't know", recorded as `messages.unresolved = 1`.

A rung never guesses: if a slot matches two entities, the rung does not match.

### Adding a model, if you want one

There is no model rung by default. Not a flag that is off — no rung in the
ladder. Turning one on is two lines in `content/bot.json`:

```jsonc
"ladder": ["knowledge", "entity", "search", "fallback", "refuse"],
"fallback": { "enabled": true, "endpoint": "…", "model": "…", "api_key_env": "…" }
```

Any OpenAI-compatible endpoint works — OpenCode Zen's free pool, a local Ollama
at `http://localhost:11434/v1/chat/completions`, or any provider.

Measured: **~7 s and a model call, against ~0.2 ms for the rungs above it.** That
gap is the entire reason the default is off.

Two properties make it safe to enable:

- **The turn is still recorded as unresolved.** A model answering is a real
  answer to the visitor and a non-answer from the structure; `Answer.unresolved`
  is deliberately not `Answer.refused`. Clearing the flag would answer the
  visitor and blind the maintenance loop at the same time.
- **It fails into a refusal.** Timeout, bad key, 500 — the ladder falls through
  to `refuse`. The bot never gets worse because the model is down.

The static site cannot have one: a browser-side call would expose the API key.
The fallback is a server-side rung (`pc serve`), which is the honest split —
`docs/STATIC_SITE.md`.

`docs/LEVELS.md` defines L2 (Flow + Task: the bot remembers a conversation and
hands a lead to a human) and L3 (Composite: one sentence containing several
questions, which needs an algorithm rather than tables).

## Five tables

`entities` · `entity_links` · `documents`(+FTS) · `knowledge` — synced from
offline content. `messages` — the only table the runtime writes.

No `products` table (a product is `entity_type='product'`). No `settings` table
(a JSON file). No `proposals` / `reviews` / `revisions` / `tests` / `runs` tables
— that is a pull request. Reasons for each in `docs/CONCERNS.md`.

## The loop

```
pc inbox                 what visitors asked that the tables could not answer
edit content/*.yaml      fix the gap, add a pattern, curate an entity
add to tests.yaml        pin it with the visitor's exact wording
pc build && pc test      iterate until green
open a PR                human reviews, merges, deploys
```

Run by a coding agent (opencode / Hermes / DSH / Claude Code), described in
`AGENTS.md` and `skills/maintain-round.md`. It needs no special API — it edits
files and commits. CI is the gate: green on `main`, red on the branch is a
regression.

`opencode` is wired up and ready, free models included:

```bash
opencode models | grep -i free   # the free pool rotates; check, do not assume
opencode
> /review                        # look at what is failing, change nothing
> /maintain                      # run one round
```

`.opencode/README.md` explains the model choice (read the privacy column, not the
price column — a round reads real visitor questions) and the permission boundary
that stops the agent editing anything outside `content/`.

## The cost rule

> Anything that changes faster than the wording around it must not live in the
> wording.

`{price}`, `{stars}`, `{version}` resolve from entities, never typed into a
template. A price change is then 1 row and **0 AI invocations** instead of N
answer rewrites (`docs/CONCERNS.md` C2).

## Layout

```
examples/hardcoded_bot.py  the foil: the same job done the unreviewable way
examples/compare.py        the five properties that decide maintainability
AGENTS.md                  entry point for the maintenance agent
skills/maintain-round.md   the full procedure for one round
db/schema.sql              5 tables + 4 views
content/                   bot.json · knowledge.yaml · tests.yaml · curation.yaml
personal_chatbots/         the engine (config, store, resolve, engine, build, serve)
tests/                     unit + invariant tests, and the API fixtures
docs/IDEAS.md              index of every idea, one line each, and where it lives
docs/TEMPLATE.md           what to change to point this at your own data
docs/CONCERNS.md           the doubts and their answers -- start here
docs/LEVELS.md             Facts / Flow / Task / Composite, and levels L1-L3
docs/DESIGN.md             why: the four homes, the ladder, the seams
docs/STATIC_SITE.md        putting the bot on an existing static site (Astro etc.)
docs/INGEST_GITHUB.md      how entities get built from 4 GitHub identities
docs/ROADMAP.md            what is built, what waits, and the trigger for each
```

## Boundaries

Not RAG, not text-to-SQL, not autonomous, not no-code. See `docs/DESIGN.md` §8.

## License

MIT — see `LICENSE`. Everything the bot knows is public GitHub metadata plus
hand-written judgement about public work. There is no private or client data in
this repository (`docs/CONCERNS.md` C10).

## Related

- [OpenGallery](https://github.com/plae-tljg/OpenGallery) — the synthetic demo of
  the same pattern, with a bespoke review table instead of git
- [MaaFwPhoneAI](https://github.com/plae-tljg/MaaFwPhoneAI) — the same pattern
  outside text, on Android GUI pipelines
