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
import sys
from pathlib import Path

from . import content as content_mod
from .build import build as build_all
from .config import Config, ConfigError
from .content import ContentError
from .engine import Runtime
from .runner import run_tests
from .store import SCHEMA_VERSION, SchemaTooOld, Store
from .vocabulary import VocabularyError


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def _prepare(args: argparse.Namespace) -> Config:
    return Config.load(args.root)


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
    print(answer.text)
    if not args.quiet:
        cites = ", ".join(c.key for c in answer.citations) or "-"
        print(f"\n  [{answer.source}:{answer.matched_slug or '-'}]  {answer.latency_ms:.2f} ms  · 0 tokens")
        print(f"  cites: {cites}")
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
        print(f"{len(rows)} unanswered question shape(s):\n")
        for row in rows:
            print(f"  x{row['same_shape_count']:<3} {row['content']}")
    print(f"\nkappa {_pct(stats['kappa'])}  ·  {stats['answered']} answered, "
          f"{stats['refused']} refused, {stats['unresolved']} unresolved")
    return 0


def cmd_test(args: argparse.Namespace) -> int:
    cfg = _prepare(args)
    cases = content_mod.load_test_cases(cfg.content_dir / "tests.yaml")
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
                "ladder": cfg.ladder,
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
                {"from": from_key, "type": link_type, "to": to_key}
                for from_key, link_type, to_key in store.all_links()
            ],
            "documents": documents,
            # shipped so the static page can run its own self-check in the browser
            "tests": content_mod.load_test_cases(cfg.content_dir / "tests.yaml"),
        }

        target = Path(args.out) if args.out else (cfg.root / "web" / "data.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    size = target.stat().st_size
    print(f"exported {target.relative_to(cfg.root)}  ({size / 1024:.0f} KB)")
    print(f"  {len(payload['knowledge'])} knowledge rows, {len(payload['entities'])} entities, "
          f"{len(payload['links'])} links, {len(documents)} documents, "
          f"{len(payload['tests'])} cases")
    print("  serve it with any static host: the ladder runs in the browser, 0 tokens")
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
    p.set_defaults(func=cmd_export)

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
    try:
        return int(args.func(args))
    except (ConfigError, ContentError, VocabularyError, SchemaTooOld) as exc:
        return _fail(str(exc))
    except FileNotFoundError as exc:
        return _fail(f"{exc}. Run `pc build` first.")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
