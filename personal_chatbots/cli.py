"""``pc`` — the command line.

    pc build     sync content/*.yaml + GitHub into data/bot.db
    pc ask       one question, showing which rung answered and the citations
    pc inbox     the questions the structure could not answer, clustered
    pc test      run content/tests.yaml
    pc stats     kappa, refusal rate, dead rows
    pc serve     FastAPI + the chat widget
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import content as content_mod
from .build import build as build_all
from .config import Config, ConfigError
from .content import ContentError
from .engine import Runtime
from .runner import run_tests
from .textnorm import tokenize
from .store import SCHEMA_VERSION, SchemaTooOld, Store
from .vocabulary import VocabularyError


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _prepare(args: argparse.Namespace) -> Config:
    # `--db` exists so a released database can be queried without a build:
    #     pc --db dist/bot.db ask "what does dsh-review do?"
    # Every command goes through here, so there is one place that decides which
    # database "the database" means.
    db = getattr(args, "db", None)
    return Config.load(args.root, db_path=Path(db).resolve() if db else None)


# ---------------------------------------------------------------------------


def cmd_build(args: argparse.Namespace) -> int:
    cfg = _prepare(args)
    report = build_all(cfg, offline=args.offline, cache_dir=Path(args.cache) if args.cache else None)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    ent = report["entities"]
    print(f"built {cfg.db_path}")
    print(f"  entities   {ent['created']} created, {ent['updated']} updated, "
          f"{ent['unchanged']} unchanged, {ent['archived']} archived")
    print(f"  knowledge  {report['knowledge']} rows")
    print(f"  links      {report['links']}")
    print(f"  documents  {report['documents']}")
    print(f"  fetched    {report['fetch']['network']} from network, "
          f"{report['fetch']['cache']} from cache")
    print(f"  messages   {report['messages']} (live state, untouched by the build)")
    for note in report["notes"]:
        print(f"  note: {note}")
    if report["dangling_links"]:
        print(f"  dangling links: {len(report['dangling_links'])}")
        for item in report["dangling_links"][:5]:
            print(f"    {item}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    cfg = _prepare(args)
    with Store(cfg.db_path) as store:
        store.require_schema()
        if not store.knowledge_rows():
            return _fail("no knowledge rows -- run `pc build` first")
        runtime = Runtime.from_store(store, cfg)
        answer = runtime.ask_and_record(args.question, session_id=args.session)
        fallback_error = runtime.last_fallback_error
        # Computed against the *original* question, before any reference rewrite,
        # because that is the wording the visitor used and the wording a pattern
        # would have to cover.
        near = runtime.near_misses(tokenize(args.question)) if answer.unresolved else []
    print(answer.text)
    if not args.quiet:
        cites = ", ".join(c.key for c in answer.citations) or "-"
        cost = "0 tokens" if answer.source != "fallback" else "model call"
        print(f"\n  [{answer.source}:{answer.matched_slug or '-'}]  "
              f"{answer.latency_ms:.2f} ms  · {cost}")
        print(f"  cites: {cites}")
        if answer.suggestions:
            print(f"  try:   {'  ·  '.join(answer.suggestions)}")
        for slug, note in near:
            print(f"  near:  {slug} — {note}")
        if near:
            print("         one word apart. That is a content fix, not a model fix.")
        if fallback_error and answer.source == "refuse":
            # Otherwise a broken key is indistinguishable from a model that had
            # nothing to say, which is exactly how this looked for an hour.
            print(f"  note:  the model rung failed — {fallback_error[:120]}")
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    cfg = _prepare(args)
    with Store(cfg.db_path) as store:
        store.require_schema()
        rows = store.unresolved_inbox(args.limit)
        stats = store.stats()
    if not rows:
        print("inbox empty — nothing has been asked that the tables could not answer")
    else:
        # Annotating each shape with its nearest row turns "add a row" into
        # "widen this row", which is the cheaper and usually correct fix.
        from .engine import Runtime
        from .textnorm import tokenize

        with Store(cfg.db_path) as store:
            runtime = Runtime.from_store(store, cfg)
        print(f"{len(rows)} unanswered question shape(s):\n")
        for row in rows:
            print(f"  x{row['same_shape_count']:<3} {row['content']}")
            for slug, note in runtime.near_misses(tokenize(row["content"]), limit=1):
                print(f"       near {slug}: {note}")
    print(f"\nkappa {_pct(stats['kappa'])}  ·  {stats['answered']} answered, "
          f"{stats['refused']} refused, {stats['unresolved']} unresolved")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    cfg = _prepare(args)
    cases = content_mod.load_test_cases(cfg.content_dir / "tests.yaml")
    if "fallback" in cfg.ladder:
        print("note: the frozen cases run without the model rung — they assert what the")
        print("      structure does, so a fluent answer must not be able to pass them\n")
    with Store(cfg.db_path) as store:
        store.require_schema()
        if not store.knowledge_rows():
            return _fail("no knowledge rows -- run `pc build` first")
        outcomes = run_tests(cfg, cases, store)
    failed = [o for o in outcomes if not o.passed]
    for outcome in outcomes:
        mark = "ok  " if outcome.passed else "FAIL"
        print(f"{mark} {outcome.slug}")
        if args.verbose or not outcome.passed:
            print(f"       {outcome.question!r} -> [{outcome.got.get('source')}]")
            for note in outcome.notes:
                print(f"       - {note}")
    print(f"\n{len(outcomes) - len(failed)}/{len(outcomes)} passed")
    return 1 if failed else 0


def cmd_stats(args: argparse.Namespace) -> int:
    cfg = _prepare(args)
    with Store(cfg.db_path) as store:
        store.require_schema()
        stats = store.stats()
        coverage = store.knowledge_coverage()
        dead = store.dead_knowledge()
        mix = store.resolution_mix()

    print(f"{cfg.name}  (level {cfg.level}, ladder {' -> '.join(cfg.ladder)})")
    print(f"  entities      {stats['entities']}")
    print(f"  documents     {stats['documents']}")
    print(f"  knowledge     {stats['knowledge']} live, {stats['knowledge_archived']} archived")
    print(f"  messages      {stats['messages']}")
    print(f"  kappa         {_pct(stats['kappa'])}  "
          f"({stats['answered']} answered, {stats['refused']} refused)")
    print(f"  unresolved    {stats['unresolved']}")
    if mix:
        print("  answered by")
        for row in mix:
            print(f"    {row['resolution_source']:<10} {row['turns']:>4} turns  "
                  f"avg {row['avg_latency_ms']:.2f} ms")
    if coverage:
        print("  knowledge rows never matched")
        for row in coverage:
            if not row["hits"]:
                print(f"    {row['slug']}")
    if dead:
        print(f"  dead rows (live, never matched): {len(dead)}")
    print("  cost          $0.00 — no model runs at request time")
    return 0


def cmd_sql(args: argparse.Namespace) -> int:
    """Run any read-only query against the database.

    The maintainer is not limited to the commands this file happens to offer.
    `pc entities` covers the common questions, and `pc sql` covers everything
    else -- joins, counts, whatever the agent needs to understand the data before
    proposing a change.

    Opened with `mode=ro`, so this is not a policy the agent is asked to respect:
    SQLite itself rejects an INSERT, an UPDATE or a DROP on this connection with
    "attempt to write a readonly database". Reading is unlimited; writing is
    impossible. That is the distinction the permission config originally got
    wrong when it denied `sqlite3` outright.
    """
    import sqlite3

    cfg = _prepare(args)
    if not cfg.db_path.exists():
        return _fail(f"{cfg.db_path} does not exist -- run `pc build`")

    query = (args.query or "").strip()
    connection = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if not query or query.lower() in ("tables", ".tables"):
            rows = connection.execute(
                "SELECT type, name FROM sqlite_master"
                " WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'"
                " AND name NOT LIKE '%_fts_%' ORDER BY type, name"
            ).fetchall()
            print("tables and views (query them with `pc sql \"...\"`):\n")
            for row in rows:
                count = ""
                if row["type"] == "table":
                    n = connection.execute(f"SELECT COUNT(*) AS n FROM {row['name']}").fetchone()["n"]
                    count = f"{n:>6} rows"
                print(f"  {row['type']:<6} {row['name']:<24} {count}")
            print("\n  entities / knowledge / documents / links are the synced data;")
            print("  messages is live and changes as people ask things.")
            return 0

        cursor = connection.execute(query)
        rows = cursor.fetchmany(args.limit)
        if not rows:
            print("(no rows)")
            return 0
        columns = list(rows[0].keys())
        if args.json:
            print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=1))
            return 0

        widths = [len(c) for c in columns]
        body = []
        for row in rows:
            cells = ["" if row[c] is None else str(row[c]) for c in columns]
            cells = [c if len(c) <= 60 else c[:57] + "..." for c in cells]
            body.append(cells)
            widths = [max(w, len(c)) for w, c in zip(widths, cells)]
        print("  " + "  ".join(c.ljust(w) for c, w in zip(columns, widths)))
        print("  " + "  ".join("-" * w for w in widths))
        for cells in body:
            print("  " + "  ".join(c.ljust(w) for c, w in zip(cells, widths)))
        if len(rows) == args.limit:
            print(f"\n  (stopped at {args.limit} rows; raise with --limit)")
        return 0
    except sqlite3.OperationalError as exc:
        return _fail(f"{exc}\n  (this connection is read-only: SELECT only. Run `pc sql` with no"
                     f" query to list the tables.)")
    finally:
        connection.close()


def cmd_doctor(args: argparse.Namespace) -> int:
    """Why is (or is not) the bot answering? One command, no guessing.

    Written because the answer was not obvious even to the person who built it:
    the fallback rung is absent from the ladder by default, so "the AI is not
    answering" is a *config* fact, not a bug, and nothing said so.
    """
    import os

    cfg = _prepare(args)
    fb = cfg.fallback
    ladder = cfg.ladder

    print(f"{cfg.name} — health check\n")

    print("runtime")
    print(f"  ladder           {' -> '.join(ladder)}")
    if "fallback" not in ladder:
        print("  model fallback   OFF — 'fallback' is not in runtime.ladder")
        print(f"                   so unknown questions get: {cfg.refuse_template!r}")
        print("                   to enable it, add \"fallback\" to runtime.ladder in")
        print("                   content/bot.json and set fallback.enabled (docs/CONCERNS.md C1)")
    else:
        endpoint = str(fb.get("endpoint", ""))
        model = str(fb.get("model", ""))
        key_env = str(fb.get("api_key_env", ""))
        key = os.environ.get(key_env, "")
        print(f"  model fallback   {'ENABLED' if fb.get('enabled') else 'IN THE LADDER but fallback.enabled is false'}")
        print(f"    endpoint       {endpoint or '(unset)'}")
        print(f"    model          {model or '(unset)'}")
        print(f"    api key        {key_env} — {'set (' + str(len(key)) + ' chars)' if key else 'NOT SET'}")
        if fb.get("enabled") and not key:
            print("                   ^ every fallback call will be skipped and fall through")
            print("                     to refusal, silently. export the key and restart.")
        if fb.get("enabled") and not endpoint:
            print("                   ^ no endpoint configured; same silent skip")

    if args.ping and "fallback" in ladder and fb.get("enabled"):
        import time

        from .engine import Runtime

        with Store(cfg.db_path) as store:
            store.require_schema()
            runtime = Runtime.from_store(store, cfg)
        started = time.perf_counter()
        answer = runtime.rung_fallback(__import__("personal_chatbots.textnorm", fromlist=["x"]).tokenize("ping"))
        elapsed = (time.perf_counter() - started) * 1000
        if answer is None:
            print("\nping             FAILED")
            if runtime.last_fallback_error:
                print(f"                 {runtime.last_fallback_error[:150]}")
            else:
                print("                 no answer and no error — check the api key line above")
        else:
            print(f"\nping             ok in {elapsed:.0f} ms")
            print(f"                 {answer.text.splitlines()[0][:70]}")

    with Store(cfg.db_path) as store:
        store.require_schema()
        stats = store.stats()
        inbox = store.unresolved_inbox(args.limit)
        coverage = store.knowledge_coverage()

    print("\nknowledge")
    print(f"  entities         {stats['entities']} live")
    print(f"  knowledge rows   {stats['knowledge']} live, {stats['knowledge_archived']} archived")
    never = [row["slug"] for row in coverage if not row["hits"]]
    if never:
        print(f"  never matched    {len(never)}: {', '.join(never[:6])}"
              + (" …" if len(never) > 6 else ""))

    with Store(cfg.db_path) as store:
        documents, repo_count = store.document_coverage()
    print("\ndocuments")
    print(f"  readmes          {documents} of {repo_count} repositories")
    if documents < repo_count:
        print(f"                   the other {repo_count - documents} have no searchable text,")
        print("                   so questions needing their README will refuse.")
        print("                   set GITHUB_TOKEN and rebuild to fetch all of them")

    print("\ntraffic")
    print(f"  messages         {stats['messages']}")
    kappa = "n/a" if stats["kappa"] is None else f"{stats['kappa'] * 100:.0f}%"
    print(f"  kappa            {kappa}  ({stats['answered']} answered, {stats['refused']} refused)")
    with Store(cfg.db_path) as store:
        mix = {row["resolution_source"]: row["turns"] for row in store.resolution_mix()}
    total_turns = sum(mix.values()) or 1
    model_calls = mix.get("fallback", 0)
    print(f"  model calls      {model_calls} of {total_turns} turns"
          f"  ({model_calls / total_turns * 100:.0f}% went to the model)")
    if model_calls and model_calls / total_turns > 0.2:
        print("                   that is high. each one is ~2-7 s and a token spend;")
        print("                   the inbox below is what would remove them")
    print(f"  inbox            {len(inbox)} unanswered shape(s)")
    for row in inbox[:5]:
        print(f"    x{row['same_shape_count']:<3} {row['content'][:60]}")

    print("\ncontent")
    cases = content_mod.load_test_cases(cfg.content_dir / "tests.yaml")
    print(f"  frozen cases     {len(cases)}")
    if kappa != "n/a" and stats["kappa"] < 0.5 and stats["messages"] > 20:
        print("  note             over half the traffic is being refused. The inbox above")
        print("                   is the to-do list: run a maintenance round.")
    return 0


def cmd_entities(args: argparse.Namespace) -> int:
    """Look at the vocabulary without opening the database.

    Without this, an agent asked to check whether a slot resolves has to either
    guess or reach for sqlite3 -- and sqlite3 is not on its allow-list precisely
    because the build artifact is not the thing anyone should be writing to.
    """
    cfg = _prepare(args)
    with Store(cfg.db_path) as store:
        store.require_schema()
        if args.summary:
            print("live entities by type:\n")
            for row in store.entity_summary():
                archived = row["archived"] or 0
                extra = f"  ({archived} archived)" if archived else ""
                print(f"  {row['entity_type']:<10} {row['live']:>4}{extra}")
            print("\n  pc entities <type> [needle]   list them")
            return 0

        entity_type = args.entity_type or ""
        found = store.find_entities(args.needle or "", entity_type, args.limit)
        if not found:
            print(f"  no live entity matches type={entity_type or '*'} needle={args.needle or '*'!r}")
            print("  check the spelling with: pc entities --summary")
            return 1
        for entity in found:
            metrics = ""
            if entity.attrs:
                interesting = [
                    f"{k}={v}" for k, v in entity.attrs.items()
                    if k in ("stars", "language", "price", "stock", "pushed_at")
                ]
                metrics = "  " + " ".join(interesting) if interesting else ""
            print(f"  {entity.entity_type:<9} {entity.key:<44} {entity.name}{metrics}")
            if args.verbose and entity.aliases:
                print(f"            aliases: {', '.join(entity.aliases)}")
    return 0


def cmd_knowledge(args: argparse.Namespace) -> int:
    """The rows the bot answers from, with their patterns and hit counts."""
    cfg = _prepare(args)
    with Store(cfg.db_path) as store:
        store.require_schema()
        hits = {row["slug"]: row["hits"] for row in store.knowledge_coverage()}
        for row in store.knowledge_rows():
            if args.grep and args.grep.lower() not in row.slug.lower() \
                    and not any(args.grep.lower() in p.lower() for p in row.patterns):
                continue
            matched = hits.get(row.slug) or 0
            print(f"  {row.slug:<20} [{row.action.get('kind', '?'):<7}] {matched:>3} hits")
            for pattern in row.patterns:
                print(f"      {pattern}")
            slots = ", ".join(f"{{{k}}}:{v}" for k, v in row.slots.items()) or "-"
            print(f"      slots: {slots}")
        print("\n  'resolves' needs a live entity of that type -- check with `pc entities`")
    return 0


#: Rungs the browser engine implements. `fallback` is excluded on purpose: a
#: browser-side call would put the API key in the page, so the static site cannot
#: have one (docs/STATIC_SITE.md). `composite` is L3 and not built anywhere yet.
BROWSER_RUNGS = ("knowledge", "entity", "search", "refuse")


#: Written next to the exported artifacts so the consuming site has the rules in
#: the same directory as the files they apply to.
CONTRACT = """# bot/ — generated artifacts

