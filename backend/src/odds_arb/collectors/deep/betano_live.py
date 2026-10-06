from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any, cast

import structlog
from curl_cffi import requests as cc_requests

from odds_arb.collectors.deep.competitions import DeepCompetition

BETANO_BASE = "https://www.betano.bet.br"
BETANO_IMPERSONATE = "chrome131"
DEFAULT_TIMEOUT_SECONDS = 15.0
_PATH_RE = re.compile(r"/odds/[a-z0-9-]+/\d{6,}/")
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html",
    "Referer": "https://www.betano.bet.br/",
}

BETANO_DEEP_BT = "6"
BETANO_DEEP_REQ = "s,stnf,c"
BETANO_DEEP_FALLBACK_SLUG = "event"


def build_betano_deep_url(event_url_path: str) -> str:
    path = event_url_path if event_url_path.startswith("/") else f"/{event_url_path}"
    return f"{BETANO_BASE}/api{path}?bt={BETANO_DEEP_BT}&req={BETANO_DEEP_REQ}"


def extract_betano_deep_markets(json_text: str) -> list[Mapping[str, Any]]:
    try:
        payload = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(payload, Mapping):
        return []
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return []
    event = data.get("event")
    if not isinstance(event, Mapping):
        return []
    markets = event.get("markets")
    if not isinstance(markets, list):
        return []
    return [m for m in markets if isinstance(m, Mapping)]


logger = structlog.get_logger(__name__)

HtmlGetter = Callable[[str], str]
JsonGetter = Callable[[str], str]


def discover_betano_event_paths(html: str) -> list[str]:
    return sorted(set(_PATH_RE.findall(html)))


def extract_betano_event(html: str) -> Mapping[str, Any] | None:
    marker = html.find('"event":{')
    if marker == -1:
        return None
    open_idx = html.find("{", marker + 7)
    if open_idx == -1:
        return None
    depth = 0
    for j in range(open_idx, len(html)):
        ch = html[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(html[open_idx : j + 1])
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, Mapping) else None
    return None


def _default_html_getter(url: str) -> str:
    with cc_requests.Session(impersonate=cast(Any, BETANO_IMPERSONATE)) as session:
        response = session.get(url, headers=_HEADERS, timeout=DEFAULT_TIMEOUT_SECONDS)
        response.raise_for_status()
        return cast(str, response.text)


def fetch_betano_events(
    competition: DeepCompetition,
    *,
    html_getter: HtmlGetter = _default_html_getter,
) -> list[Mapping[str, Any]]:
    listing_html = html_getter(competition.betano_url)
    events: list[Mapping[str, Any]] = []
    for path in discover_betano_event_paths(listing_html):
        try:
            event_html = html_getter(BETANO_BASE + path)
        except Exception as exc:  # one bad event must not kill the house
            logger.warning("betano_live.event_fetch_failed", path=path, error=str(exc))
            continue
        event = extract_betano_event(event_html)
        if event is not None:
            events.append(event)
    return events


_JSON_HEADERS = {
    **_HEADERS,
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}


def _default_json_getter(url: str) -> str:
    with cc_requests.Session(impersonate=cast(Any, BETANO_IMPERSONATE)) as session:
        response = session.get(url, headers=_JSON_HEADERS, timeout=DEFAULT_TIMEOUT_SECONDS)
        response.raise_for_status()
        return cast(str, response.text)


def _merge_deep_markets(
    event: Mapping[str, Any], deep_markets: list[Mapping[str, Any]]
) -> Mapping[str, Any]:
    existing = event.get("markets")
    existing_list = existing if isinstance(existing, list) else []
    seen = {str(m.get("id")) for m in existing_list if isinstance(m, Mapping)}
    extra = [m for m in deep_markets if str(m.get("id")) not in seen]
    merged = dict(event)
    merged["markets"] = [*existing_list, *extra]
    return merged


def fetch_betano_events_deep(
    competition: DeepCompetition,
    *,
    html_getter: HtmlGetter = _default_html_getter,
    json_getter: JsonGetter = _default_json_getter,
) -> list[Mapping[str, Any]]:
    events = fetch_betano_events(competition, html_getter=html_getter)
    enriched: list[Mapping[str, Any]] = []
    for event in events:
        path = event.get("url")
        if not isinstance(path, str) or not path:
            # The bt=6 API is slug-agnostic — only the numeric id matters — so
            # synthesize a path from id when SSR omits `url`. Enrich then never
            # silently no-ops on a missing field.
            event_id = event.get("id")
            if event_id is None:
                enriched.append(event)
                continue
            path = f"/odds/{BETANO_DEEP_FALLBACK_SLUG}/{event_id}/"
        try:
            deep_markets = extract_betano_deep_markets(json_getter(build_betano_deep_url(path)))
        except Exception as exc:  # one bad event must not kill the house
            logger.warning("betano_live.deep_fetch_failed", path=path, error=str(exc))
            enriched.append(event)
            continue
        enriched.append(_merge_deep_markets(event, deep_markets) if deep_markets else event)
    return enriched
