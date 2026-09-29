"""The serving path: FastAPI over the built database, plus a one-page widget.

Two design points the code makes visible:

* **The runtime is read-only.** The only write is one row pair in ``messages``.
  Nothing here can change what the bot knows — that requires a pull request.
* **Every answer says how it was produced.** The rung and the citations are part
  of the response, because "answered from tables, zero tokens" is the claim, and
  a claim you cannot see is a claim you have to take on faith.
"""

# NOTE: deliberately no `from __future__ import annotations` in this module.
# It turns annotations into strings, and FastAPI then cannot resolve AskRequest,
# which only exists as a local name inside create_app -- the symptom is a 422 on
# every request.

import json
from pathlib import Path
from typing import Any

from .config import Config
from .engine import Answer, Runtime
from .store import SchemaTooOld, Store

STATIC_DIR = Path(__file__).parent / "static"


class RuntimeCache:
    """Rebuild the in-memory index when the database file changes.

    A build replaces rows, so a long-running server must notice. Watching the
    mtime is enough and costs nothing per request.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._mtime: float | None = None
        self._runtime: Runtime | None = None
        self._store: Store | None = None

    def get(self) -> tuple[Runtime, Store]:
        mtime = self.cfg.db_path.stat().st_mtime if self.cfg.db_path.exists() else None
        if self._runtime is None or mtime != self._mtime:
            if self._store is not None:
                self._store.close()
            self._store = Store(self.cfg.db_path)
            self._store.require_schema()
            self._runtime = Runtime.from_store(self._store, self.cfg)
            self._mtime = mtime
        return self._runtime, self._store  # type: ignore[return-value]


def _serialize(answer: Answer) -> dict[str, Any]:
    return {
        "session_id": answer.session_id,
        "turn": answer.turn,
        "text": answer.text,
        "source": answer.source,
        "matched": answer.matched_slug,
        "citations": [{"key": c.key, "label": c.label, "url": c.url} for c in answer.citations],
        "latency_ms": round(answer.latency_ms, 2),
        "slots": {name: entity.key for name, entity in answer.slots.items()},
        # What a referring expression was read as ("the second one" -> a key).
        # Surfaced because a rewrite the visitor cannot see is indistinguishable
        # from a guess.
        "refs": answer.refs,
        # False for a fallback answer, and the client shows it.
        "deterministic": answer.source != "fallback",
        "tokens": 0 if answer.source != "fallback" else None,
        # The honest boundary, sent to the client so the UI can say it out loud.
        "context_used": False,
    }


def create_app(cfg: Config) -> Any:
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import FileResponse, JSONResponse
        from pydantic import BaseModel
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("serving needs fastapi + uvicorn") from exc

    cache = RuntimeCache(cfg)
    app = FastAPI(title=cfg.name, docs_url=None, redoc_url=None)

    @app.exception_handler(SchemaTooOld)
    def _stale_schema(_request: Any, exc: Exception) -> Any:
        # 503, not 500: the service is fine, its database is out of date, and the
        # client can do nothing about it except the owner running one command.
        return JSONResponse({"detail": str(exc)}, status_code=503)

    class AskRequest(BaseModel):
        question: str
        session_id: str | None = None

    @app.get("/")
    def index() -> Any:
        page = STATIC_DIR / "index.html"
        if not page.exists():
            raise HTTPException(status_code=500, detail="widget missing")
        return FileResponse(page)

    @app.get("/api/config")
    def api_config() -> Any:
        return {
            "name": cfg.name,
            "tagline": cfg.data.get("tagline", ""),
            "level": cfg.level,
            "ladder": cfg.ladder,
            # Display only. The stored citations are never capped, because the
            # frame reads them back to resolve "the second one".
            "max_citations": cfg.max_citations,
        }

    @app.post("/api/ask")
    def api_ask(request: AskRequest) -> Any:
        question = request.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="empty question")
        if len(question) > 500:
            raise HTTPException(status_code=400, detail="question too long")
        runtime, store = cache.get()
        answer = runtime.ask_and_record(question, session_id=request.session_id)
        return JSONResponse(_serialize(answer))

    # -- sessions ------------------------------------------------------------
    #
    # A session is a view over `messages`, not a table: title, turn count and
    # timestamps are all derivable, and a second copy of that truth would be one
    # more thing to keep in sync.

    @app.get("/api/sessions")
    def api_sessions(limit: int = 50) -> Any:
        _, store = cache.get()
        return {
            "sessions": [
                {
                    "id": row["session_id"],
                    "title": (row["title"] or "").strip() or "(empty)",
                    "turns": row["turns"],
                    "messages": row["messages"],
                    "unresolved": row["unresolved"],
                    "started_at": row["started_at"],
                    "last_at": row["last_at"],
                }
                for row in store.list_sessions(max(1, min(limit, 200)))
            ]
        }

    @app.get("/api/sessions/{session_id}")
    def api_session(session_id: str) -> Any:
        _, store = cache.get()
        meta = store.session(session_id)
        if meta is None:
            raise HTTPException(status_code=404, detail=f"no such session: {session_id}")
        messages = [
            {
                "id": row["id"],
                "turn": row["turn"],
                "role": row["role"],
                "content": row["content"],
                "source": row["resolution_source"],
                "matched": row["matched_slug"],
                "citations": json.loads(row["citations_json"] or "[]"),
                "refs": json.loads(row["refs_json"] or "{}"),
                "unresolved": bool(row["unresolved"]),
                "latency_ms": round(row["latency_ms"], 2),
                "at": row["created_at"],
            }
            for row in store.session_messages(session_id)
        ]
        return {
            "id": session_id,
            "title": (meta["title"] or "").strip() or "(empty)",
            "turns": meta["turns"],
            "unresolved": meta["unresolved"],
            "started_at": meta["started_at"],
            "messages": messages,
        }

    @app.delete("/api/sessions/{session_id}")
    def api_delete_session(session_id: str) -> Any:
        _, store = cache.get()
        deleted = store.delete_session(session_id)
        if deleted == 0:
            raise HTTPException(status_code=404, detail=f"no such session: {session_id}")
        return {"deleted": deleted}

    @app.get("/api/stats")
    def api_stats() -> Any:
        _, store = cache.get()
        stats = store.stats()
        return {
            "knowledge": stats["knowledge"],
            "entities": stats["entities"],
            "messages": stats["messages"],
            "kappa": stats["kappa"],
            "refused": stats["refused"],
            "unresolved": stats["unresolved"],
            "ladder": cfg.ladder,
        }

    return app
