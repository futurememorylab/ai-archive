from pathlib import Path

import pytest

from backend.app.services.media_store_map import MediaStoreMap


FIXTURE = [
    {
        "ID": 361803,
        "name": "Pragafilm",
        "paths": [
            {
                "pathOrder": 2,
                "path": "/Volumes/ARECA/ARCHIV_SOUKROME_FILMOVE_HISTORIE",
                "pathType": {"mediaType": "hires", "target": None},
            },
            {
                "pathOrder": 3,
                "path": "/Volumes/ARECA2/ARCHIV_SOUKROME_FILMOVE_HISTORIE",
                "pathType": {"mediaType": "hires", "target": None},
            },
            {
                "pathOrder": 2,
                "path": "/Volumes/ARECA/CatDV_Proxy",
                "pathType": {"mediaType": "proxy", "target": "web"},
            },
            {
                "pathOrder": 3,
                "path": "/Volumes/ARECA2/CatDV_Proxy",
                "pathType": {"mediaType": "proxy", "target": "web"},
            },
            # Decoy: client-target proxy must be ignored — we only care
            # about "target": "web".
            {
                "pathOrder": 4,
                "path": "/Volumes/ARECA/CatDV_DesktopProxy",
                "pathType": {"mediaType": "proxy", "target": "client"},
            },
        ],
    }
]


def test_parses_two_prefix_rules_from_fixture():
    m = MediaStoreMap.from_json(FIXTURE)
    assert m.rules == [
        ("/Volumes/ARECA/ARCHIV_SOUKROME_FILMOVE_HISTORIE",
         "/Volumes/ARECA/CatDV_Proxy"),
        ("/Volumes/ARECA2/ARCHIV_SOUKROME_FILMOVE_HISTORIE",
         "/Volumes/ARECA2/CatDV_Proxy"),
    ]


def test_resolve_swaps_prefix_keeps_relative_path():
    m = MediaStoreMap.from_json(FIXTURE)
    hires = "/Volumes/ARECA/ARCHIV_SOUKROME_FILMOVE_HISTORIE/ABRAMCUKOVA Anna/ABRAMCUKOVA Anna 01.mov"
    assert m.resolve_proxy(hires) == Path(
        "/Volumes/ARECA/CatDV_Proxy/ABRAMCUKOVA Anna/ABRAMCUKOVA Anna 01.mov"
    )


def test_resolve_second_root():
    m = MediaStoreMap.from_json(FIXTURE)
    hires = "/Volumes/ARECA2/ARCHIV_SOUKROME_FILMOVE_HISTORIE/foo/bar.mov"
    assert m.resolve_proxy(hires) == Path("/Volumes/ARECA2/CatDV_Proxy/foo/bar.mov")


def test_resolve_returns_none_when_no_prefix_matches():
    m = MediaStoreMap.from_json(FIXTURE)
    assert m.resolve_proxy("/some/other/place/file.mov") is None


def test_unpaired_hires_root_is_dropped():
    # If a hires path has pathOrder 5 but no matching proxy path with
    # pathOrder 5, the rule is silently dropped — we only emit paired
    # rules.
    fixture = [
        {
            "ID": 1,
            "name": "X",
            "paths": [
                {"pathOrder": 5, "path": "/a", "pathType": {"mediaType": "hires", "target": None}},
                {"pathOrder": 6, "path": "/b", "pathType": {"mediaType": "proxy", "target": "web"}},
            ],
        }
    ]
    m = MediaStoreMap.from_json(fixture)
    assert m.rules == []


def test_empty_response_yields_empty_map():
    m = MediaStoreMap.from_json([])
    assert m.rules == []
    assert m.resolve_proxy("/anything") is None
