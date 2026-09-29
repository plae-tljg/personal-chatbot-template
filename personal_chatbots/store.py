"""All SQL lives here. This is the ``Store`` seam.

Two rules from the design that this module enforces:

1. **Synced tables are stateless.** `knowledge`, `entities`, `entity_links` and
   `documents` are written by `pc build` and read by the runtime. Nothing that
   must survive a rebuild is stored there -- no counters, no session flags.
2. **Nothing is deleted by a sync.** A row that disappears upstream becomes
   ``status='archived'``. `messages.matched_slug` history still points at it, and
   a visitor's past answer must stay explicable.

The only table the runtime writes is ``messages``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import Scope

SYNCED_TABLES = ("entities", "entity_links", "documents", "knowledge")
LIVE_TABLES = ("messages",)

#: Bumped whenever db/schema.sql changes shape. `pc build` stamps it.
#:
#: Without this, a database built by an older version fails deep inside a query
#: with "no such table" -- a 500 from the server and an opaque traceback from the
#: CLI. Both of those send you looking in the wrong place; the actual fix is one
#: command, and the error should say so.
SCHEMA_VERSION = 3


class SchemaTooOld(RuntimeError):
    """The database was built by a different schema. Run `pc build`."""


@dataclass
class Entity:
    id: int
    entity_type: str
    key: str
    name: str
    summary: str
    aliases: list[str]
    attrs: dict[str, Any]
    url: str
    status: str = "live"
    origin: str = ""

    @property
    def short(self) -> str:
        return self.key.split("/", 1)[-1]


@dataclass
class Citation:
    key: str
    label: str
    url: str

    def matches(self, needle: str) -> bool:
        needle = needle.strip().lower()
        if not needle:
            return False
        return needle in self.key.lower() or needle in self.url.lower() or needle in self.label.lower()

    def to_json(self) -> dict[str, str]:
        return {"key": self.key, "label": self.label, "url": self.url}


@dataclass
class KnowledgeRow:
    id: int
    slug: str
    locale: str
    patterns: list[str]
    slots: dict[str, str]
    match: dict[str, Any]
    action: dict[str, Any]
    citations: list[str]
    status: str = "live"


@dataclass
class Document:
    id: int
    slug: str
    title: str
    body: str
    entity_id: int | None


@dataclass
class BuildStats:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    archived: int = 0
    notes: list[str] = field(default_factory=list)

    def merge(self, other: "BuildStats") -> None:
        self.created += other.created
        self.updated += other.updated
        self.unchanged += other.unchanged
        self.archived += other.archived
        self.notes.extend(other.notes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "archived": self.archived,
            "notes": self.notes,
        }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Store:
    def __init__(self, db_path: str | Path, scope: Scope | None = None):
        self.path = Path(db_path)
        self.scope = scope or Scope()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False because the serving path is not single-threaded:
        # FastAPI runs sync endpoints in a worker pool, so a cached Store is used
        # from whichever thread picks up the request. Without this the second
        # request from a different worker raises
        # "SQLite objects created in a thread can only be used in that same
        # thread" -- intermittently, which is the worst way to find out.
        #
        # Concurrent reads on one connection are serialized by SQLite itself;
        # writes are serialized by _write_lock below.
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        # WAL lets readers proceed while a write is in flight; busy_timeout turns
        # a lock collision into a short wait instead of an immediate error.
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._write_lock = threading.RLock()

    # -- lifecycle ------------------------------------------------------------

    def ensure_schema(self, schema_sql: str) -> None:
        self.conn.executescript(schema_sql)
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    def schema_version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def require_schema(self) -> None:
        """Fail with the fix, not with a traceback about a missing view."""
        found = self.schema_version()
        if found != SCHEMA_VERSION:
            raise SchemaTooOld(
                f"{self.path} was built with schema version {found}, "
                f"this code expects {SCHEMA_VERSION}. Run `pc build`."
            )

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # =========================================================================
    # sync (written by pc build, read by the runtime)
    # =========================================================================

    def upsert_entity(
        self,
        *,
        entity_type: str,
        key: str,
        name: str,
        summary: str = "",
        aliases: Sequence[str] = (),
        attrs: dict[str, Any] | None = None,
        url: str = "",
        origin: str = "",
        source_ref: str = "",
    ) -> str:
        """Insert or update one entity. Returns 'created' | 'updated' | 'unchanged'."""
        attrs = attrs or {}
        row = self.conn.execute(
            "SELECT id, name, summary, aliases_json, attrs_json, url FROM entities "
            "WHERE entity_type = ? AND key = ?",
            (entity_type, key),
        ).fetchone()

        payload = (_json(list(aliases)), _json(attrs), url, name, summary)
        if row is None:
            self.conn.execute(
                "INSERT INTO entities (entity_type, key, name, summary, aliases_json,"
                " attrs_json, url, status, origin, source_ref, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'live', ?, ?, datetime('now'))",
                (entity_type, key, name, summary, payload[0], payload[1], url, origin, source_ref),
            )
            return "created"

        current = (row["aliases_json"], row["attrs_json"], row["url"], row["name"], row["summary"])
        if list(current) == list(payload) and row["url"] == url:
            if row["url"] == url and current[0] == payload[0] and current[1] == payload[1] \
                    and current[3] == payload[3] and current[4] == payload[4]:
                return "unchanged"
        self.conn.execute(
            "UPDATE entities SET name = ?, summary = ?, aliases_json = ?, attrs_json = ?,"
            " url = ?, status = 'live', origin = ?, source_ref = ?,"
            " updated_at = datetime('now') WHERE id = ?",
            (name, summary, payload[0], payload[1], url, origin, source_ref, row["id"]),
        )
        return "updated"

    def entity_id(self, entity_type: str, key: str) -> int | None:
        row = self.conn.execute(
            "SELECT id FROM entities WHERE entity_type = ? AND key = ?", (entity_type, key)
        ).fetchone()
        return int(row["id"]) if row else None

    def replace_links(self, links: Iterable[tuple[int, str, int]]) -> None:
        """Make the link set exactly ``links``.

        Links come from ingest *and* from `curation.yaml`, both computed every
        build, so the desired set is complete and anything else is stale.
        """
        desired = {(int(a), str(b), int(c)) for a, b, c in links}
        current = {
            (int(r["from_entity_id"]), r["link_type"], int(r["to_entity_id"]))
            for r in self.conn.execute("SELECT from_entity_id, link_type, to_entity_id FROM entity_links")
        }
        for row in current - desired:
            self.conn.execute(
                "DELETE FROM entity_links WHERE from_entity_id = ? AND link_type = ? AND to_entity_id = ?",
                row,
            )
        for row in desired - current:
            self.conn.execute(
                "INSERT OR IGNORE INTO entity_links (from_entity_id, link_type, to_entity_id)"
                " VALUES (?, ?, ?)",
                row,
            )

    def upsert_document(
        self, *, slug: str, title: str, body: str, entity_id: int | None, source_ref: str = ""
    ) -> str:
        digest = str(abs(hash(body)))
        row = self.conn.execute(
            "SELECT id, content_hash, title, body FROM documents WHERE slug = ?", (slug,)
        ).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO documents (slug, title, body, kind, entity_id, source_ref, content_hash)"
                " VALUES (?, ?, ?, 'readme', ?, ?, ?)",
                (slug, title, body, entity_id, source_ref, digest),
            )
            return "created"
        if row["content_hash"] == digest and row["title"] == title:
            return "unchanged"
        self.conn.execute(
            "UPDATE documents SET title = ?, body = ?, entity_id = ?, source_ref = ?,"
            " content_hash = ?, fetched_at = datetime('now') WHERE id = ?",
            (title, body, entity_id, source_ref, digest, row["id"]),
        )
        return "updated"

    def upsert_knowledge(
        self,
        *,
        slug: str,
        patterns: Sequence[str],
        slots: dict[str, str],
        match: dict[str, Any],
        action: dict[str, Any],
        citations: Sequence[str] = (),
        locale: str = "",
        source: str = "content",
    ) -> str:
        """Upsert one knowledge row by slug. Removed slugs are archived, never deleted."""
        payload = (
            _json(list(patterns)),
            _json(slots),
            _json(match),
            _json(action),
            _json(list(citations)),
        )
        row = self.conn.execute(
            "SELECT id, patterns_json, slots_json, match_json, action_json, citations_json,"
            " locale, status FROM knowledge WHERE slug = ?",
            (slug,),
        ).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO knowledge (slug, locale, patterns_json, slots_json, match_json,"
                " action_json, citations_json, status, source, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'live', ?, datetime('now'))",
                (slug, locale, *payload, source),
            )
            return "created"
        current = (
            row["patterns_json"], row["slots_json"], row["match_json"],
            row["action_json"], row["citations_json"],
        )
        if list(current) == list(payload) and row["locale"] == locale and row["status"] == "live":
            return "unchanged"
        self.conn.execute(
            "UPDATE knowledge SET locale = ?, patterns_json = ?, slots_json = ?, match_json = ?,"
            " action_json = ?, citations_json = ?, status = 'live', source = ?,"
            " updated_at = datetime('now') WHERE id = ?",
            (locale, *payload, source, row["id"]),
        )
        return "updated"

    def archive_missing_knowledge(self, keep_slugs: Iterable[str]) -> int:
        keep = set(keep_slugs)
        rows = self.conn.execute("SELECT id, slug FROM knowledge WHERE status = 'live'").fetchall()
        stale = [r for r in rows if r["slug"] not in keep]
        for row in stale:
            self.conn.execute(
                "UPDATE knowledge SET status = 'archived', updated_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
        return len(stale)

    def archive_missing_entities(self, origin_prefix: str, keep_keys: Iterable[str]) -> int:
        """Archive repos/accounts a source no longer returns. Never delete."""
        keep = set(keep_keys)
        rows = self.conn.execute(
            "SELECT id, key FROM entities WHERE origin LIKE ? AND status = 'live'",
            (origin_prefix + "%",),
        ).fetchall()
        stale = [r for r in rows if r["key"] not in keep]
        for row in stale:
            self.conn.execute(
                "UPDATE entities SET status = 'archived', updated_at = datetime('now') WHERE id = ?",
                (row["id"],),
            )
        return len(stale)

    def clear_documents_for(self, origin_prefix: str) -> None:
        self.conn.execute(
            "DELETE FROM documents WHERE source_ref IN "
            "(SELECT source_ref FROM entities WHERE origin LIKE ? AND source_ref <> '')",
            (origin_prefix + "%",),
        )

    # =========================================================================
    # read (the runtime)
    # =========================================================================

    def entities(self, entity_type: str | None = None) -> list[Entity]:
        sql = "SELECT * FROM entities WHERE status = 'live'"
        args: list[Any] = []
        if entity_type:
            sql += " AND entity_type = ?"
            args.append(entity_type)
        sql += " ORDER BY id"
        return [self._entity(r) for r in self.conn.execute(sql, args)]

    def _entity(self, row: sqlite3.Row) -> Entity:
        return Entity(
            id=int(row["id"]),
            entity_type=row["entity_type"],
            key=row["key"],
            name=row["name"],
            summary=row["summary"] or "",
            aliases=json.loads(row["aliases_json"] or "[]"),
            attrs=json.loads(row["attrs_json"] or "{}"),
            url=row["url"] or "",
            status=row["status"],
            origin=row["origin"] or "",
        )

    def all_links(self) -> list[tuple[str, str, str]]:
        """Every link as (from_key, link_type, to_key), for the static export."""
        return [
            (r["from_key"], r["link_type"], r["to_key"])
            for r in self.conn.execute(
                "SELECT f.key AS from_key, l.link_type, t.key AS to_key FROM entity_links l"
                " JOIN entities f ON f.id = l.from_entity_id"
                " JOIN entities t ON t.id = l.to_entity_id"
                " ORDER BY l.id"
            )
        ]

    def entity_summary(self) -> list[sqlite3.Row]:
        """What kinds of thing exist, and how many of each are live."""
        return list(
            self.conn.execute(
                "SELECT entity_type, COUNT(*) AS live,"
                " SUM(CASE WHEN status = 'archived' THEN 1 ELSE 0 END) AS archived"
                " FROM entities GROUP BY entity_type ORDER BY live DESC"
            )
        )

    def find_entities(self, needle: str = "", entity_type: str = "", limit: int = 40) -> list[Entity]:
        """Search the vocabulary by name, key, alias or attribute value.

        Exists so an agent can answer "does a `kotlin` language entity exist?"
        and "what topics are there?" without opening the database directly --
        the maintainer's permissions allow `pc`, not `sqlite3`.
        """
        sql = "SELECT * FROM entities WHERE status = 'live'"
        args: list[Any] = []
        if entity_type:
            sql += " AND entity_type = ?"
            args.append(entity_type)
        if needle:
            sql += (
                " AND (name LIKE ? OR key LIKE ? OR aliases_json LIKE ?"
                " OR summary LIKE ? OR attrs_json LIKE ?)"
            )
            args.extend([f"%{needle}%"] * 5)
        sql += " ORDER BY entity_type, name LIMIT ?"
        args.append(limit)
        return [self._entity(r) for r in self.conn.execute(sql, args)]

    def entity_by_key(self, key: str) -> Entity | None:
        row = self.conn.execute(
            "SELECT * FROM entities WHERE key = ? AND status = 'live' ORDER BY id LIMIT 1", (key,)
        ).fetchone()
        return self._entity(row) if row else None

    def linked_entities(
        self, entity_id: int, link_type: str, direction: str, entity_type: str | None = None
    ) -> list[Entity]:
        if direction == "in":
            sql = (
                "SELECT e.* FROM entities e JOIN entity_links l ON l.from_entity_id = e.id"
                " WHERE l.link_type = ? AND l.to_entity_id = ? AND e.status = 'live'"
            )
        else:
            sql = (
                "SELECT e.* FROM entities e JOIN entity_links l ON l.to_entity_id = e.id"
                " WHERE l.link_type = ? AND l.from_entity_id = ? AND e.status = 'live'"
            )
        args: list[Any] = [link_type, entity_id]
        if entity_type:
            sql += " AND e.entity_type = ?"
            args.append(entity_type)
        sql += " ORDER BY e.id"
        return [self._entity(r) for r in self.conn.execute(sql, args)]

    def list_entities(
        self, entity_type: str, *, order_by: str = "id", desc: bool = False, limit: int = 0
    ) -> list[Entity]:
        direction = "DESC" if desc else "ASC"
        sql = f"SELECT * FROM entities WHERE status = 'live' AND entity_type = ? ORDER BY {order_by} {direction}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self._entity(r) for r in self.conn.execute(sql, (entity_type,))]

    def knowledge_rows(self) -> list[KnowledgeRow]:
        rows = self.conn.execute("SELECT * FROM knowledge WHERE status = 'live'").fetchall()
        out = [
            KnowledgeRow(
                id=int(r["id"]),
                slug=r["slug"],
                locale=r["locale"] or "",
                patterns=json.loads(r["patterns_json"] or "[]"),
                slots=json.loads(r["slots_json"] or "{}"),
                match=json.loads(r["match_json"] or "{}"),
                action=json.loads(r["action_json"] or "{}"),
                citations=json.loads(r["citations_json"] or "[]"),
            )
            for r in rows
        ]
        # Most specific first: rows with slots beat rows without, then YAML order.
        out.sort(key=lambda k: (-len(k.slots), k.id))
        return out

    def all_documents(self) -> list[tuple[Document, str]]:
        """Every document with its entity key, for the static export."""
        rows = self.conn.execute(
            "SELECT d.*, e.key AS entity_key FROM documents d"
            " LEFT JOIN entities e ON e.id = d.entity_id ORDER BY d.id"
        ).fetchall()
        return [
            (
                Document(id=int(r["id"]), slug=r["slug"], title=r["title"],
                         body=r["body"], entity_id=r["entity_id"]),
                r["entity_key"] or "",
            )
            for r in rows
        ]

    def documents_for_entity(self, entity_id: int) -> list[Document]:
        return [
            Document(
                id=int(r["id"]), slug=r["slug"], title=r["title"],
                body=r["body"], entity_id=r["entity_id"],
            )
            for r in self.conn.execute(
                "SELECT * FROM documents WHERE entity_id = ?", (entity_id,)
            )
        ]

    def search_documents(self, entity_id: int, words: Sequence[str], limit: int = 1) -> list[Document]:
        """FTS5 (trigram) over one entity's documents. Deterministic, zero tokens.

        Scoped to a single entity on purpose: an unscoped full-text guess over the
        whole corpus answers questions it has no business answering.
        """
        if not words:
            return []
        query = " AND ".join('"' + w.replace('"', '""') + '"' for w in words)
        try:
            rows = self.conn.execute(
                "SELECT d.* FROM documents_fts f JOIN documents d ON d.id = f.rowid"
                " WHERE documents_fts MATCH ? AND d.entity_id = ? LIMIT ?",
                (query, entity_id, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [
            Document(id=int(r["id"]), slug=r["slug"], title=r["title"],
                     body=r["body"], entity_id=r["entity_id"])
            for r in rows
        ]

    # =========================================================================
    # live state: the only table the runtime writes
    # =========================================================================

    def record_turn(
        self,
        *,
        session_id: str,
        question: str,
        normalized: str,
        answer: str,
        source: str,
        matched_slug: str,
        citations: Sequence[Citation],
        unresolved: bool,
        latency_ms: float,
        refs: dict[str, str] | None = None,
    ) -> int:
        with self._write_lock:
            return self._record_turn_locked(
                session_id=session_id, question=question, normalized=normalized,
                answer=answer, source=source, matched_slug=matched_slug,
                citations=citations, unresolved=unresolved, latency_ms=latency_ms,
                refs=refs or {},
            )

    def _record_turn_locked(self, **kw) -> int:
        session_id = kw["session_id"]
        turn = self.conn.execute(
            "SELECT COALESCE(MAX(turn), 0) + 1 AS t FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()["t"]
        self.conn.execute(
            "INSERT INTO messages (session_id, turn, role, content, normalized,"
            " resolution_source, matched_slug, citations_json, unresolved, latency_ms)"
            " VALUES (?, ?, 'user', ?, ?, '', '', '[]', ?, 0)",
            (session_id, turn, kw["question"], kw["normalized"], 1 if kw["unresolved"] else 0),
        )
        self.conn.execute(
            "INSERT INTO messages (session_id, turn, role, content, normalized,"
            " resolution_source, matched_slug, citations_json, refs_json,"
            " unresolved, latency_ms)"
            " VALUES (?, ?, 'assistant', ?, '', ?, ?, ?, ?, 0, ?)",
            (
                session_id, turn, kw["answer"], kw["source"], kw["matched_slug"],
                _json([c.to_json() for c in kw["citations"]]),
                _json(kw.get("refs") or {}), kw["latency_ms"],
            ),
        )
        self.conn.commit()
        return int(turn)

    def count_messages(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"])

    # =========================================================================
    # reports -- both the owner and the maintainer read these, so they cannot
    # disagree about what "good" means
    # =========================================================================

    # -- sessions: a view over messages, not a table --------------------------

    def list_sessions(self, limit: int = 50) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM v_sessions LIMIT ?", (limit,)))

    def session(self, session_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM v_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()

    def session_messages(self, session_id: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT id, turn, role, content, resolution_source, matched_slug,"
                " citations_json, refs_json, unresolved, latency_ms, created_at FROM messages"
                " WHERE session_id = ? ORDER BY turn, id",
                (session_id,),
            )
        )

    def delete_session(self, session_id: str) -> int:
        """Delete one visitor's transcript.

        The "archive, never delete" rule protects *knowledge*: a past answer must
        stay explicable. A transcript is the visitor's own data, and being able
        to remove it is a feature, not a violation.
        """
        with self._write_lock:
            cursor = self.conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            self.conn.commit()
            return cursor.rowcount

    def unresolved_inbox(self, limit: int = 50) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM v_unresolved_inbox LIMIT ?", (limit,)
            )
        )

    def resolution_mix(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM v_resolution_mix"))

    def knowledge_coverage(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM v_knowledge_coverage ORDER BY hits ASC"))

    def dead_knowledge(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM v_dead_knowledge"))

    def stats(self) -> dict[str, Any]:
        def scalar(sql: str) -> int:
            return int(self.conn.execute(sql).fetchone()[0] or 0)

        answered = scalar(
            "SELECT COUNT(*) FROM messages WHERE role = 'assistant' AND resolution_source <> 'refuse'"
        )
        refused = scalar(
            "SELECT COUNT(*) FROM messages WHERE role = 'assistant' AND resolution_source = 'refuse'"
        )
        total = answered + refused
        return {
            "entities": scalar("SELECT COUNT(*) FROM entities WHERE status = 'live'"),
            "documents": scalar("SELECT COUNT(*) FROM documents"),
            "knowledge": scalar("SELECT COUNT(*) FROM knowledge WHERE status = 'live'"),
            "knowledge_archived": scalar("SELECT COUNT(*) FROM knowledge WHERE status = 'archived'"),
            "messages": scalar("SELECT COUNT(*) FROM messages"),
            "answered": answered,
            "refused": refused,
            "kappa": (answered / total) if total else None,
            "unresolved": scalar("SELECT COUNT(*) FROM messages WHERE unresolved = 1"),
        }
