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

## Verified and not verified

This config is written against the opencode docs
([commands](https://opencode.ai/docs/commands/),
[agents](https://opencode.ai/docs/agents/),
[permissions](https://opencode.ai/docs/permissions/),
[rules](https://opencode.ai/docs/rules/)) for opencode 1.18.x.

**Not verified:** the agents have not been executed here — opencode needs a log
directory outside this workspace, which the sandbox refused. So treat the first
`/maintain` as a smoke test: confirm the agent starts, that `pc inbox` output
lands in the prompt, and that an edit outside `content/` is actually refused.
The permission rules are the part most worth checking by hand.

**Verified:** every `pc` command the prompt injects works offline, and `pc build
&& pc test` is green (`docs/ROADMAP.md`).
