# Levels and Concepts

The question "does the bot need state?" is not a matter of degree. It changes
what kind of thing you are building, and each kind needs a different mechanism.

This document names the four concepts, groups them into three levels, and says
exactly what each level costs to add. **The maintenance agent must know this
taxonomy**, because "make the bot handle X" means different work at different
levels — and because the wrong mechanism at the wrong level is how a small
system turns into an unmaintainable one.

---

## 1. Five concepts, on two axes

Two questions decide everything:

- **Does it need to know about earlier turns?** (cross-turn)
- **Does it produce something outside the conversation?** (side effect)

| Concept | Chinese | Cross-turn | Stored state | Outside effect | Mechanism |
|---|---|---|---|---|---|
| **Fact** | 事实行 | no | no | no | `knowledge` rows |
| **Reference** | **指代 / 语境帧** | **yes** | **no — derived** | no | the frame (`frame.py`) |
| **Flow** | 流程 | yes | **yes** | no | `flow_states` (L2) + `flows` (built) |
| **Task** | 任务 | no | yes | **yes** | `tasks` (L2) |
| **Composite** | 复合问句 | no | no | no | a ladder rung, **no tables** (L3) |

### The third column is the one that was missing

An earlier version of this document had four concepts and treated "cross-turn" as
a synonym for "needs a state machine". That is wrong, and everyday conversation
is the counter-example:

```
› what projects does LKM have?
  • Finance-Management-App … • Anime-Webview … • domain-ops-agent … etc
› tell me about the second one
› and the third?
› it
```

Nothing here is state. It needs the *previous answer*, which is already stored so
that answers stay traceable. So:

> **Reference is cross-turn without being stateful.** It reads the transcript;
> it does not own a variable.

This distinction matters because the two are priced completely differently:

| | Reference (frame) | Flow (state) |
|---|---|---|
| Storage | **none** | `flow_states` (live table) |
| Where the truth is | the transcript you already keep | a parallel record you must keep correct |
| Failure mode | resolves to the wrong thing, visibly | the visitor gets stuck in a step |
| Cost to add | a rewrite step | live state, plus an escape hatch, plus a timeout |

The frame is a **projection** of `messages.citations_json`:

```
items    the citations of the most recent answer that listed several things
subject  the single entity of the most recent answer that discussed one
```

Both are bounded to the last few answers (`DEFAULT_WINDOW = 6`), so a list from
twenty turns ago cannot hijack the current question.

**It is a rewrite, not a rung.** "the second one" becomes "Anime-Webview" before
the ladder runs, so every rung below stays exactly what it was. That is why
adding context added no tables, no state, and no new answer path.

Two rules keep it honest:

1. **No context, no resolution.** An ordinal with no list behind it resolves to
   nothing and the question falls through to refusal. It never guesses.
2. **The rewrite is visible.** `messages.refs_json` records what a phrase was
   read as, and the UI shows it. A rewrite the visitor cannot see is
   indistinguishable from a guess.

### Two things the frame does *not* do

- **It does not collect values.** "How many do you want?" then "3" needs a slot
  that persists across turns — that is a Flow, and this is the line between the
  two.
- **It does not carry a task.** Switching products mid-question works here
  because the subject is derived, not held. What a Flow adds is a *pending
  question*, and that is the only thing it has to store.

## 2. The three levels

| Level | Name | What it adds | Live tables | Ladder |
|---|---|---|---|---|
| **L1** | **Facts** | answer questions | `messages` | knowledge → entity → search → refuse |
| **L2** | **Flow + Task** | remember a conversation; hand work to a human | + `flow_states`, `tasks` | + `flow` (first rung) |
| **L3** | **Composite** | answer a question that contains several questions | (none) | + `composite` (before knowledge) |

The project ships at **L1**. `content/bot.json` carries `runtime.level` so the
agent can see which level it is working at without guessing.

### Why L1 first

- L1 is a complete, honest product. It answers 42 repositories from ten rows,
  refuses what it does not know, and records the refusal.
- L2 and L3 are **appends**, not rewrites. The seams already exist:
  `messages.session_id` gives every turn a conversation identity, `ladder` in
  `bot.json` is an ordered list, and a new `action.kind` is new code by design.
- Going up a level before the inbox justifies it means maintaining state and
  flows that nobody uses — the two most expensive kinds of dead weight.

