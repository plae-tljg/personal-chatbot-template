# Running the maintenance agent

The design says the maintainer is an external command, not something embedded in
the runtime. This directory is that wiring for **opencode**; the same thing works
with any coding agent, because all of it is files.

Nothing here is used while serving. `pc serve` never reads this directory.

---

## The boundary, enforced by the tool

The claim in `docs/DESIGN.md` is that the AI's permissions are a property of the
filesystem, not of a prompt. `opencode.json` makes that literal:

```jsonc
"edit": {
  "*": "deny",            // the engine, the schema, the docs: read-only
  "content/*": "allow",   // what the bot says
  "content/**": "allow"   // what must stay true, and the judgement
}
```

The maintainer **cannot** edit `personal_chatbots/`, `db/schema.sql`, or
`AGENTS.md`. If a round needs a code change — a new `action.kind`, a new rung —
the honest outcome is that it says so and stops, because that is the boundary
between "new knowledge is data" and "new primitive is code".

Bash is restricted the same way: the `pc` commands and read-only git are allowed,
`git push` and `rm` are denied outright, and everything else asks.

---

## Usage

```bash
# once
opencode auth login          # or /connect in the TUI, and pick a provider

# see what is available, including the current free pool
opencode models | grep -i free

# run a round
opencode
> /maintain                  # the maintainer agent does one round and stops

# or just look, changing nothing
> /review
```

The commands are in `.opencode/commands/`. `/maintain` injects the live inbox and
stats into the prompt with `` !`python -m personal_chatbots inbox` ``, attaches
`content/knowledge.yaml` and `content/tests.yaml` with `@`, and hands the whole
thing to the `maintainer` agent. `/review` is the read-only version.

---

## Models

The commands default to a **free** OpenCode Zen model. Which ones exist rotates,
so check rather than trusting this list:

| Model id | Note |
|---|---|
| `opencode/space-bunny-free` | stealth model, zero-retention provider — **the default here** |
| `opencode/longcat-2.5-preview-free` | zero-retention provider |
| `opencode/big-pickle` | free during its stealth period; data may be used to improve it |
| `opencode/nemotron-3.5-lightning-free` | NVIDIA trial endpoint — do not send personal data |
| `opencode/mimo-v2.5-free` | free for a limited time |
| `opencode/ling-3.0-flash-fin-free` | free for a limited time |

**Read the privacy column, not the price column.** A maintenance round reads real
visitor questions out of the inbox, and several of the free models are free
*because* the provider may train on what you send. `space-bunny-free` and
`longcat-2.5-preview-free` follow a zero-retention policy; the NVIDIA trial
endpoints explicitly say not to submit confidential data. For a portfolio bot
whose inbox is public questions this is a small risk, but it is not zero, and it
is the kind of thing that should be a decision rather than an accident.

Change the model per agent in `opencode.json`, or per command in the frontmatter
of `.opencode/commands/*.md`:

```markdown
---
description: Run one maintenance round
agent: maintainer
model: opencode/longcat-2.5-preview-free
---
```

Any provider works — this is not tied to Zen. Point the agent at a local model
via Ollama if the inbox should never leave the machine.

---

## What a round actually costs

The runtime is free, so the only spend is the round itself:

```
maintain round   one agent session, in batch, offline     <- the only cost
pc build         pure Python over cached JSON             ~0
pc test          8 assertions                             ~0
serve            rows                                     0, always
```

The free models make the first number zero as well. Which is worth noticing: the
thing that makes this maintainable is not the model being cheap, it is that the
model is **not on the request path**. Put a free model in a RAG chatbot and you
still pay per request and still cannot test the result.

---

## Three gates, and which one does what

Running the agent for real produced a clean demonstration of how the safety
actually decomposes. It does not rest on any single mechanism:

| Gate | Stops | Where |
|---|---|---|
| **Edit boundary** | the agent touching the engine at all | `opencode.json` permission rules |
| **Validator** | broken content reaching the database | `pc build` |
| **Tests** | a behaviour change without a pin | `pc test`, and CI |

Verified directly, by asking the maintainer agent to append a line to three
files:

```
db/schema.sql              REFUSED
personal_chatbots/engine.py REFUSED
content/bot.json           ALLOWED
```

The refusal quotes the rule set it hit:

```
{"permission":"edit","pattern":"*","action":"deny"},
{"permission":"edit","pattern":"content/*","action":"allow"},
{"permission":"edit","pattern":"content/**","action":"allow"}
```

And the allowed edit is the interesting half: the agent appended `# boundary
probe` to `content/bot.json`, which is not valid JSON. The build caught it
immediately —

```
error: content/bot.json: Extra data: line 36 column 1 (char 1108)
```

— naming the file and the position. So a permitted edit that is *wrong* still
cannot ship. The boundary decides what the agent may change; the validator
decides whether the change was any good. Neither is sufficient alone.

## Verified and not verified

This config is written against the opencode docs
([commands](https://opencode.ai/docs/commands/),
[agents](https://opencode.ai/docs/agents/),
[permissions](https://opencode.ai/docs/permissions/),
[rules](https://opencode.ai/docs/rules/)) for opencode 1.18.x.

**Verified by running it:** the skill loads, the injected commands execute, the
edit boundary refuses what it should (above), the validator catches a bad
content edit, and `pc build && pc test` is green offline.

**Not finished:** seven rounds were run and **none reached an edit**. Rounds with
`opencode/space-bunny-free` did competent reconnaissance and then stopped;
`deepseek/deepseek-flash` got as far as correctly diagnosing a near-miss —
`ThreeBodySystemAnimation` carries the alias `three body system animation` but
not `three body simulation`, so the question refuses — and then stopped without
writing the pattern.

So the loop is **wired and safe, but not yet demonstrated end to end**. The gap
is agent capability, not the design: the config is model-agnostic, so a stronger
model is a one-line change in `opencode.json`. That is the honest state of it.

Everything that *is* verified was verified offline, with no network.
