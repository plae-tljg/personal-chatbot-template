"""``pc build``: the sync from offline content to the online database.

Four sources merge into one desired state, then get applied idempotently:

    GitHub API      -> entities, links, documents
    curation.yaml   -> entities, links, overrides
    knowledge.yaml  -> knowledge rows
    (never)         -> messages        <- live state, untouched by a build

The build is safe to run at any time, including during a production day, because
it only ever writes the synced tables and it never deletes: a row that disappears
upstream or from the YAML becomes ``status='archived'``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from . import content as content_mod
from . import ingest as ingest_mod
from . import vocabulary as vocab
from .textnorm import aliases_for
from .config import Config
from .store import BuildStats, Store

SYNCED_ORIGIN_PREFIX = "github:"


def _apply_overrides(
    specs: dict[tuple[str, str], ingest_mod.EntitySpec],
    overrides: list[dict[str, Any]],
) -> list[str]:
    """Merge curation's judgement onto ingested facts, in place."""
    notes: list[str] = []
    for patch in overrides:
        key = patch["key"]
        wanted = patch.get("type") or ""
        spec = next(
            (
                candidate
                for candidate in specs.values()
                if candidate.key == key and (not wanted or candidate.entity_type == wanted)
            ),
            None,
        )
        if spec is None:
            label = f"{wanted}:{key}" if wanted else key
            notes.append(f"override for {label!r} matched no ingested entity")
            continue
        if patch.get("summary"):
            spec.summary = str(patch["summary"])
        for alias in patch.get("add_aliases", []):
            if alias not in spec.aliases:
                spec.aliases.append(alias)
        for attr, value in patch.get("set", {}).items():
            spec.attrs[attr] = value
        for link_type, to_key in patch.get("add_links", []):
            if (link_type, to_key) not in spec.links:
                spec.links.append((link_type, to_key))
    return notes


def build(
    cfg: Config,
    *,
    offline: bool = False,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    store = Store(cfg.db_path)
    try:
        store.ensure_schema(cfg.schema_path.read_text(encoding="utf-8"))
        state = content_mod.load_all(cfg)

        # ---- 1. gather ------------------------------------------------------
        pulled = ingest_mod.ingest_sources(cfg, offline=offline, cache_dir=cache_dir)
        notes = list(pulled.notes)

        specs: dict[tuple[str, str], ingest_mod.EntitySpec] = {
            (spec.entity_type, spec.key): spec for spec in pulled.entities
        }
        notes.extend(_apply_overrides(specs, state.overrides))

        for curated in state.entities:
            spec = ingest_mod.EntitySpec(
                entity_type=curated.entity_type,
                key=curated.key,
                name=curated.name,
                summary=curated.summary,
                aliases=aliases_for(curated.name, curated.key, extra=curated.aliases),
                attrs=dict(curated.attrs),
                url=curated.url,
                origin="curation",
                source_ref="content/curation.yaml",
            )
            existing = specs.get((spec.entity_type, spec.key))
            if existing:
                # Curation wins on wording; ingest keeps the facts.
                existing.summary = spec.summary or existing.summary
                existing.name = spec.name or existing.name
                existing.url = spec.url or existing.url
                for alias in spec.aliases:
                    if alias not in existing.aliases:
                        existing.aliases.append(alias)
                existing.attrs.update(spec.attrs)
            else:
                specs[(spec.entity_type, spec.key)] = spec

        # ---- 2. entities ----------------------------------------------------
        stats = BuildStats()
        for spec in specs.values():
            outcome = store.upsert_entity(
                entity_type=spec.entity_type,
                key=spec.key,
                name=spec.name,
                summary=spec.summary,
                aliases=spec.aliases,
                attrs=spec.attrs,
                url=spec.url,
                origin=spec.origin,
                source_ref=spec.source_ref,
            )
            setattr(stats, outcome, getattr(stats, outcome) + 1)

        # Curated links are attached after the merge so they point at the final rows.
        curated_links: list[tuple[str, str, str]] = []
        for curated in state.entities:
            for link_type, to_key in curated.links:
                curated_links.append((curated.key, link_type, to_key))

        # ---- 3. links -------------------------------------------------------
        # A key is unique only within an entity type, so the source resolves as
        # (entity_type, key) and the target resolves type-aware -- see
        # vocabulary.LINK_TARGETS for why that map has to exist.
        ids: dict[tuple[str, str], int] = {}
        by_key: dict[str, list[tuple[str, int]]] = {}
        for spec in specs.values():
            found = store.entity_id(spec.entity_type, spec.key)
            if found is None:
                continue
            ids[(spec.entity_type, spec.key)] = found
            by_key.setdefault(spec.key, []).append((spec.entity_type, found))

        def pick_target(to_key: str, link_type: str, source_type: str) -> int | None:
            candidates = by_key.get(to_key, [])
            for wanted in vocab.LINK_TARGETS.get(link_type, ()):
                for entity_type, entity_id in candidates:
                    if entity_type == wanted and entity_type != source_type:
                        return entity_id
            others = {i for t, i in candidates if t != source_type}
            return others.pop() if len(others) == 1 else None

        pairs: list[tuple[str, str, str, str]] = [
            (spec.entity_type, spec.key, link_type, to_key)
            for spec in specs.values()
            for link_type, to_key in spec.links
        ] + [("", from_key, link_type, to_key) for from_key, link_type, to_key in curated_links]

        links: list[tuple[int, str, int]] = []
        dangling: list[str] = []
        for from_type, from_key, link_type, to_key in pairs:
            if from_type:
                source_id = ids.get((from_type, from_key))
            else:
                # Curated links name their source by key alone; a unique match is fine.
                matches = {i for _, i in by_key.get(from_key, [])}
                source_id = matches.pop() if len(matches) == 1 else None
            resolved = pick_target(to_key, link_type, from_type) if to_key else None
            if source_id is None or resolved is None or source_id == resolved:
                if to_key:
                    dangling.append(f"{from_key} -{link_type}-> {to_key}")
                continue
            links.append((source_id, link_type, resolved))
        store.replace_links(links)

        # ---- 4. documents ---------------------------------------------------
        for entity_key, title, body in pulled.readmes:
            entity_id = ids.get(("repo", entity_key))
            store.upsert_document(
                slug=f"readme:{entity_key}",
                title=title,
                body=body,
                entity_id=entity_id,
                source_ref=f"https://github.com/{entity_key}",
            )

        # ---- 5. knowledge ---------------------------------------------------
        for row in state.knowledge:
            outcome = store.upsert_knowledge(
                slug=row["slug"],
                patterns=row["patterns"],
                slots=row["slots"],
                match=row["match"],
                action=row["action"],
                citations=row["citations"],
                locale=row["locale"],
            )
            setattr(stats, outcome, getattr(stats, outcome) + 1)

        stats.archived = store.archive_missing_knowledge(r["slug"] for r in state.knowledge)
        ingest_keys = {spec.key for spec in pulled.entities}
        stats.archived += store.archive_missing_entities(SYNCED_ORIGIN_PREFIX, ingest_keys)

        store.conn.commit()
        return {
            "entities": {k: v for k, v in stats.as_dict().items() if k != "notes"},
            "knowledge": len(state.knowledge),
            "documents": len(pulled.readmes),
            "links": len(links),
            "dangling_links": sorted(set(dangling)),
            "fetch": {"network": pulled.fetched, "cache": pulled.from_cache},
            "notes": notes,
            "messages": store.count_messages(),
        }
    finally:
        store.close()