### Why L2 is probably worth it anyway

The showoff value is real. "Ask what projects exist → ask about hiring → leave a
contact → a task appears for the host" is a demonstration that the bot is not a
FAQ page. That is exactly the "future business" goal. So L2 is the natural
second act, not a hypothetical.

Note something useful about that example: **the first half is still L1.** "How
much to hire you" is a fact row. A flow is only needed at the moment the
conversation must *remember* something — here, when it starts collecting a
contact after the question is already answered. Add state where memory begins,
not where a topic begins.

---

## 3. Level 2 — Flow

A flow is a **state tree: the branch is chosen by what already happened.**

Definitions live in `content/flows.yaml` (a build artifact). Session state lives
in a live table that `pc build` never touches.

**Triggers stay in `knowledge.yaml`**, so there is exactly one pattern-matching
engine:

```yaml
# content/knowledge.yaml
- slug: hire.start
  patterns:
    - "how much to hire {person}"
    - "are you available for work"
  slots: { person: person }
  action: { kind: flow, flow: hire }      # <- starts a flow instead of answering
```

```yaml
# content/flows.yaml
- slug: hire
  steps:
    - say: "Depends on scope. What kind of work is it?"
      ask: kind
      options: [app, automation, consulting]

    - say: "Roughly how long?"
      ask: duration

    - say: "Leave a contact and I'll pass it on."
      ask: contact

    - action:                            # terminal step: side effect
        kind: create_task
        task_kind: lead
        title: "Hiring enquiry: {kind} / {duration}"
        body: "{contact}"
      say: "Thanks — I've logged this for LKM."

    - say: "Changed my mind? Say 'cancel' any time."   # optional escape
      on: { cancel: abandon }
```

Storage:

```sql
-- built from content/flows.yaml, rebuilt every pc build
CREATE TABLE flows (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  slug       TEXT NOT NULL UNIQUE,
  name       TEXT NOT NULL DEFAULT '',
  steps_json TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'live'
             CHECK (status IN ('draft', 'live', 'archived')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- LIVE: session state. Never rebuilt, never hand-edited.
CREATE TABLE flow_states (
  session_id TEXT PRIMARY KEY,
  flow_slug  TEXT NOT NULL,
  step_index INTEGER NOT NULL DEFAULT 0,
  slots_json TEXT NOT NULL DEFAULT '{}',
  status     TEXT NOT NULL DEFAULT 'active'
             CHECK (status IN ('active', 'completed', 'abandoned')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Runtime change: **one rung, first in the ladder.**

```
flow        if a flow is active for this session, advance it
knowledge   ...
```

### The rule that makes flows safe for arbitrary questions

A single global state machine is the wrong tool here: it *confines* the visitor,
so any question that is not the next step either breaks the flow or gets
refused. A flow must instead **yield the turn whenever the input is not an
answer to its question.**

```
flow is active, waiting for slot `budget`
visitor: "around 3000. by the way, how long is the refund window?"
   1. offer the turn to the budget parser
        parsed -> fill the slot, advance the flow
        not parsed -> do NOT force it and do NOT abandon it
   2. fall through to the normal ladder and answer what was actually asked
   3. the flow stays `active` at the same `step_index` -- nothing was consumed
