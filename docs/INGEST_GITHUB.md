# Ingest — seeding from GitHub

The bot's knowledge comes from four public GitHub identities:

| source slug | account | repos | character |
|---|---|---|---|
| `github:plae-tljg` | `plae-tljg` | 7 | primary: personal site, DSH plugin, MaaFwPhoneAI |
| `github:LKM-Repo` | `LKM-Repo` | 12 | infra / AI experiments (RAG, GPU, voice, asterisk) |
| `github:ellkaimu` | `ellkaimu` | 10 | Android apps, daily-utility tools |
| `github:plae-lkm` | `plae-lkm` | 13 | small scripts with personality (cables, yinyang, three-body) |

43 repositories total. That is enough real, messy data to prove the design:
mixed languages, snake_case and CamelCase names, forks mixed with originals,
empty descriptions, and one repository with no language at all.

---

## 1. What "seed" means here

Ingest writes **library rows only**: `entities`, `entity_links`, `documents`,
Provenance is the `entities.origin` text column (`'github:plae-tljg'`), not a
`sources` table, and there is no run ledger to update — the build log is enough.

It does **not** write `knowledge`. Facts about repos and the sentences the bot
says about them are different things, and the first `knowledge` rows are
author-written seeds (a human's editorial voice, once), after which the
maintenance agent revises them, in `content/knowledge.yaml`. Keeping ingest out
of `knowledge` is what stops
the bot from sounding like a README concatenator.

---

## 2. API calls

| purpose | endpoint | notes |
|---|---|---|
| account identity | `GET /users/{u}` | name, bio, avatar, public_repos |
| repository list | `GET /users/{u}/repos?per_page=100&sort=pushed` | one page is enough for all four accounts |
| repo detail | `GET /repos/{owner}/{name}` | only when the list payload changes |
| README | `GET /repos/{owner}/{name}/readme` (Accept: `application/vnd.github.raw`) | becomes a `documents` row |
| languages | `GET /repos/{owner}/{name}/languages` | optional; the primary `language` field is usually enough |

Rate limits: 60 requests/hour unauthenticated, 5000/hour with a token. A full
seeding of four accounts is 4 + 4 + 43 = 51 requests, so it fits in the
unauthenticated budget but only if README fetches are cached. Use a token
(`GITHUB_TOKEN`) for anything repeated, and treat the token as an ingest-only
credential that has no access to this project's database.

**The API is not the runtime.** All requests happen in `ingest`, offline from
the serving path. The bot answers from rows, and must keep answering when
GitHub is down.

---

## 3. Field mapping

### Repository → `entities`

| GitHub field | destination | reasoning |
|---|---|---|
| `full_name` | `key` | stable natural key, survives renames via `id` check |
| `name` | `name` | |
| `description` | `summary` | already one line; the natural `{summary}` slot |
| `html_url` | `url`, `source_ref` | citations resolve from here |
| `id`, `node_id` | `attrs_json.github_id` | change detection when a repo is renamed |
| `topics[]` | **links** `tagged` → `topic:*` | they are nouns; question 4 traverses them |
| `language` | **link** `uses_language` → `language:*` | same reason |
| `fork` | `attrs_json.fork` + optional `forked_from` link | forks must not be presented as original work |
| `archived` | `attrs_json.archived`, `status` | drives whether the bot still recommends it |
| `homepage` | `attrs_json.homepage` | carried, rarely queried |
| `license.spdx_id` | `attrs_json.license` | carried |
| `default_branch` | `attrs_json.default_branch` | carried |
| `pushed_at` | `attrs_json.pushed_at` | "recently active" questions sort on it |
| `created_at` | `attrs_json.created_at` | carried |
| `stargazers_count` | `attrs_json.stars` | ranked questions |
| `forks_count` | `attrs_json.forks` | |
| `size` | `attrs_json.size_kb` | |
| `open_issues_count` | `attrs_json.open_issues` | |

The rule from `docs/DESIGN.md` §5 applied literally: **zero promoted metric
tables, ten carried fields, two link types.**

Stars and sizes go into `attrs_json` and are read with
`json_extract(attrs_json, '$.stars')`. At 42 rows that is instant, and promoting
them to sorted rows or a `repo_stats` table buys nothing yet. The trigger to
promote is written down in `docs/CONCERNS.md` C3: when a query sorting on JSON
actually hurts. The two link types stay because a relation in a JSON array
cannot be queried backwards — that one gets harder to add later, not easier.

### Account → `entities`

`entity_type='account'`, `key` = login, `summary` = bio, `url` = profile URL,
`attrs_json` carries `public_repos` and `followers`.

### Person → `entities`

One row: `entity_type='person'`, `key='plae-tljg'`, `name='plaetljg'`,
`summary` = the profile bio. Each account links `owned_by` to it. This is what
makes the answer to "who is this?" a traversal rather than a hardcoded string.

> Note the honest wrinkle: the profile bio is "Plasticity, Elasticity, Torsion."
> and the location is a lyric. The bot should not present those as a
> professional summary. This is a real example of why `knowledge` rows are
> human-seeded first: raw upstream data is not an editorial voice.

### README → `documents`

`slug = 'readme:{owner}/{name}'`, `title = name`, `kind='readme'`,
`entity_id` set, `content_hash` computed. Re-ingest updates the row only when
the hash changes.

READMEs are truncated for storage sanity (first ~8 KB is plenty); the full text
is not needed to answer, and a smaller corpus makes the FTS rung faster and the
maintainer's context cheaper.

### Topics → `entities` (`topic`)

Each topic string becomes a `topic` entity (`key='topic:maafw'`), linked
`tagged`. Topics are the cheapest high-value signal in the whole dataset — they
are already keywords a human chose.

### Languages → `entities` (`language`)

`key='language:python'`, normalized to lowercase; `name` keeps display casing.

---

## 4. Names, aliases, and why resolution needs them

Slot resolution has to map "dsh review" → `plae-tljg/dsh-review` and
"finance management app" → `Finance-Management-App`. GitHub naming is hostile
to this: `Type_As_If_You_Are_Working`, `ThreeBodySystemAnimation`,
`i_love_you_web`, `Finance_Lux_Web`.

At ingest, generate aliases mechanically:

```
normalize(s) = lowercase, strip non-alphanumerics, collapse runs

plae-tljg/dsh-review          → "dshreview"
plae-tljg/Finance-Management-App → "financemanagementapp"
LKM-Repo/PIKE-RAG_Verbose     → "pikeragverbose"
```

and split camelCase / snake_case / dashes into a spaced form:

```
"Finance-Management-App" → "finance management app"
"Type_As_If_You_Are_Working" → "type as if you are working"
"ThreeBodySystemAnimation" → "three body system animation"
```

Store both forms in `aliases_json`. Resolution then matches the normalized
question against `name` and every alias, longest match first, and requires a
**unique** winner — ambiguity falls through to refusal rather than guessing
between `Finance-Management-App` and `Finance-Management-Web`.

Owner-qualified names are aliases too: `plae-tljg/dsh-review`,
`dsh-review`, `dshreview`.

---

## 5. A hand-made layer: `project` entities

The API gives 42 flat repositories. A human sees families:

| project | members |
|---|---|
| Finance Management | `Finance-Management-App`, `Finance-Management-Web`, `Finance_Lux_Web` |
| Voice Cloning (MUSA) | `GPT-SoVITS-Musa`, `so-vits-svc-musa`, `Retrieval-based-Voice-Conversion-WebUI-musa` |
| MaaFramework work | `MaaFwPhoneAI`, `maaMines`, `FGA` |
| Local AI infra | `Graph_Chatbot`, `PIKE-RAG_Verbose`, `domain-ops-agent`, `AI_Plan_Cost_Web` |

These groupings are **not** in the API. They are exactly the kind of judgement
an AI maintainer should propose and a human should confirm — an
new `project` entity in `content/curation.yaml`, plus `includes` links.
It is the single best demo of the whole thesis: a genuinely useful
non-mechanical contribution, arriving through the review gate, with a rationale
a human can argue with.

Ship v1 with one or two of these seeded by hand so the `project` type exists in
the vocabulary, then let the maintainer propose the rest.

---

## 6. Idempotency

```
for each repo from the API:
    hash = sha256(json of the mapped fields)
    row  = entities where (entity_type='repo', key=full_name)

    if row is None:              insert, count created
    elif row.content_hash != hash: update, count updated, mark "changed"
    else:                        count unchanged, write nothing

    metrics: upsert only when the value differs
    links:   insert-if-absent, archive links that disappeared upstream
```

Guarantees:

1. Running ingest twice in a row produces zero `updated_rows` on the second run.
2. A repo that disappears upstream is archived, never deleted — its answers and
   citations stay valid.
3. Renames are detected through `attrs_json.github_id`, which is the only stable
   identifier GitHub offers.
4. Ingest writes **nothing into `content/`**. It is a data pull, not a judgement.

---

## 7. Failure modes to design for

| failure | behaviour |
|---|---|
| rate limited (403) | run status `partial`, keep cursors, resume next run |
| one README 404s | skip that document, mark the run `partial`, do not fail the whole run |
| `description` is null | `summary=''`; the validator warns when a live knowledge row needs `{summary}` from it |
| repo has no language | no `uses_language` link; "which projects use X" simply excludes it |
| two repos normalize to the same alias | resolution requires a unique winner, so the collision is recorded and both fall through |
| GitHub returns a repo renamed mid-run | matched by `github_id`; key updated and the change recorded on `entities.updated_at` |

The last two are the ones that matter: **collisions and renames are where a
data-driven bot silently starts lying.**

---

## 8. The seed `knowledge` rows

`content/knowledge.yaml` ships with 11 rows. The first batch was the
human's one-time editorial act; `repo.author` was added later by a maintenance
round, which is the point.

| slug | pattern(s) | action |
|---|---|---|
| `owner.intro` | `who is {person}` / `what does {person} do` | answer, cites the profile |
| `owner.accounts` | `which accounts does {person} publish under` | list over `account` via `owned_by` |
| `repo.about` | `what is {repo} about` (+ the `the {repo}` variants) | answer `{repo} — {repo.summary}`, cites the repo |
| `repo.author` | `who made {repo}` / `who created {repo}` | list over `account` via `owned_by` |
| `repo.list` | `what projects does {person} have` | list over `repo`, newest first, limit 10 |
| `repo.ranked` | `what is your most starred repo` | list ordered by `attrs.stars`, limit 5 |
| `repo.by_language` | `which projects use {language}` | list via `uses_language` |
| `repo.by_topic` | `what have you built with {topic}` | list via `tagged` |
| `project.about` | `what is the {project} project` | list via `includes` |
| `contact` | `how do i contact {person}` | answer |
| `meta.provenance` | `where did you get this` | answer, points at the sources |

11 rows for 42 repositories across four accounts, and 14 frozen
cases pinning them. Multi-pattern rows are what keep the count down: adding a
rephrase to an existing row costs a line, while adding a row costs a row plus a
test plus future maintenance.

`repo.author` is the interesting one, because a maintenance round added it and
the shape was not obvious: attribution is a different question from description,
and the answer comes from the `owned_by` link rather than a hardcoded name.

There is no "unknown" row. Refusal is the last rung of the ladder, not data
(`docs/DESIGN.md` §3) — that way it cannot be edited into answering something it
has no grounds for.

If 11 rows cannot describe 42 repositories, the problem is the design of
the rows, not the number of them.
