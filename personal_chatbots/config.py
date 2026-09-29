"""Configuration: ``content/bot.json`` plus the paths around it.

Config is a file, not a table (docs/CONCERNS.md C4): one bot, changed by a human,
and a change to it means a deploy rather than a reviewed data change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Scope:
    """Which bot's data a Store is allowed to touch.

    v1 has exactly one bot, so this carries no predicate yet. It exists so that
    multi-bot later is a change inside Store's WHERE clauses rather than a change
    at every call site -- every query already goes through Store. A scope object
    with one field is cheaper than a `bot_id` column on nine tables that nobody
    filters on.
    """

    bot: str = "default"


@dataclass(frozen=True)
class Config:
    root: Path
    data: dict[str, Any]
    #: Overrides for tests and for building a throwaway database. A build is
    #: reproducible, so pointing it somewhere else is always safe.
    db_override: Path | None = None
    cache_override: Path | None = None

    @classmethod
    def load(
        cls,
        root: str | Path,
        *,
        db_path: str | Path | None = None,
        cache_dir: str | Path | None = None,
    ) -> "Config":
        root = Path(root).resolve()
        path = root / "content" / "bot.json"
        if not path.exists():
            raise ConfigError(f"missing config: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
        cfg = cls(
            root=root,
            data=data,
            db_override=Path(db_path) if db_path else None,
            cache_override=Path(cache_dir) if cache_dir else None,
        )
        cfg._validate()
        return cfg

    def _validate(self) -> None:
        rt = self.data.get("runtime")
        if not isinstance(rt, dict):
            raise ConfigError("bot.json: missing 'runtime' object")
        ladder = rt.get("ladder")
        if not isinstance(ladder, list) or not ladder:
            raise ConfigError("bot.json: runtime.ladder must be a non-empty list")
        level = rt.get("level")
        if level not in (1, 2, 3):
            raise ConfigError(f"bot.json: runtime.level must be 1, 2 or 3 (got {level!r})")
        if not self.data.get("owner_entity_key"):
            raise ConfigError("bot.json: owner_entity_key is required")

    # -- convenience accessors ------------------------------------------------

    @property
    def name(self) -> str:
        return self.data.get("name", "chatbot")

    @property
    def default_locale(self) -> str:
        return self.data.get("default_locale", "en")

    @property
    def owner_key(self) -> str:
        return self.data["owner_entity_key"]

    @property
    def ladder(self) -> list[str]:
        return list(self.data["runtime"]["ladder"])

    @property
    def level(self) -> int:
        return int(self.data["runtime"]["level"])

    @property
    def max_citations(self) -> int:
        return int(self.data["runtime"].get("max_citations", 3))

    @property
    def refuse_template(self) -> str:
        return self.data.get("refuse_template", "I don't know.")

    @property
    def sources(self) -> list[str]:
        return list(self.data.get("build", {}).get("sources", []))

    @property
    def readme_bytes(self) -> int:
        return int(self.data.get("build", {}).get("readme_bytes", 8000))

    @property
    def readme_limit(self) -> int:
        """How many READMEs to fetch.

        The unauthenticated GitHub API allows 60 requests/hour. Four accounts
        plus their repositories is ~46, which leaves room for about ten READMEs.
        Descriptions are the better summary anyway; READMEs are for the search
        rung.
        """
        return int(self.data.get("build", {}).get("readme_limit", 10))

    # -- paths -----------------------------------------------------------------

    @property
    def db_path(self) -> Path:
        return self.db_override or (self.root / "data" / "bot.db")

    @property
    def cache_dir(self) -> Path:
        return self.cache_override or (self.root / "data" / "cache")

    @property
    def content_dir(self) -> Path:
        return self.root / "content"

    @property
    def schema_path(self) -> Path:
        return self.root / "db" / "schema.sql"
