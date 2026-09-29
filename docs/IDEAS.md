# Idea index

Every idea this project argues, where it is argued, and the one sentence it comes
down to. Written because the discussions that produced this repository ranged
across a dozen topics, and a reader — or an agent — should be able to find the
argument for any of them without reading everything.

Each row is a candidate section for an article. The "claim" column is the
sentence the section has to earn.

---

## 1. The core

| # | Idea | Where | Claim |
|---|---|---|---|
| 1 | **Structure executes, AI maintains, human publishes** | `DESIGN.md` §1 | The model is not on the request path; it is in the maintenance loop. |
| 2 | **Content is not a seed** | `DESIGN.md` §2 | The database never accumulates knowledge. What is not in `content/` is not known, and traffic does not change that. Traffic produces a to-do list. |
| 3 | **Four homes, by who changes them** | `DESIGN.md` §2 | Config is a file, content is a PR, data is a projection, state is append-only. Each has exactly one home and no overlap. |
| 4 | **The vocabulary is the seam** | `CONCERNS.md` C4, `DESIGN.md` §7 | Adding a word is a row; adding an interpreter primitive is code. That boundary is what keeps "AI-maintainable" honest. |

## 2. How much structure

| # | Idea | Where | Claim |
|---|---|---|---|
| 5 | **Declared vs reconstructed structure** | `CONCERNS.md` C13 | You cannot minimise structure, only choose where it lives. Minimise the reconstructed kind — it is re-paid on every read, by everyone. |
| 6 | **The briefing test** | `CONCERNS.md` C13 | Every sentence an agent needs before acting correctly is structure that was not declared. |
| 7 | **The promotion ladder** | `DESIGN.md` §5 | Carry in JSON → promote to a sorted row → promote to a typed table, on evidence, never on taste. |
| 8 | **Three questions before declaring a table** | `CONCERNS.md` C13 | Known on day one? Three queries must agree? Machine-checkable invariants? All three yes, declare it. |
| 9 | **Why a product table is not less flexible** | `CONCERNS.md` C3 | A shared field vocabulary is the thing being bought. Five `json_extract` calls drift to three key names and the bot quotes two prices. |

## 3. Conversation

| # | Idea | Where | Claim |
|---|---|---|---|
| 10 | **Reference is cross-turn without being stateful** | `LEVELS.md` §1 | "The second one" needs the previous answer, not a state machine. The frame is a projection of the transcript, and it cost no tables. |
| 11 | **The yielding flow** | `LEVELS.md` §3 | A global state machine confines the visitor. A flow that yields the turn whenever the input is not an answer to its question does not, and pause/resume comes free. |
| 12 | **Flow is for a value, not a topic** | `LEVELS.md` §3, `AGENTS.md` | A subject that changes is a reference. A value that must survive a turn ("how many?" → "3") is a flow. Confusing them is how a bot gets stuck. |
| 13 | **Composite needs an algorithm, not tables** | `LEVELS.md` §5 | One sentence containing several questions is a resolution strategy. Reaching for storage here is the most common over-build. |
| 14 | **Five concepts, two axes** | `LEVELS.md` §1 | Fact, Reference, Flow, Task, Composite — sorted by cross-turn need and stored state. |

## 4. Trust and process

| # | Idea | Where | Claim |
|---|---|---|---|
| 15 | **Proposals are pull requests** | `CONCERNS.md` C5 | When the maintainer is an ordinary coding agent, `proposals`/`reviews`/`revisions` tables are seven tables rebuilding what git already does. |
| 16 | **Tests are files, and git is the baseline** | `AGENTS.md`, `ROADMAP.md` | Green on `main`, red on the branch is the whole regression mechanism. No `test_results` table. |
| 17 | **The inbox is the product** | `DEV_LOOP` → `skills/maintain-round.md` | `unresolved = 1` on a partial index is worth more than any amount of prompt engineering. |
| 18 | **Answering is not knowing** | `CONCERNS.md` C1 | A fallback answer is a real answer to the visitor and a non-answer from the structure. Clearing `unresolved` would answer the visitor and blind the loop. |
| 19 | **A refusal with no way forward reads as broken** | `engine.py` `suggestions()` | Follow-ups are derived from the knowledge rows, and a suggestion that would itself refuse is not offered. |
| 20 | **The validator is the boundary** | `CONCERNS.md` C2, `content.py` | A price in a sentence is a value that will change, and a stale one reads exactly like a correct one. Refused at build time. |

