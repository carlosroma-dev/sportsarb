import json as _json

from odds_arb.collectors.deep.betano_live import (
    build_betano_deep_url,
    discover_betano_event_paths,
    extract_betano_deep_markets,
    extract_betano_event,
    fetch_betano_events,
    fetch_betano_events_deep,
)
from odds_arb.collectors.deep.competitions import DeepCompetition

LISTING_HTML = """
<html><a href="/odds/egito-ira/83963790/">Egito</a>
<a href="/odds/brasil-italia/83963791/">Brasil</a>
<a href="/odds/egito-ira/83963790/">dup</a>
<a href="/sport/futebol/">menu</a></html>
"""

EVENT_HTML = (
    'prefix...{"event":{"id":83963790,"name":"Egito - Irã",'
    '"startTime":1750000000000,"markets":[{"id":7,"name":"x","nested":{"a":1}}]}}...suffix'
)


def test_discover_dedupes_and_sorts_event_paths():
    paths = discover_betano_event_paths(LISTING_HTML)
    assert paths == ["/odds/brasil-italia/83963791/", "/odds/egito-ira/83963790/"]


def test_extract_event_returns_embedded_object_with_nested_braces():
    event = extract_betano_event(EVENT_HTML)
    assert event is not None
    assert event["id"] == 83963790
    assert event["name"] == "Egito - Irã"
    assert event["markets"][0]["nested"] == {"a": 1}


def test_extract_event_returns_none_when_absent():
    assert extract_betano_event("<html>no event here</html>") is None


def test_fetch_betano_events_assembles_listing_and_details():
    comp = DeepCompetition(alias="t", betano_url="https://b/list/", label="T")
    pages = {
        "https://b/list/": LISTING_HTML,
        "https://www.betano.bet.br/odds/egito-ira/83963790/": EVENT_HTML,
        "https://www.betano.bet.br/odds/brasil-italia/83963791/": "no event",
    }
    events = fetch_betano_events(comp, html_getter=lambda url: pages[url])
    assert len(events) == 1
    assert events[0]["id"] == 83963790


def test_fetch_betano_events_swallows_per_event_errors():
    comp = DeepCompetition(alias="t", betano_url="https://b/list/", label="T")

    def getter(url: str) -> str:
        if url == "https://b/list/":
            return LISTING_HTML
        if url.endswith("83963790/"):
            return EVENT_HTML
        raise RuntimeError("boom")

    events = fetch_betano_events(comp, html_getter=getter)
    assert len(events) == 1


def test_extract_event_returns_none_when_marker_has_no_following_brace():
    assert extract_betano_event('garbage "event": not-an-object here') is None


def test_fetch_betano_events_skips_malformed_event_without_crashing():
    comp = DeepCompetition(alias="t", betano_url="https://b/list/", label="T")
    pages = {
        "https://b/list/": LISTING_HTML,
        "https://www.betano.bet.br/odds/egito-ira/83963790/": EVENT_HTML,
        "https://www.betano.bet.br/odds/brasil-italia/83963791/": 'x "event": broken',
    }
    events = fetch_betano_events(comp, html_getter=lambda url: pages[url])
    assert len(events) == 1


def test_build_betano_deep_url_from_event_path():
    url = build_betano_deep_url("/odds/equador-alemanha/83963753/")
    assert url == (
        "https://www.betano.bet.br/api/odds/equador-alemanha/83963753/?bt=6&req=s,stnf,c"
    )


def test_build_betano_deep_url_adds_leading_slash():
    url = build_betano_deep_url("odds/egito-ira/83963790/")
    assert url == ("https://www.betano.bet.br/api/odds/egito-ira/83963790/?bt=6&req=s,stnf,c")


def test_extract_deep_markets_returns_market_list():
    payload = _json.dumps(
        {"data": {"event": {"markets": [{"id": 4159, "name": "Total de chutes"}]}}}
    )
    markets = extract_betano_deep_markets(payload)
    assert [m["id"] for m in markets] == [4159]


def test_extract_deep_markets_returns_empty_on_bad_json():
    assert extract_betano_deep_markets("not json{") == []


def test_extract_deep_markets_returns_empty_when_keys_missing():
    assert extract_betano_deep_markets(_json.dumps({"data": {}})) == []
    assert extract_betano_deep_markets(_json.dumps({"data": {"event": {}}})) == []
    assert extract_betano_deep_markets(_json.dumps({"data": {"event": {"markets": 5}}})) == []


