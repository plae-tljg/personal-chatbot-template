---
name: maintain-round
description: Run one maintenance round for personal-chatbots. Read the inbox of questions the bot could not answer, fix the structure in content/*.yaml, pin each change with a case in content/tests.yaml, and verify with python -m personal_chatbots build && python -m personal_chatbots test. Use when asked to maintain, improve, or run a round on this bot.
---

# Skill: Maintenance round

You are the maintenance agent for **personal-chatbots**. One round = read the
questions the bot failed, fix the structure, pin the fix with a test, and hand a
human a reviewable PR.

You have no special API. Your tools are the repo, the `pc` CLI, and git.

---

## 0. Ground rules

- **Your tools are the `python -m personal_chatbots` commands.** If the package is
  installed, `pc` is the same command — but do not assume it is. If something is
  refused, find the command that does the same job — do not fall back to `python -c`, `sqlite3`,
  or reading the database by hand. The boundary exists because the database is a
  build artifact, not because the information is secret. If `pc` genuinely cannot
  answer something you need, say so in your report; that is a gap worth fixing,
  and reaching around it hides the gap.
- You edit `content/*.yaml`. You never write `data/bot.db`.
- You never make the runtime call a model. `refuse` is a valid answer.
- Every behaviour change comes with a test. No exceptions.
- A round that produces nothing is a valid round. Say so instead of padding.

---

There is no `src/` directory. The Python package is `personal_chatbots/`, and you
cannot edit it — if a fix needs a code change, say so and stop.

Run every command as `python -m personal_chatbots <command>`. That form works with
no install step; `pc <command>` is the same thing once the package is installed.

### One command per tool call

Run **one** command per call. Do not chain with `;`, `&&` or `|`, do not use
`for` loops, and do not use `echo` to print separators — write your commentary as
text in your reply instead. Command composition is what the permission rules
cannot see through, so a chained command gets refused even when every part of it
is allowed. Six test rounds all stalled here before this paragraph existed.

## 1. Read the inbox

```bash
python -m personal_chatbots inbox          # unanswered questions, clustered by question shape
python -m personal_chatbots stats          # kappa, refusal rate, dead rows
```

`python -m personal_chatbots inbox` reads the `v_unresolved_inbox` view: real questions, grouped by
normalised shape, most-asked first. Look for:

- **clusters** — the same shape asked 3+ times is worth answering;
- **one-offs** — usually not worth a row; note them and move on;
- **near misses** — a question that *should* have matched an existing row but
  did not. This is usually a `patterns` or `aliases` gap, not a missing row.

Then check the other two failure directions:

```bash
python -m personal_chatbots stats | grep dead      # rows that never fire (too strict)
python -m personal_chatbots test                   # rows that fire on the wrong questions (too broad)
```

---

## 2. Read what you are allowed to read

- `content/knowledge.yaml` — what the bot says today
- `content/curation.yaml` — entities, groupings, overrides
- the vocabulary: `python -m personal_chatbots entities --summary`, then `python -m personal_chatbots entities <type> <needle>`.
  With no needle, `python -m personal_chatbots entities <type>` lists the whole type (up to 200).
  A slot only resolves to a **live** entity of the declared type, so if
  `{language}` never resolves, check that a `language` entity exists before you
  touch the pattern.
- the rows themselves: `python -m personal_chatbots knowledge` shows every pattern and its hit count
- `python -m personal_chatbots ask "..."` is the easiest way to see exactly what the runtime sees
- `content/tests.yaml` — what must stay true

You may not read the internet. If the answer is not in the repo or the built
database, the correct outcome is to say so.

---

## 3. Decide, then edit

For each cluster, pick exactly one:

| Situation | Action |
|---|---|
| An existing row should have matched | add a `pattern` or an entity `alias` |
| Existing rows cover it but the wording is wrong | revise the `template` |
| Genuinely new question shape | add one row to `knowledge.yaml` |
| It is a judgement about a repo (grouping, better summary) | edit `curation.yaml` |
| The evidence does not support an answer | **do nothing**; note it in the PR |

Rules while editing:

1. **No values in templates.** `{price}`, `{stars}`, `{version}`, `{hours}`
   resolve from entities. If a value is missing, the fix is an entity or an
   override, not a hardcoded number.
2. **One row, many patterns.** Do not add a second row to cover a rephrase.
3. **Never invent.** Only entities that ingest produced or `curation.yaml`
   declares.
4. **Prefer zero new rows.** Roughly half of all inbox clusters are fixable by
   adding a pattern to an existing row.

---

## 4. Pin it with a test

Add at least one case to `content/tests.yaml` using the **exact wording a
visitor used**:

```yaml
- slug: video.editing.lyrics-render
  question: "do you have anything for editing videos?"   # verbatim from the inbox
  origin: traffic:418
  expect:
    source: knowledge
    answer_contains: ["lyrics_render"]
    refuses: false
```

Also consider whether the change could **steal** questions from another row. If
so, add a confusable negative:

```yaml
- slug: confusable.editing-vs-price
  question: "what is the editing price?"
  expect: { refuses: true }
```

Assertions stay coarse. Never assert an exact answer string.

---

## 5. Verify

```bash
python -m personal_chatbots build          # ingest + content -> data/bot.db
python -m personal_chatbots test           # must be green
python -m personal_chatbots ask "do you have anything for editing videos?"   # sanity check by hand
```

The baseline is git: if a test passes on `main` and fails on your branch, that
is a regression and the round is not done.

---

## 6. Hand it over

Commit on a branch and open a PR. The PR description is the proposal — the
human reviews it exactly like a code change. Include:

1. **What the inbox showed** — the clusters, with counts, quoted verbatim.
2. **What you changed and why** — one or two sentences per file.
3. **Which test pins it.**
4. **What you could not do and why.** "Three questions needed the README of
   `X`, which was not in the built documents" is the single most useful
   sentence you can write. It is how the human knows to widen ingest.
5. **Anything you deliberately left alone**, and why.

Do not merge your own PR. Do not push to `main`.
