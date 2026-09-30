# Putting the bot on a static site

Short answer: **yes, it is entirely static**, and the compiler you are asking for
already exists — it compiles *data*, not Python.

```
content/*.yaml                     the source of truth        (this repo)
      |
      |  pc export --bundle DIR     <-- the compiler
      v
DIR/data.json                      every row, as JSON
DIR/engine.js                      the ladder, ~500 lines, no dependencies
      |
      |  copied into any static site's public/ directory
      v
the browser                       0 tokens, 0 servers, 0 request-time anything
```

## Why not transpile the Python

Because there is nothing to transpile. The Python runtime is a small interpreter
*over rows*; the rows are data and the interpreter is a few hundred lines of
resolution logic. Two consequences:

- **The data ships as JSON** — `data.json` is the whole knowledge base, not a
  dump that the JS re-derives.
- **The interpreter was written twice on purpose, and the two are checked
  against each other.** `pc export` puts `content/tests.yaml` inside
  `data.json`, `web/parity.mjs` runs those cases against the JS engine, and CI
  fails on any disagreement. That check is the reason a second implementation is
  safe rather than reckless.

Shipping Python to the browser (Pyodide, Brython) would mean ~10 MB of runtime
to execute 400 lines of logic, and it would still need the same data file.

## Why not copy the markdown across

The READMEs are already in `data.json` as `documents` — paired with the entity
key they belong to, which is what the search rung needs. Copying the `.md` files
separately would create a second source of truth, and the pairing would have to
be rebuilt by hand.

One file to copy, one contract, no duplicates.

## Size

| file | raw | gzipped |
|---|---|---|
| `data.json` | ~140 KB | ~30 KB |
| `engine.js` | ~19 KB | ~5 KB |

Static hosts gzip. This is smaller than one photograph.

---

## Integrating into an Astro site

This mirrors the `content:sync` convention already used for articles: a declared
source in `src/site.mjs`, a script that copies, a manifest, and a `verify` for CI.

### 1. Declare the source

In `src/site.mjs`:

```js
/** Where `npm run bot:sync` copies the generated chatbot artifacts from. */
export const BOT_SYNC = {
  source: process.env.BOT_SOURCE || '~/Music/personal-chatbots',
  outDir: 'public/bot',
  manifest: 'src/content/.bot-manifest.json',
}
```

### 2. Copy the artifacts

```bash
npm run bot:sync          # run `pc export --bundle` in the source repo, then copy
npm run bot:sync -- --dry-run
npm run bot:verify        # CI: fail if public/bot is stale
```

The script is small, because the compiling already happened:

```js
// scripts/bot.mjs
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { BOT_SYNC } from '../src/site.mjs'

const ROOT = path.resolve(import.meta.dirname, '..')
const SOURCE = BOT_SYNC.source.replace(/^~/, process.env.HOME)
const OUT = path.join(ROOT, BOT_SYNC.outDir)
const FILES = ['data.json', 'engine.js', 'CONTRACT.md']

// The export runs in the source repo. Nothing is edited there.
execFileSync('python3', ['-m', 'personal_chatbots', 'export', '--bundle', OUT], {
  cwd: SOURCE, stdio: 'inherit',
})

const manifest = {
  source: SOURCE,
  syncedAt: new Date().toISOString(),
  files: Object.fromEntries(FILES.map((f) => [
    f, fs.statSync(path.join(OUT, f)).size,
  ])),
}
fs.writeFileSync(path.join(ROOT, BOT_SYNC.manifest), JSON.stringify(manifest, null, 2) + '\n')
```

`bot:verify` compares the recorded sizes against the files — the same "did
someone hand-edit a generated file" check `content:verify` does.

### 3. Serve them at a fixed path

`public/bot/` lands at `/bot/` on the built site. **Fetch it from the site root,
never relative to the page**:

```astro
---
// src/pages/[lang]/ask.astro
import { BASE_URL } from '../site.mjs'   // or import.meta.env.BASE_URL
---
<div id="ask"></div>
<script>
  import { createBot } from '/bot/engine.js'   // Astro bundles this
  const base = import.meta.env.BASE_URL
  const bot = createBot(await (await fetch(`${base}bot/data.json`)).json())
</script>
```

**This is the one real gotcha.** A page at `/zh/ask/` that fetches
`./data.json` looks for `/zh/ask/data.json` and gets a 404. Use the base URL.

Astro will bundle `/bot/engine.js` from `public/` as an external import; if you
prefer it fully bundled, copy `engine.js` into `src/lib/` instead and import it
relatively — but keep it in `bot:verify`, or it becomes an unmanaged copy that
drifts from the Python engine it is supposed to match.

### 4. Bilingual pages come free

`content/knowledge.yaml` rows carry `locale: ""` meaning *any language*, and the
engine picks the refusal template from the question's own script. So `/ask` and
`/zh/ask` can share one engine and one `data.json`; only the surrounding chrome
differs. That is deliberate — see `docs/CONCERNS.md` C9.

### 5. CI

Add `npm run bot:verify` to the site's checks. It catches the failure that
matters: someone edited an answer in `public/bot/data.json` by hand because it
was quicker than opening a PR in this repo.

---

## Why the static site has no model fallback

`pc serve` can be configured with a fallback rung; the static page cannot, and it
is not an oversight.

A browser-side call needs the API key **in the page**, where anyone can read it
out of the network tab. There is no way to hide it in a static site: no server
means no secret. The three honest options are

1. keep the static page deterministic and let it refuse (`/ask` on the site),
2. keep the fallback behind `pc serve`, which means running a server, or
3. have the visitor bring their own key, stored in their own browser.

This project takes (1) for the public site. It also happens to be the option that
keeps the refusal rate measurable — a static page's refusals land in
`localStorage`, not the inbox, so nothing is hidden either way.

### The key is not the real obstacle, and it is worth knowing why

Option (3) does not work either, and neither does "publish the key, the account
is $0". Measured against the default endpoint,
`POST https://opencode.ai/zen/v1/chat/completions`:

```
OPTIONS (preflight, as a browser sends it)    -> 404, no Access-Control-* headers
POST with Origin: https://plae-tljg.github.io -> 200, no Access-Control-Allow-Origin
```

The request is accepted. The *response* is withheld by the browser, because
nothing in it says the page may read it — and a request carrying `Authorization`
is preflighted first, which 404s. So a page cannot call this endpoint at all,
with or without a key, and no amount of willingness to leak one changes that.

This matters beyond the decision: "we chose not to expose the key" is a judgement
a reader can argue with, and "the browser is not allowed to read the reply" is
not. A model on the static site needs a CORS-enabled endpoint or a proxy, and
then the key lives in the proxy. One `curl` settles it before anyone writes
browser code that cannot work:

```bash
curl -s -D - -o /dev/null -X OPTIONS <endpoint> \
  -H 'Origin: https://example.github.io' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: authorization,content-type'
```

No `access-control-allow-origin` in that output means option (3) is closed.

## What the browser engine deliberately cannot do

It answers, cites, and refuses. It cannot build, test, or maintain anything —
those need the maintenance round, and that is git in *this* repository. Sessions
live in `localStorage`, so a visitor's transcript never reaches the inbox.

That split is the point: the public site is a read-only projection, and the only
thing that can change what it knows is a merged pull request.