Generated by `personal-chatbots` at {generated}. **Do not edit anything here.**

    data.json    every row the bot answers from: entities, links, documents,
                 knowledge, and the frozen test cases ({rows} knowledge rows,
                 {entities} entities for {name}).
    engine.js    the ladder. A small interpreter over data.json — no network,
                 no model, no build step, no dependencies.

## Using it

Serve both files from your site and load the engine as a module. **Resolve
data.json against the site root, not relative to the page** — a page at
`/zh/ask/` fetching `./data.json` would look for `/zh/ask/data.json`:

```js
import {{ createBot }} from '/bot/engine.js'

const bot = createBot(await (await fetch('/bot/data.json')).json())
const answer = bot.ask('what is dsh-review about?')
// {{ text, source, citations, slots, refs, latency_ms, tokens }}

// pass the session so far to resolve "the second one" / "it":
const followUp = bot.ask('and the third?', previousTurns)

// the frozen cases ship with the data, so the page can check itself:
bot.selfCheck()   // 16 cases, the same ones `pc test` runs
```

In Astro, use `import.meta.env.BASE_URL` instead of a hard-coded `/` so a
project-page base path keeps working.

## Why copying is safe

These two files are the *compiled* form of `content/*.yaml`, the same way a
static site's HTML is the compiled form of its markdown. There is exactly one
source of truth and it is not here.