## 5. Cost

| # | Idea | Where | Claim |
|---|---|---|---|
| 21 | **Wake count, not token price** | `CONCERNS.md` C1 | Invocations should scale with *structural* change — not with traffic, not with data change. |
| 22 | **Keep values out of sentences** | `CONCERNS.md` C2 | A price change is one row and zero model calls, instead of N answer rewrites. |
| 23 | **Honest economics at personal scale** | `DESIGN.md` §8 | At 50 questions a day the loop is justified by correctness and demonstration, not by token savings. Saying so is stronger than a fake ROI. |
| 24 | **~7 s versus ~0.2 ms** | `README.md`, `CONCERNS.md` C1 | The measured gap between a model rung and the rungs above it. That is the whole reason the default is off. |

## 6. Working with an agent

| # | Idea | Where | Claim |
|---|---|---|---|
| 25 | **The boundary is the filesystem** | `.opencode/README.md` | `edit: "*": deny, "content/**": allow` — enforced by the tool, not by asking nicely in a prompt. Verified: `db/schema.sql` refused, `content/bot.json` allowed. |
| 26 | **A boundary is only usable if the allowed surface is sufficient** | `.opencode/README.md` | Every refusal sent the agent looking for a way around rather than through. Six rounds stalled on that before it was fixed. |
| 27 | **A snapshot taken too early is not evidence** | `ROADMAP.md` | A round was reported as incomplete because its log was read mid-run. The lesson is the same one the tests exist for. |
| 28 | **Two implementations, one parity check** | `docs/STATIC_SITE.md`, `web/parity.mjs` | Ports drift silently. The browser engine runs the same frozen cases, and CI fails on any disagreement — which it has, three times. |

## 7. Publishing

| # | Idea | Where | Claim |
|---|---|---|---|
| 29 | **Compile the data, not the language** | `docs/STATIC_SITE.md` | The engine is an interpreter over rows, so the rows ship as JSON and the site is static. No Pyodide, no server, no key. |
| 30 | **A static page cannot hold a secret** | `docs/STATIC_SITE.md` | Which is why the fallback rung is compiled out of the browser ladder rather than shipped and failed on. |
| 31 | **The browser reads a compiled artifact** | `docs/STATIC_SITE.md` | `content/` → `pc build` → `db` → `pc export` → `data.json`. The page never sees YAML or SQLite. |
| 32 | **Public data only** | `CONCERNS.md` C10 | Every row is public GitHub metadata or hand-written judgement about public work, and `git log` shows which. |

---

## Ideas that were argued and rejected

Kept, because a design is partly its refusals, and because each one is a trap a
reader will otherwise walk into.

| Rejected | Why | Where |
|---|---|---|
| A `products` table for a portfolio | one kind of noun, few queries; the column buys nothing here | C3, C13 |
| A `settings` table | one bot; a config file has no review to gate | C4 |
| `proposals` / `reviews` / `revisions` tables | git does all three, and the maintainer already lives in git | C5 |
| A `test_results` table | git provides the baseline; green on main, red on the branch | C5 |
| A global state machine | confines the visitor; a yielding flow does not | LEVELS §3 |
| Tables for composite questions | it is a resolution strategy, not data | LEVELS §5 |
| Reserved columns for multi-bot | the seam is a `Scope` object; empty columns rot | C4 |
| A model fallback by default | cost, and it would hide the signal the loop runs on | C1 |
| An echo fallback in the browser | `examples/hardcoded_bot.py --echo-demo`: it answers everything and therefore teaches nothing | `examples/` |
| Transpiling Python to JS | 10 MB of runtime to run 400 lines, and it still needs the data file | STATIC_SITE |
| Copying the markdown to the site separately | the documents are already in `data.json`, paired with their entity. A second copy is a second truth. | STATIC_SITE |
