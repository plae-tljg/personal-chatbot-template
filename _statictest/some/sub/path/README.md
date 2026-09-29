# The static build

`personal-chatbots` runs as a plain static site. The ladder is a small
interpreter over rows, and the rows are data — so the rows ship as JSON and the
whole bot runs in the browser.

```bash
python -m personal_chatbots build --offline --cache tests/fixtures
python -m personal_chatbots export          # writes web/data.json
python -m http.server -d web 8080           # or any static host
```

That is the entire deployment. No server process, no database, no API keys, no
hosting cost, and nothing on the request path that can fail or be billed.

## Why this is not a fork

Two implementations of one interpreter drift. What stops that here:

```
personal_chatbots/*.py   the Python runtime   (serves via pc serve)
web/engine.js            the browser runtime  (serves from a static host)
        \                        /
         `--- content/tests.yaml `--- the frozen cases both must pass
```

`pc export` ships `content/tests.yaml` inside `data.json`, so the browser engine
can run the same assertions the Python runtime runs. `web/parity.mjs` does it
from the command line and CI calls it:

```bash
python -m personal_chatbots export && node web/parity.mjs
# ok repo.about.dsh-review
# ...
# 8/8 cases agree with the Python runtime
# 4 probe questions agree
```

Open `web/index.html` and press **run the frozen cases here** — the same eight
pass in your browser, with no server involved. That button is the whole
architecture in one gesture: if the two engines ever disagree, CI says so before
a visitor does.

## What each side is for

| | Python | JavaScript |
|---|---|---|
| Runs in | `pc serve`, the CLI, tests | the browser |
| Reads | `data/bot.db` | `web/data.json` |
| Writes | `messages` (the inbox) | `localStorage` (the transcript) |
| Used for | the full loop: build, test, review | the public site |

The browser side cannot build, test, or maintain anything — those need the
maintenance round, and that is git. What it can do is answer, cite, and refuse,
which is exactly what a visitor needs.

## What differs, honestly

- **Sessions live in `localStorage`, not a table.** There is no server, so a
  visitor's transcript is theirs. The `refs` for "the second one" are recomputed
  from that transcript, so references still work — but the inbox never sees
  those questions.
- **Search has no FTS index.** Python uses SQLite FTS5 with a trigram tokenizer;
  the browser scans the ten exported documents. Same behaviour on this corpus,
  but it will diverge if the document set grows. `parity.mjs` is what would
  catch it.
- **`data.json` is a build artifact** and is gitignored. `pc export` regenerates
  it from the same content the Python build uses, so it cannot drift from what
  `pc test` verified.

## Deploying

Anything that serves files: GitHub Pages, Cloudflare Pages, Netlify, S3, a
`file://` URL. Point it at `web/` after running `pc export`. If the host has CI,
the whole pipeline is three commands:

```bash
python -m personal_chatbots build --offline --cache tests/fixtures
python -m personal_chatbots test
python -m personal_chatbots export
```

The first two are the regression gate; the third is the artifact.