Two things keep it honest:

* `pc export` regenerates both files from the content the Python runtime tests;
* the browser engine runs the same `content/tests.yaml`, and
  `node web/parity.mjs` fails if the two engines ever disagree. CI runs it.

So the consuming site never edits these files and never needs Python. Refresh
them with:

    python -m personal_chatbots export --bundle /path/to/site/public/bot

## Sessions

`engine.js` does not store conversations. That is the host's job: `ask()` takes
the previous turns as its second argument, which is what reference resolution
("the second one") reads. `localStorage` is fine; a server is not required.
"""


def cmd_export(args: argparse.Namespace) -> int:
    """Write a static snapshot the browser engine can answer from.

    This is what makes "mostly static webpage" work: the ladder is a small
    interpreter over rows, so the rows can ship as JSON and the whole bot runs
    client-side with no server, no request-time model, and no hosting cost. The
    only thing that needs a machine is the maintenance round, and that is git.
    """
    import datetime
    import json as _json

    cfg = _prepare(args)
    with Store(cfg.db_path) as store:
        store.require_schema()
        documents = [
            {
                "slug": doc.slug,
                "title": doc.title,
                "body": doc.body,
                "entity_key": entity_key,
            }
            for doc, entity_key in store.all_documents()
        ]
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "bot": {
                "name": cfg.name,
                "tagline": cfg.data.get("tagline", ""),
                "default_locale": cfg.default_locale,
                "refuse_template": cfg.refuse_template,
                "refuse_template_zh": cfg.data.get("refuse_template_zh", ""),
                # The ladder this target can actually run. The browser has no
                # API key and must not have one, so `fallback` is compiled out
                # rather than shipped and failed on -- and what was dropped is
                # recorded so the page can say so instead of quietly differing
                # from `pc serve`.
                "ladder": [r for r in cfg.ladder if r in BROWSER_RUNGS],
                "ladder_dropped": [r for r in cfg.ladder if r not in BROWSER_RUNGS],
                "level": cfg.level,
                "max_citations": cfg.max_citations,
            },
            # already in match order (most specific first), so the JS engine does
            # not have to re-derive the ordering rule and cannot drift from it
            "knowledge": [
                {
                    "slug": row.slug,
                    "locale": row.locale,
                    "patterns": row.patterns,
                    "slots": row.slots,
                    "match": row.match,
                    "action": row.action,
                    "citations": row.citations,
                }
                for row in store.knowledge_rows()
            ],
            "entities": [
                {
                    # A key is unique only within a type: `plae-tljg` is both an
                    # account and the person who owns it. Links must reference
                    # something stable and unambiguous, so they carry ids.
                    "id": e.id,
                    "key": e.key,
                    "type": e.entity_type,
                    "name": e.name,
                    "summary": e.summary,
                    "aliases": e.aliases,
                    "attrs": e.attrs,
                    "url": e.url,
                }
                for e in store.entities()
            ],
            "links": [
                {"from": from_id, "type": link_type, "to": to_id}
                for from_id, link_type, to_id in store.all_links()
            ],
            "documents": documents,
            # shipped so the static page can run its own self-check in the browser
            "tests": content_mod.load_test_cases(cfg.content_dir / "tests.yaml"),
        }

        target = Path(args.out) if args.out else (cfg.root / "web" / "data.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

        if args.bundle:
            # For a site in another repository: one directory to copy, and a
            # contract that says what the files are and what may not be edited.
            bundle = Path(args.bundle)
            bundle.mkdir(parents=True, exist_ok=True)
            (bundle / "data.json").write_text(
                _json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            shutil.copyfile(cfg.root / "web" / "engine.js", bundle / "engine.js")
            (bundle / "CONTRACT.md").write_text(CONTRACT.format(
                name=cfg.name,
                generated=payload["generated_at"],
                rows=len(payload["knowledge"]),
                entities=len(payload["entities"]),
            ), encoding="utf-8")

    size = target.stat().st_size
    print(f"exported {target.relative_to(cfg.root)}  ({size / 1024:.0f} KB)")
    if args.bundle:
        print(f"  bundled to {args.bundle}/  (data.json + engine.js + CONTRACT.md)")
    print(f"  {len(payload['knowledge'])} knowledge rows, {len(payload['entities'])} entities, "
          f"{len(payload['links'])} links, {len(documents)} documents, "
          f"{len(payload['tests'])} cases")
    print("  serve it with any static host: the ladder runs in the browser, 0 tokens")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    """Write a database that is safe to publish.

    The built database is two different things in one file: rows synced from
    ``content/`` and public GitHub metadata, and ``messages``, which is what
    visitors actually asked. The first is publishable and the second is other
    people's questions. They are separated here by dropping the live tables
    rather than by remembering not to share the file.

    Schema stays identical, so a released file is a working database: point
    ``pc --db dist/bot.db`` at it and every read command works, with no build
    and no network.
    """
    import hashlib
    import sqlite3

    cfg = _prepare(args)
    source = Path(cfg.db_path)
    if not source.exists():
        return _fail(f"{source} does not exist. Run `pc build` first.")

    # Check the source before anything is copied. Writing the destination first
    # and validating afterwards puts a file containing `messages` on disk even
    # on the refusal path -- the release directory would briefly hold exactly
    # what a release exists to exclude.
    with Store(source) as origin:
        origin.require_schema()
        rows = {
            "knowledge": origin.conn.execute("SELECT COUNT(*) AS n FROM knowledge").fetchone()["n"],
            "entities": origin.conn.execute("SELECT COUNT(*) AS n FROM entities").fetchone()["n"],
            "links": origin.conn.execute("SELECT COUNT(*) AS n FROM entity_links").fetchone()["n"],
            "documents": origin.conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"],
        }

    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # A file copy, not a re-export: the released database must be the same rows
    # the tests passed against, byte for byte where it can be.
    shutil.copyfile(source, dest)

    live = ("messages", "flow_states", "tasks")  # the last two arrive at L2
    dropped: list[str] = []
    con = sqlite3.connect(dest)
    try:
        for table in live:
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not exists:
                continue
            # Counted before the drop, because the number is the point: it is
            # how you know the release carries no conversations.
            held = int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            con.execute(f"DELETE FROM {table}")
            dropped.append(f"{table} ({held} rows)")
        # VACUUM cannot run inside a transaction, and the DELETEs opened one.
        con.commit()
        con.execute("VACUUM")  # the pages those rows occupied go back to the OS
    finally:
        con.close()

    # Verify the artifact the way a consumer will use it, not the way it was made.
    with Store(dest) as check:
        check.require_schema()
        leftover = check.conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
    if leftover:
        return _fail(f"{dest} still holds {leftover} messages -- refusing to call this a release")

    digest = hashlib.sha256(dest.read_bytes()).hexdigest()
    size = dest.stat().st_size

    if args.manifest:
        manifest = Path(args.manifest)
    else:
        manifest = dest.with_suffix(dest.suffix + ".manifest.json")
    manifest.write_text(
        json.dumps(
            {
                "name": cfg.name,
                "schema_version": SCHEMA_VERSION,
                "level": cfg.level,
                "ladder": cfg.ladder,
                "rows": rows,
                "live_tables_emptied": dropped,
                "sha256": digest,
                "bytes": size,
                "contains": "public GitHub metadata and the content/ files of the repository",
                "does_not_contain": "messages, sessions, or any visitor's questions",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"released {dest}  ({size / 1024:.0f} KB)")
    print(f"  knowledge  {rows['knowledge']} rows")
    print(f"  entities   {rows['entities']}")
    print(f"  links      {rows['links']}")
    print(f"  documents  {rows['documents']}")
    print(f"  emptied    {', '.join(dropped) if dropped else 'nothing (no live tables yet)'}")
    print(f"  sha256     {digest[:16]}…")
    print(f"  manifest   {manifest}")
    print(f"  use it     pc --db {dest} ask \"what does dsh-review do?\"")
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    cfg = _prepare(args)
    with Store(cfg.db_path) as store:
        store.require_schema()
        if args.session_id:
            messages = store.session_messages(args.session_id)
            if not messages:
                return _fail(f"no such session: {args.session_id}")
            meta = store.session(args.session_id)
            print(f"{meta['title']}\n{meta['turns']} turns · {meta['unresolved']} unresolved"
                  f" · started {meta['started_at']}\n")
            for row in messages:
                if row["role"] == "user":
                    mark = "  (unresolved)" if row["unresolved"] else ""
                    print(f"  t{row['turn']} › {row['content']}{mark}")
                else:
                    body = row["content"].replace("\n", "\n      ")
                    print(f"      {body}")
                    refs = json.loads(row["refs_json"] or "{}")
                    if refs:
                        for phrase, key in refs.items():
                            print(f"      read {phrase!r} as {key}")
                    print(f"      [{row['resolution_source']}"
                          f"{':' + row['matched_slug'] if row['matched_slug'] else ''}]"
                          f"  {row['latency_ms']:.2f} ms")
            return 0

        rows = store.list_sessions(args.limit)
        if not rows:
            print("no sessions yet — nothing has been asked")
            return 0
        print(f"{len(rows)} session(s), most recent first:\n")
        for row in rows:
            flag = f"  {row['unresolved']} unresolved" if row["unresolved"] else ""
            print(f"  {row['turns']:>3} turns{flag:<15} {row['last_at']}  {row['session_id'][:8]}")
            print(f"      {(row['title'] or '(empty)')[:72]}")
        print("\n  pc sessions <session_id>   show a full transcript")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    cfg = _prepare(args)
    try:
        import uvicorn
    except ImportError:
        return _fail("serving needs fastapi + uvicorn: pip install fastapi uvicorn")
    from .serve import create_app

    app = create_app(cfg)
    print(f"{cfg.name} on http://{args.host}:{args.port}  (ctrl-c to stop)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_tasks(args: argparse.Namespace) -> int:
    cfg = _prepare(args)
    print(
        f"{cfg.name} is at level {cfg.level} (Facts). Tasks arrive at level 2.\n"
        "See docs/LEVELS.md: a Task is what leaves the conversation and waits for\n"
        "a human. Raising the level is a decision, not a build flag."
    )
    return 0


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pc", description="personal-chatbots")
    parser.add_argument("--root", default=".", help="repo root (default: .)")
    parser.add_argument("--db", default=None, help="database to read (default: <root>/data/bot.db)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="sync content + GitHub into the database")
    p.add_argument("--offline", action="store_true", help="use only cached API responses")
    p.add_argument("--cache", default=None, help="cache directory (default: data/cache)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("ask", help="ask one question")
    p.add_argument("question")
    p.add_argument("--session", default=None)
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_ask)

    p = sub.add_parser("inbox", help="questions the tables could not answer")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_inbox)

    p = sub.add_parser("test", help="run content/tests.yaml")
    p.add_argument("--verbose", "-v", action="store_true")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("stats", help="kappa, refusal rate, dead rows")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("export", help="write web/data.json for the static browser engine")
    p.add_argument("--out", default=None, help="target path (default: web/data.json)")
    p.add_argument("--bundle", default=None,
                   help="also write a copyable directory: data.json + engine.js + CONTRACT.md")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("release", help="write a publishable database (live tables emptied)")
    p.add_argument("out", nargs="?", default="dist/bot.db", help="default dist/bot.db")
    p.add_argument("--manifest", default=None, help="default <out>.manifest.json")
    p.set_defaults(func=cmd_release)

    p = sub.add_parser("sql", help="any read-only query against the database")
    p.add_argument("query", nargs="?", default="", help="a SELECT; omit to list the tables")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_sql)

    p = sub.add_parser("doctor", help="why is (or is not) the bot answering?")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--ping", action="store_true", help="make one real call to the fallback endpoint")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("entities", help="inspect the vocabulary (no database access needed)")
    p.add_argument("entity_type", nargs="?", default="", help="repo, account, language, topic, project, person")
    p.add_argument("needle", nargs="?", default="", help="substring to search names, aliases and attrs")
    p.add_argument("--summary", "-s", action="store_true", help="counts per type")
    p.add_argument("--limit", type=int, default=200, help="default 200: enough to see a whole type")
    p.add_argument("--verbose", "-v", action="store_true", help="show aliases")
    p.set_defaults(func=cmd_entities)

    p = sub.add_parser("knowledge", help="the rows the bot answers from")
    p.add_argument("--grep", default="")
    p.set_defaults(func=cmd_knowledge)

    p = sub.add_parser("sessions", help="list chat sessions, or show one transcript")
    p.add_argument("session_id", nargs="?", default=None)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_sessions)

    p = sub.add_parser("serve", help="run the web widget")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("tasks", help="level 2 placeholder")
    p.set_defaults(func=cmd_tasks)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Before anything reads a key. Silent when there is no .env, and never
    # overrides a value the shell already set.
    from .env import load_dotenv

    load_dotenv(Path(args.root).resolve())
    try:
        return int(args.func(args))
    except (ConfigError, ContentError, VocabularyError, SchemaTooOld) as exc:
        return _fail(str(exc))
    except FileNotFoundError as exc:
        return _fail(f"{exc}. Run `pc build` first.")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
