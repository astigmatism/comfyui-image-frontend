"""The published catalog as the control panel sees it (docs/llm-router-contract.md §4, §5).

The panel offers the models the router is publishing right now, and an account's chosen service
is honoured or explained against that same list. Everything here runs over the small projection
that is stored and served — service IDs, labels and capability flags — never over a document that
still carries canonical model IDs.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from app.services.llm_router import (
    CATALOG_LIMIT,
    PIN_ABSENT,
    PIN_OFFLINE,
    PIN_OK,
    PIN_UNSUPPORTED,
    UNAVAILABLE,
    WAIT,
    catalog_models,
    catalog_view,
    entry_has_features,
    find_catalog_entry,
    model_for,
    pick_from_catalog,
    pin_status,
    resolve_from_catalog,
)
from tests.router_fixtures import (
    DAYTIME_PAIRED_ID,
    NIGHTTIME_ID,
    paired_document,
    router_document,
    router_model,
    solo_document,
)

CANONICAL_IDS = (DAYTIME_PAIRED_ID, NIGHTTIME_ID, "fake-flash-next-solo-mtp3")


def _catalog(document: dict[str, Any]) -> dict[str, Any]:
    catalog = catalog_view(document)
    assert catalog is not None
    return catalog


def _services(catalog: dict[str, Any]) -> list[str]:
    return [entry["service"] for entry in catalog["models"]]


def test_the_catalog_lists_every_published_service_best_capable_first() -> None:
    catalog = _catalog(paired_document())

    assert catalog["object"] == "router_catalog"
    assert catalog["configuration_id"] == "fake-paired"
    assert catalog["exclusive"] is False
    assert catalog["accepting_requests"] is True
    assert catalog["draining"] is False
    assert catalog["maintenance"] is False
    assert catalog["revision"] == paired_document()["revision"]
    # Daytime scores higher, so it is the first line a person sees.
    assert _services(catalog) == ["daytime", "nighttime"]

    daytime, nighttime = catalog["models"]
    assert daytime["label"] == "Fake Daytime"
    assert daytime["nsfw"] is False
    assert daytime["capability_score"] == 68.3
    assert daytime["context_window"] == 163_840
    assert daytime["available"] is True
    assert daytime["offline_reason"] is None
    assert daytime["vision"] is True and daytime["thinking"] is True and daytime["tools"] is True
    # The alias the router also answers for, minus the service ID itself.
    assert daytime["aliases"] == ["local-active"]
    assert nighttime["service"] == "nighttime"
    assert nighttime["nsfw"] is True


def test_the_catalog_never_carries_a_canonical_model_id() -> None:
    """Only service IDs are stored and served; a canonical ID is the router's to resolve (§3)."""

    catalog = json.dumps(_catalog(paired_document()), sort_keys=True)
    for canonical in CANONICAL_IDS:
        assert canonical not in catalog
    assert '"ids"' not in catalog
    assert "match" not in json.loads(catalog)["models"][0]


def test_a_service_the_router_stopped_is_listed_as_unavailable_with_its_reason() -> None:
    document = paired_document(unavailable=("nighttime",))
    entry = next(entry for entry in _catalog(document)["models"] if entry["service"] == "nighttime")

    assert entry["available"] is False
    assert entry["offline_reason"] == "unavailable"
    # Still listed, because a panel that hides it cannot explain why the chosen model changed.
    assert _services(_catalog(document)) == ["daytime", "nighttime"]

    offline = _catalog(solo_document())
    assert _services(offline) == ["daytime"]
    assert pin_status(offline, "nighttime") == PIN_ABSENT


def test_two_models_publishing_one_service_id_answer_as_one_line(caplog) -> None:
    models = [
        router_model("daytime", canonical="first-canonical", score=50.0, context_window=1000),
        router_model("daytime", canonical=DAYTIME_PAIRED_ID, score=72.0, context_window=2000),
        router_model("nighttime", score=60.0),
    ]
    with caplog.at_level(logging.WARNING, logger="app.services.llm_router"):
        catalog = _catalog(router_document(models))

    assert _services(catalog) == ["daytime", "nighttime"]
    daytime = catalog["models"][0]
    assert daytime["capability_score"] == 72.0  # the better-ranked entry answers for the service
    assert daytime["label"] == "Fake Daytime"
    assert [record.message for record in caplog.records] == ["llm_router_duplicate_service"]
    warning = caplog.records[0]
    assert warning.router_service == "daytime"  # type: ignore[attr-defined]
    assert warning.router_entries == 2  # type: ignore[attr-defined]
    assert DAYTIME_PAIRED_ID not in json.dumps(catalog)