next turn: offer the turn to `budget` again
```

Pause and resume come for free, because the flow's state is persistent and the
turn was never spent on it. There is no stack to push and nothing to restore.

| | global state machine | yielding flow |
|---|---|---|
| the visitor is "in" a state | yes, and can do nothing else | no — the flow asks, it does not confine |
| an off-script question | breaks, or is refused | answered by the normal ladder |
| resuming after a detour | needs an explicit stack | nothing to do; the state was never consumed |
| state explosion | one machine per path through the graph | at most one row per session |

This is why "the bot must handle arbitrary questions" and "the bot must walk
someone through collecting a lead" are not in conflict here.

### Rules

1. **A flow yields the turn** (above). It is a rung that gets first refusal on
   the input, not a mode the conversation enters.
2. **A flow never guesses a slot.** No free-text interpretation at L2. If the
   input does not parse, rule 1 applies — answer the question, keep the state.
3. **A flow does not nag.** After yielding, it may append one soft reminder
   ("still want to finish the enquiry?"), **at most once**. Then it waits to be
   resumed explicitly, or times out.
4. **No stack until there is a reason.** A stack is only needed to run a
   *second* flow to completion and then return to the first. Pausing covers the
   common case. Trigger to add one: a real session in which two flows must both
   be active at once.
5. **One active flow per session.** Concurrent flows are the thing rule 4 is
   about, and they are not supported until they happen.
6. **A flow is escapable.** Every flow needs a cancel path (`cancel`, `stop`,
   `never mind`), or a visitor gets stuck.
7. **A flow ends by timeout, not by interruption.** `status='abandoned'` after N
   minutes of inactivity; the next message starts fresh. An interruption is not
   an abandonment — that is the whole point of rule 1.
8. **Steps are data; step *actions* are primitives.** `say`, `ask`,
   `create_task` are code. A new one is new code — the same boundary as
   `action.kind` for knowledge.

### Why `status` alone is not the answer

`status` (`active` / `completed` / `abandoned`) is the **lifecycle**. The state
machine itself is:

```
step_index    which step is waiting
slots_json    what has been collected so far
```

So "the state machine" here is **two columns and a list of steps** — not a
framework. `status` says whether the flow is still alive; `step_index` says
where it is; `slots_json` says what it knows.

### How this maps to the usual "hybrid architecture" advice

The common recommendation for bots that accept arbitrary questions is: an
understanding layer, a memory/state layer, a policy-routing layer, and
pluggable task FSMs with interrupt-and-resume. That is this design. Dictionary:

| Common term | Here |
|---|---|
| understanding layer (intent, entities, retrieval) | the ladder: pattern match + slot resolution + FTS |
| memory / state layer | `messages` (history) + `flow_states` (task, slots) |
| policy / routing layer | `LADDER` in `bot.json` — an ordered list of rungs |
| pluggable task FSMs | `flows`, one row per flow, keyed by slug |
| interrupt, push to stack, resume | the flow simply is not advanced (rule 1) |
| fallback / clarify | the `refuse` rung |
| hand off to a human | a `Task` row |

Deliberately not adopted:

- **sentiment detection** — no model at runtime, and it changes nothing here;
- **user profiling** — privacy cost with no benefit for a portfolio bot;
- **constraint narrowing over a catalogue** ("budget 3000, not Apple → fewer
  candidates") — this *is* worth having once there is a catalogue, and it is
  still data: a `list` action whose filters come from flow slots
  (`where: {price_max: "{budget}", brand_not: "{exclude}"}`). Add it with the
  catalogue, not before.

## 4. Level 2 — Task

A task is **a thing that leaves the conversation and waits for a human**. It is
the only mechanism in this design whose output is consumed outside the system.

```sql
-- LIVE: never rebuilt
CREATE TABLE tasks (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  public_id  TEXT NOT NULL UNIQUE,
  kind       TEXT NOT NULL,              -- lead | bug | request
  title      TEXT NOT NULL,
  body       TEXT NOT NULL DEFAULT '',
  status     TEXT NOT NULL DEFAULT 'pending'
             CHECK (status IN ('pending', 'done', 'dismissed')),
  session_id TEXT NOT NULL DEFAULT '',
  flow_slug  TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  handled_at TEXT,
  handled_by TEXT NOT NULL DEFAULT '',
  note       TEXT NOT NULL DEFAULT ''
);