SSR_EVENT_HTML = (
    'x{"event":{"id":83963790,"name":"Egito - Irã",'
    '"url":"/odds/egito-ira/83963790/","startTime":1750000000000,'
    '"markets":[{"id":34,"name":"Total de Escanteios",'
    '"selections":[{"id":1,"name":"Mais de 9.5","handicap":"9.5","price":"2.10"},'
    '{"id":2,"name":"Menos de 9.5","handicap":"9.5","price":"1.85"}]}]}}y'
)
DEEP_SHOTS_JSON = _json.dumps(
    {
        "data": {
            "event": {
                "markets": [
                    {
                        "id": 4159,
                        "name": "Total de chutes",
                        "selections": [
                            {
                                "id": 901,
                                "name": "Mais de 25.5",
                                "handicap": "25.5",
                                "price": "2.00",
                            },
                            {
                                "id": 902,
                                "name": "Menos de 25.5",
                                "handicap": "25.5",
                                "price": "1.90",
                            },
                        ],
                    },
                ]
            }
        }
    }
)


def _deep_comp() -> DeepCompetition:
    return DeepCompetition(alias="t", betano_url="https://b/list/", label="T")


def _ssr_pages() -> dict[str, str]:
    return {
        "https://b/list/": '<a href="/odds/egito-ira/83963790/">e</a>',
        "https://www.betano.bet.br/odds/egito-ira/83963790/": SSR_EVENT_HTML,
    }


def test_fetch_deep_merges_shots_into_ssr_event():
    events = fetch_betano_events_deep(
        _deep_comp(),
        html_getter=lambda url: _ssr_pages()[url],
        json_getter=lambda url: DEEP_SHOTS_JSON,
    )
    assert len(events) == 1
    ids = sorted(str(m["id"]) for m in events[0]["markets"])
    assert ids == ["34", "4159"]  # SSR corners + deep shots


def test_fetch_deep_dedupes_by_market_id():
    dup_json = _json.dumps(
        {
            "data": {
                "event": {
                    "markets": [
                        {"id": 34, "name": "dup corners"},
                        {"id": 4159, "name": "Total de chutes"},
                    ]
                }
            }
        }
    )
    events = fetch_betano_events_deep(
        _deep_comp(),
        html_getter=lambda url: _ssr_pages()[url],
        json_getter=lambda url: dup_json,
    )
    ids = sorted(str(m["id"]) for m in events[0]["markets"])
    assert ids == ["34", "4159"]  # SSR's 34 kept, deep's duplicate 34 dropped


def test_fetch_deep_isolates_per_event_json_failure():
    def boom(url: str) -> str:
        raise RuntimeError("cloudflare")

    events = fetch_betano_events_deep(
        _deep_comp(),
        html_getter=lambda url: _ssr_pages()[url],
        json_getter=boom,
    )
    assert len(events) == 1
    ids = sorted(str(m["id"]) for m in events[0]["markets"])
    assert ids == ["34"]  # SSR event preserved, no shots, no crash


def test_fetch_deep_falls_back_to_id_when_url_missing():
    # SSR event lacks "url" but has "id"; the bt=6 API is slug-agnostic, so
    # enrich must still fire using an id-derived path (never silently no-op).
    html = (
        'x{"event":{"id":1,"name":"A - B","startTime":1750000000000,'
        '"markets":[{"id":34,"name":"Total de Escanteios",'
        '"selections":[{"id":1,"name":"Mais de 9.5","handicap":"9.5","price":"2.0"}]}]}}y'
    )
    called: list[str] = []

    def tracking_json(url: str) -> str:
        called.append(url)
        return DEEP_SHOTS_JSON

    events = fetch_betano_events_deep(
        _deep_comp(),
        html_getter=lambda url: {
            "https://b/list/": '<a href="/odds/a-b/000001/">x</a>',
            "https://www.betano.bet.br/odds/a-b/000001/": html,
        }[url],
        json_getter=tracking_json,
    )
    assert len(called) == 1
    assert "/api/odds/event/1/?bt=6&req=s,stnf,c" in called[0]
    ids = sorted(str(m["id"]) for m in events[0]["markets"])
    assert ids == ["34", "4159"]


def test_fetch_deep_skips_enrich_when_url_and_id_missing():
    # No url AND no id -> cannot build a path -> enrich skipped, SSR preserved.
    html = (
        'x{"event":{"name":"A - B","startTime":1750000000000,'
        '"markets":[{"id":34,"name":"Total de Escanteios",'
        '"selections":[{"id":1,"name":"Mais de 9.5","handicap":"9.5","price":"2.0"}]}]}}y'
    )
    called: list[str] = []

    def tracking_json(url: str) -> str:
        called.append(url)
        return DEEP_SHOTS_JSON

    events = fetch_betano_events_deep(
        _deep_comp(),
        html_getter=lambda url: {
            "https://b/list/": '<a href="/odds/a-b/000001/">x</a>',
            "https://www.betano.bet.br/odds/a-b/000001/": html,
        }[url],
        json_getter=tracking_json,
    )
    assert called == []
    assert [str(m["id"]) for m in events[0]["markets"]] == ["34"]
