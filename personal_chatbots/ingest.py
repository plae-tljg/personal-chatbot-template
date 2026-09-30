"""Ingest: GitHub metadata -> entities, links, documents.

This runs offline from the serving path. The bot answers from rows and must keep
answering when GitHub is down, so nothing here is on a request path.

The API is not the runtime, and the API is not the schema either: this module's
whole job is to translate somebody else's JSON into our vocabulary, and to be
idempotent while doing it.

Rate limits (unauthenticated: 60/hour) shape the design: four accounts plus
their repositories is ~46 requests, which leaves room for about ten READMEs.
Descriptions are the better summary anyway; READMEs exist for the search rung.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .textnorm import aliases_for

API = "https://api.github.com"
USER_AGENT = "personal-chatbots/0.1 (+https://github.com/plae-tljg)"


@dataclass
class EntitySpec:
    entity_type: str
    key: str
    name: str
    summary: str = ""
    aliases: list[str] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)
    url: str = ""
    origin: str = ""
    source_ref: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)  # (link_type, to_key)


@dataclass
class IngestResult:
    entities: list[EntitySpec] = field(default_factory=list)
    readmes: list[tuple[str, str, str]] = field(default_factory=list)  # (entity_key, title, body)
    fetched: int = 0
    from_cache: int = 0
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------


class GitHubError(RuntimeError):
    pass


def _cache_path(cache_dir: Path, name: str) -> Path:
    safe = name.replace("/", "__")
    return cache_dir / "github" / f"{safe}.json"


def _get(url: str, cache: Path, *, offline: bool, token: str | None, result: IngestResult) -> Any:
    if cache.exists():
        result.from_cache += 1
        return json.loads(cache.read_text(encoding="utf-8"))
    if offline:
        raise GitHubError(
            f"offline and no cached response for {url}\n"
            f"  expected cache: {cache}\n"
            f"  run `pc build` with network once, or point --cache at a fixture directory"
        )

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (fixed host)
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GitHubError(f"GET {url} -> HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise GitHubError(f"GET {url} -> {exc.reason}") from exc

    result.fetched += 1
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload


def _get_text(url: str, cache: Path, *, offline: bool, token: str | None, result: IngestResult) -> str:
    if cache.exists():
        result.from_cache += 1
        return json.loads(cache.read_text(encoding="utf-8"))["content"]
    if offline:
        return ""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github.raw"}
    )
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError):
        return ""
    result.fetched += 1
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"content": body}, ensure_ascii=False), encoding="utf-8")
    return body


# ---------------------------------------------------------------------------
# mapping
# ---------------------------------------------------------------------------


def _topic_slug(topic: str) -> str:
    return "topic:" + topic.strip().lower().replace(" ", "-")


def _language_key(language: str) -> str:
    return "language:" + language.strip().lower()


def repo_entity(repo: dict[str, Any], origin: str) -> EntitySpec:
    owner = repo["owner"]["login"]
    name = repo["name"]
    key = f"{owner}/{name}"
    language = repo.get("language") or ""
    license_info = repo.get("license") or {}
    attrs: dict[str, Any] = {
        "stars": int(repo.get("stargazers_count") or 0),
        "forks": int(repo.get("forks_count") or 0),
        "size_kb": int(repo.get("size") or 0),
        "open_issues": int(repo.get("open_issues_count") or 0),
        "language": language,
        "fork": bool(repo.get("fork")),
        "archived": bool(repo.get("archived")),
        "topics": list(repo.get("topics") or []),
        "pushed_at": (repo.get("pushed_at") or "")[:10],
        "created_at": (repo.get("created_at") or "")[:10],
        "homepage": repo.get("homepage") or "",
        "license": license_info.get("spdx_id") or "",
        "default_branch": repo.get("default_branch") or "main",
        "github_id": int(repo.get("id") or 0),
    }
    links: list[tuple[str, str]] = [("owned_by", owner)]
    if language:
        links.append(("uses_language", _language_key(language)))
    for topic in attrs["topics"]:
        links.append(("tagged", _topic_slug(topic)))

    return EntitySpec(
        entity_type="repo",
        key=key,
        name=name,
        summary=(repo.get("description") or "").strip(),
        aliases=aliases_for(name, key),
        attrs=attrs,
        url=repo.get("html_url") or f"https://github.com/{key}",
        origin=origin,
        source_ref=repo.get("html_url") or "",
        links=links,
    )


def account_entity(user: dict[str, Any], origin: str) -> EntitySpec:
    login = user["login"]
    return EntitySpec(
        entity_type="account",
        key=login,
        name=user.get("name") or login,
        summary=(user.get("bio") or "").strip(),
        aliases=aliases_for(login, login, extra=[user.get("name") or ""]),
        attrs={
            "public_repos": int(user.get("public_repos") or 0),
            "followers": int(user.get("followers") or 0),
            "created_at": (user.get("created_at") or "")[:10],
        },
        url=user.get("html_url") or f"https://github.com/{login}",
        origin=origin,
        source_ref=user.get("html_url") or "",
    )


def language_entity(language: str, origin: str) -> EntitySpec:
    return EntitySpec(
        entity_type="language",
        key=_language_key(language),
        name=language,
        summary=f"Projects written in {language}.",
        aliases=aliases_for(language),
        attrs={},
        url="",
        origin=origin,
    )


def topic_entity(topic: str, origin: str) -> EntitySpec:
    return EntitySpec(
        entity_type="topic",
        key=_topic_slug(topic),
        name=topic,
        summary=f"Projects tagged {topic}.",
        aliases=aliases_for(topic),
        attrs={},
        url="",
        origin=origin,
    )


# ---------------------------------------------------------------------------
# the pull
# ---------------------------------------------------------------------------


def ingest_sources(cfg: Config, *, offline: bool = False, cache_dir: Path | None = None) -> IngestResult:
    """Pull every configured GitHub account into EntitySpecs.

    ``sources`` are strings like ``github:plae-tljg``. ``cache_dir`` lets tests
    point at a fixture instead of the real API.
    """
    cache_dir = cache_dir or cfg.cache_dir
    token = os.environ.get("GITHUB_TOKEN") or None
    result = IngestResult()

    specs: dict[tuple[str, str], EntitySpec] = {}
    readmes: list[tuple[str, str, str]] = []
    repos_for_readme: list[tuple[EntitySpec, str]] = []

    def put(spec: EntitySpec) -> EntitySpec:
        existing = specs.get((spec.entity_type, spec.key))
        if existing:
            for link in spec.links:
                if link not in existing.links:
                    existing.links.append(link)
            for alias in spec.aliases:
                if alias not in existing.aliases:
                    existing.aliases.append(alias)
            if not existing.summary and spec.summary:
                existing.summary = spec.summary
            return existing
        specs[(spec.entity_type, spec.key)] = spec
        return spec

    for source in cfg.sources:
        kind, _, locator = source.partition(":")
        if kind != "github" or not locator:
            result.notes.append(f"skipped unknown source {source!r}")
            continue
        origin = f"github:{locator}"
        try:
            user = _get(f"{API}/users/{locator}", _cache_path(cache_dir, f"user_{locator}"),
                        offline=offline, token=token, result=result)
            repos = _get(f"{API}/users/{locator}/repos?per_page=100&sort=pushed",
                         _cache_path(cache_dir, f"repos_{locator}"),
                         offline=offline, token=token, result=result)
        except GitHubError as exc:
            result.notes.append(str(exc))
            continue

        account = put(account_entity(user, origin))
        account.links.append(("owned_by", cfg.owner_key))

        for repo in repos:
            spec = put(repo_entity(repo, origin))
            if spec.attrs.get("language"):
                put(language_entity(spec.attrs["language"], origin))
            for topic in spec.attrs.get("topics", []):
                put(topic_entity(topic, origin))
            repos_for_readme.append((spec, origin))

    # READMEs, most-starred first. One request each, so with no token the budget
    # decides how many arrive: `readme_limit` is asked for and the API refuses
    # the rest. Since every response is cached, rebuilding after the window
    # resets fetches only what is still missing.
    repos_for_readme.sort(key=lambda pair: pair[0].attrs.get("stars", 0), reverse=True)
    limit = cfg.readme_limit
    selected = repos_for_readme if limit <= 0 else repos_for_readme[:limit]
    if limit and len(repos_for_readme) > limit:
        why = (
            f"set GITHUB_TOKEN (5000/hour) and rebuild to get all of them"
            if not token
            else f"raise build.readme_limit to cover the rest"
        )
        result.notes.append(
            f"fetched READMEs for {limit} of {len(repos_for_readme)} repositories -- {why}. "
            f"Questions needing README text only work for those {limit}."
        )
    for spec, origin in selected:
        owner, _, name = spec.key.partition("/")
        body = _get_text(
            f"{API}/repos/{owner}/{name}/readme",
            _cache_path(cache_dir, f"readme_{owner}__{name}"),
            offline=offline, token=token, result=result,
        )
        if body:
            readmes.append((spec.key, spec.name, body[: cfg.readme_bytes]))

    result.entities = list(specs.values())
    result.readmes = readmes
    return result