CREATE VIEW IF NOT EXISTS v_open_tasks AS
SELECT id, public_id, kind, title, body, created_at
FROM tasks WHERE status = 'pending' ORDER BY created_at;
```

```bash
pc tasks              # list open tasks
pc tasks done 3       # mark handled
```

Design rules:

1. **Tasks are created deterministically.** The flow collected the data; no
   model is involved. A lead capture that depends on a model call is a lead
   capture that fails when the API is down.
2. **Tasks are never auto-closed.** `done` and `dismissed` are human decisions.
3. **A task is not a message.** Do not model it as a special `messages` row; it
   has a different lifecycle and a different reader.
4. **The maintenance agent may summarize open tasks** in its round ("three
   pending leads, two from the same company") — that is a reading job, not a
   writing one.

---

## 5. Level 3 — Composite

A composite is **a clause tree: the branch is chosen by the structure inside one
sentence**, not by history. "What is dsh-review, and how much do you charge?"
contains two questions that have nothing to do with each other.

**Naming.** It is a decision tree, but on a different axis from a flow, and
conflating the two names causes real design mistakes:

| | Flow | Composite |
|---|---|---|
| Chinese | **流程 / 时序树** | **复合问句 / 并列树** |
| Branches on | what already happened | what is inside the sentence |
| Axis | across turns (vertical) | within one turn (horizontal) |
| State | yes | no |
| Storage | `flow_states` | **none** |

**Mechanism: one ladder rung, no tables.**

```
composite   split on explicit conjunctions -> resolve each part -> merge
flow        ...
knowledge   ...
```

Rules:

1. **Split only on explicit markers** (`and`, `also`, `plus`, `、`, `还有`,
   `以及`). Never guess a split from punctuation or length.
2. **Cap the parts** (`max_parts`, default 3). A five-part question is a user
   pasting a list, not a compound question.
3. **Every part resolves independently, and a part that fails is named.**
   "I can answer the first part; the second one is not in my tables." Naming the
   failure is what keeps this honest — silently dropping a clause is worse than
   refusing the whole thing.
4. **Merge, do not concatenate.** Two list answers to two parts become one
   deduplicated list, not the same repo twice.
5. **Never split while a flow is active.** A flow owns the turn; decomposing its
   input would corrupt the step machine.
6. **Citations are unioned and deduplicated.** The composite answer must be as
   traceable as the simple one.

Config, not data (`bot.json`):

```json
"composite": { "enabled": false, "split_on": [" and ", " also ", " plus ", "、"],
               "max_parts": 3 }
```

Tests for L3 read naturally in `content/tests.yaml`, because the assertion
language already supports multiple expectations:

```yaml
- slug: composite.repo-and-contact
  question: "what is dsh-review and how do I contact you?"
  expect:
    source: composite
    answer_contains: ["dsh-review", "issue"]
    cites: ["plae-tljg/dsh-review"]
- slug: composite.partial-honesty
  question: "what is dsh-review and what is your hourly rate?"
  expect:
    source: composite
    answer_contains: ["dsh-review"]
    names_unanswered: ["hourly rate"]     # partial answers must name the gap
```

**Do L3 last.** It multiplies the surface of every other rung: every answer
shape must now be mergeable. It is a real improvement for a visitor, and it is
the cheapest thing on this page to get subtly wrong.

---

## 6. What each level costs to add

| | New content files | New tables | New rungs | New primitives (code) |
|---|---|---|---|---|
| L1 → L2 | `flows.yaml` | `flows` (built), `flow_states` + `tasks` (live) | `flow` | `say`, `ask`, `create_task` |
| L2 → L3 | — | — | `composite` | splitter + merger |

Nothing at L1 has to change for either. That is the point of the seams, and the
reason `runtime.level` is a config field rather than a separate codebase.

---

## 7. The invariant that makes this safe

> **Live tables are `messages`, and at L2 `flow_states` and `tasks`.
> `pc build` never touches them.**

Everything else is a projection of `content/` plus ingest. So:

- going up a level adds live tables — it never puts live state into a rebuilt
  table;
- a rebuild can never destroy a conversation, a half-finished flow, or a lead;
- `pc build` stays safe to run at any time, including in the middle of a
  production day.

This is the same rule as C6, extended. Get it wrong once — put a counter or a
session flag in a built table — and the whole "rebuild freely" property is gone.

---

## 8. How the agent should use this document

Before changing anything, establish the level:

1. Read `runtime.level` in `content/bot.json` and the `ladder` next to it.
2. Classify the request:
   - "the bot should know X" → **Fact**. Edit `knowledge.yaml` or `curation.yaml`.
   - "the bot should remember / walk me through / collect something" → **Flow**.
   - "someone should be told about this" → **Task**.
   - "it should handle questions with several parts" → **Composite**.
3. If the request needs a level above the current one, **say so in the PR
   description** instead of quietly implementing half of it. Raising the level is
   a decision for the human, because it adds live tables and a permanent
   maintenance surface.
4. Never fake a higher level with a lower mechanic. The classic versions of this
   mistake: stuffing conversation state into `knowledge` templates; creating a
   `messages` row and calling it a task; splitting compound questions with a
   regex inside an answer template.
