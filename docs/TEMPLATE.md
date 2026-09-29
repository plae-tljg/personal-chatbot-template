# Using this as a template

This repository is two things at once, and it is worth being clear which one you
want:

- **a working example** — a bot that answers questions about 42 real
  repositories, maintained by an agent through pull requests; and
- **a template** — the pattern, with the content swapped out.

Nothing in `personal_chatbots/`, `db/`, or `web/` is specific to this bot. The
whole of "whose bot is this" lives in five files.

---

## What to change

| file | what it holds | what to do first |
|---|---|---|
| `content/bot.json` | name, persona, locales, **which sources to ingest** | point `build.sources` at your own GitHub accounts |
| `content/curation.yaml` | hand-written entities and overrides | delete the examples, or keep two as shapes to copy |
| `content/knowledge.yaml` | what the bot says | rewrite: the patterns and templates are the bot |
| `content/tests.yaml` | what must stay true | **rewrite it — see below** |
| `AGENTS.md` | the agent's rules | the rules are generic; the project description is not |

`db/schema.sql`, `personal_chatbots/`, `web/` and the workflows need no edits to
start. That is the point of the design: the engine interprets rows, so a new
domain is rows.

## The one thing that will bite you

**`content/tests.yaml` asserts facts about *this* owner.** It checks that
`dsh-review` resolves to a specific repository, that a specific project groups
three specific repos, and so on. Change `sources` and `pc test` will fail — which
is correct behaviour, and confusing if you did not expect it.

```bash
# after pointing bot.json at your own accounts
python -m personal_chatbots build --offline=false     # ingest your repos
python -m personal_chatbots entities --summary        # see what you now have
# then rewrite content/tests.yaml against your own data
```

Keep the four *kinds* of case even as you replace the content — they are what
stops the bot drifting:

| kind | asserts | guards against |
|---|---|---|
| positive | an answer, and which entity it resolved | a row that can never fire |
| confusable negative | a refusal, on a similar-looking question | a row that steals questions |
| refusal | a refusal, on something genuinely unknown | invention |
| ambiguity | a refusal, when two entities could match | picking a favourite |

A suite of only happy paths cannot catch an over-broad row. `docs/CONCERNS.md`
C7 has the long version.

## A minimal first pass

1. **Two accounts, one knowledge row.** Point `sources` at one or two GitHub
   users, write a single `repo.about` row, and get `pc ask` answering.
2. **Seed the tests before the AI touches anything.** Hand-write a positive, a
   confusable negative, a refusal and an ambiguity case. C7 explains why they
   must not be written from the same evidence the AI will fit to.
3. **One maintenance round.** `opencode` → `/maintain`, or follow
   `skills/maintain-round.md` yourself.
4. **Then decide about a model.** The fallback rung is off
   (`docs/CONCERNS.md` C1); turn it on when the refusal rate tells you to, not
   before.

## What is generic and what is not

**Generic, and the reason to use this at all:**

- the four-homes split (`content/` → `db` → runtime), and that `content/` is the
  whole knowledge base rather than a seed that grows;
- `pc build` being an idempotent sync that archives rather than deletes;
- the ladder and the rule that a rung never guesses;
- the frame — reference resolution from the transcript, with no state;
- proposals being pull requests instead of a table;
- the validator refusing currency amounts in templates.

**Specific to this instance:** every row. The repositories, the groupings, the
templates, the personas, the test assertions.

## Where the design is written down

Read in this order:

1. `docs/CONCERNS.md` — the doubts, and the trigger that would change each answer
2. `docs/LEVELS.md` — the five concepts and the three levels
3. `docs/DESIGN.md` — the four homes, the ladder, the code seams
4. `AGENTS.md` — the rules an agent has to follow
5. `docs/ROADMAP.md` — what has been run, and what has not

`docs/STATIC_SITE.md` covers publishing it as a static page, including why the
browser engine cannot have a model fallback.
