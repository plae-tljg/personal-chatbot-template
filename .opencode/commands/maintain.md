---
description: Run one maintenance round — read the inbox, fix the structure, pin it with a test
agent: maintainer
---

You are running one maintenance round for **personal-chatbots**. The full
procedure is in `@skills/maintain-round.md` — read it first and follow it.

`@AGENTS.md` states the hard rules. The short version: you edit `content/*.yaml`,
you never write `data/bot.db`, and every behaviour change comes with a case in
`content/tests.yaml`.

## Start here

The inbox as it stands right now:

!`python -m personal_chatbots inbox 2>&1 | head -60`

The current health:

!`python -m personal_chatbots stats 2>&1 | head -40`

What the bot says today:

@content/knowledge.yaml

What must stay true:

@content/tests.yaml

## What to do

1. Cluster the inbox. A shape asked more than once is worth a row; a one-off
   usually is not. A question that *should* have matched an existing row is a
   pattern or alias gap, not a missing row.
2. For each cluster, pick exactly one: add a `pattern` to an existing row, revise
   a `template`, add one new row, or edit `curation.yaml`. **Doing nothing is a
   valid outcome** — say so.
3. Never put a value in a template. `{price}`, `{stars}`, `{version}` resolve
   from an entity; the validator rejects a currency amount outright.
4. Add at least one case to `content/tests.yaml` using the visitor's **exact
   wording**. Also add a confusable negative if your change could steal a
   question from another row.
5. Verify, and keep going until it is green:

   !`python -m personal_chatbots build --offline --cache tests/fixtures && python -m personal_chatbots test`

6. Stop. Do not commit, do not merge, do not push. Summarise for the human.

## What to report

- which clusters you acted on, with counts, quoted verbatim
- what you changed and why, one or two sentences per file
- which case pins each change
- **what you could not do and why.** "Three questions needed the README of X,
  which was not in the built documents" is the most useful sentence you can
  write — it is how the human knows to widen ingest
- anything you deliberately left alone