def test_a_catalog_larger_than_the_panel_shows_is_say_so_truncated() -> None:
    models = [
        router_model(f"model-{index}", score=float(index)) for index in range(CATALOG_LIMIT + 5)
    ]
    catalog = _catalog(router_document(models, configuration_id="many-models"))

    assert len(catalog["models"]) == CATALOG_LIMIT
    assert catalog["truncated"] is True
    assert _services(catalog)[0] == "model-36"  # the highest score leads
    assert _catalog(paired_document())["truncated"] is False


def test_selection_by_name_and_by_capability_read_the_same_catalog_either_way() -> None:
    """A stored projection answers exactly as the live document does, with no router call."""

    document = paired_document()
    catalog = _catalog(document)

    assert resolve_from_catalog(catalog, "nighttime") == resolve_from_catalog(document, "nighttime")
    assert pick_from_catalog(catalog, nsfw=True) == "nighttime"
    assert pick_from_catalog(document, nsfw=True) == "nighttime"
    assert pick_from_catalog(catalog) == "daytime"
    assert pick_from_catalog(catalog, nsfw=True, require=("vision",)) == "nighttime"
    assert pick_from_catalog(catalog, nsfw=True, require=("vision",), exclude={"nighttime"}) == (
        UNAVAILABLE
    )
    # An alias the router publishes selects the same model as the service ID.
    assert resolve_from_catalog(catalog, "local-active") == "local-active"
    assert model_for(catalog, "local-active") is not None
    assert model_for(document, "local-active") is not None
    assert find_catalog_entry(catalog, "nighttime") is not None
    assert find_catalog_entry(catalog, "no-such-service") is None
    assert catalog_models(catalog) and catalog_models(None) == []


def test_a_router_that_is_draining_or_maintaining_is_a_wait_not_an_alternative_model(
    caplog,
) -> None:
    """Contract §5: while the router drains, wait — do not pick another model (§5)."""

    for document in (paired_document(draining=True), paired_document(maintenance=True)):
        catalog = _catalog(document)
        assert catalog["accepting_requests"] is False
        assert pick_from_catalog(catalog, fallback_any=True) == WAIT
        assert resolve_from_catalog(catalog, "nighttime", ["daytime"]) == WAIT
        assert pick_from_catalog(None) == UNAVAILABLE
        assert resolve_from_catalog(None, "nighttime") == UNAVAILABLE


def test_chosen_model_status_is_explained_against_the_published_catalog() -> None:
    catalog = _catalog(paired_document())

    assert pin_status(catalog, "nighttime") == PIN_OK
    assert pin_status(catalog, "local-active") == PIN_OK  # an alias selects the same model
    assert pin_status(catalog, "nighttime", require=("vision",)) == PIN_OK
    assert pin_status(catalog, None) == PIN_ABSENT
    assert pin_status(catalog, "unpublished-model") == PIN_ABSENT
    assert pin_status(None, "nighttime") == PIN_ABSENT

    no_vision = _catalog(paired_document(vision=False))
    assert pin_status(no_vision, "nighttime", require=("vision",)) == PIN_UNSUPPORTED
    assert pin_status(no_vision, "nighttime") == PIN_OK

    stopped = _catalog(paired_document(unavailable=("nighttime",)))
    assert pin_status(stopped, "nighttime") == PIN_OFFLINE
    assert pin_status(stopped, "daytime") == PIN_OK

    entry = find_catalog_entry(no_vision, "nighttime")
    assert entry is not None
    assert entry_has_features(entry, ()) is True
    assert entry_has_features(entry, ("vision",)) is False
    assert entry_has_features(entry, ("reasoning", "tools")) is True
    # The feature names are the contract's; one nobody publishes is a bug, not a miss.
    with pytest.raises(ValueError, match="unknown required feature: dreaming"):
        pin_status(catalog, "nighttime", require=("dreaming",))


@pytest.mark.parametrize("features", [("vision",), ("tools",), ("reasoning",)])
def test_every_feature_the_panel_labels_is_checked_from_the_catalog(features: tuple[str]) -> None:
    entry = {
        "vision": features[0] == "vision",
        "tools": features[0] == "tools",
        "thinking": features[0] == "reasoning",
    }

    assert entry_has_features(entry, features) is True
    assert entry_has_features(entry, ("vision", "tools", "reasoning")) is False
