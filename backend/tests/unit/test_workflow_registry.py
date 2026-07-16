from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from app.domain.publication import (
    EDITABLE_WORKFLOW_DRIFT_WARNING,
    FROZEN_API_DRIFT_WARNING,
    source_key_for,
)
from app.errors import AppError
from app.models import Base, ServiceHealth, WorkflowProfile, WorkflowState
from app.services.comfyui import ComfyCapabilities
from app.services.workflow_registry import WorkflowRegistry
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from tests.publication_fixtures import (
    GENERIC_PUBLICATION_ID,
    KREA_PUBLICATION_ID,
    PublicationBundle,
    build_publication_bundle,
    build_publication_files,
    exact_json_bytes,
    object_info_fixture,
    sha256_bytes,
)


class FixturePublicationAdapter:
    def __init__(
        self,
        files: dict[str, bytes] | None = None,
        *,
        object_info: dict[str, Any] | None = None,
    ) -> None:
        self.settings = SimpleNamespace(
            comfyui_instance_id="test-instance",
            comfyui_manifest_max_bytes=1024 * 1024,
            comfyui_workflow_max_bytes=1024 * 1024,
            comfyui_api_max_bytes=1024 * 1024,
        )
        self.files = files if files is not None else build_publication_files()
        self.object_info = object_info if object_info is not None else object_info_fixture()
        self.fetch_failures: dict[str, AppError] = {}
        self.probe_error: AppError | None = None
        self.list_error: AppError | None = None

    async def probe(self) -> ComfyCapabilities:
        await asyncio.sleep(0)
        if self.probe_error:
            raise self.probe_error
        return ComfyCapabilities(
            object_info=self.object_info,
            workflow_list_route="v2_query:/v2/userdata",
            workflow_get_route="encoded_segment:/userdata/{path}",
            system={},
            assets=[],
            capabilities={"workflow_userdata": True},
        )

    async def list_workflow_files(self) -> list[str]:
        await asyncio.sleep(0)
        if self.list_error:
            raise self.list_error
        return sorted(self.files)

    async def get_userdata_file(self, path: str, *, maximum_bytes: int) -> bytes:
        await asyncio.sleep(0)
        if failure := self.fetch_failures.get(path):
            raise failure
        try:
            value = self.files[path]
        except KeyError as exc:
            raise AppError("userdata_file_not_found", "Fixture artifact is absent.") from exc
        if len(value) > maximum_bytes:
            raise AppError("comfyui_response_too_large", "Fixture artifact is too large.")
        return value


def make_registry(
    adapter: FixturePublicationAdapter,
) -> tuple[WorkflowRegistry, sessionmaker[Session], Any]:
    # Registry validation/commit work intentionally crosses onto a worker thread. Keep this
    # in-memory fixture on one thread-safe connection so every Session sees the same schema.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False, class_=Session)
    return WorkflowRegistry(session_factory, adapter), session_factory, engine  # type: ignore[arg-type]


def test_refresh_discovers_two_output_aware_publications() -> None:
    adapter = FixturePublicationAdapter()
    registry, session_factory, engine = make_registry(adapter)

    diagnostics = asyncio.run(registry.refresh())

    assert [(item.basename, item.accepted, item.code) for item in diagnostics] == [
        ("Generic Landscape", True, "ready"),
        ("Krea 2 NSFW V4", True, "ready"),
    ]
    with session_factory() as session:
        profiles = registry.list_current(session)
        assert [profile.display_name for profile in profiles] == [
            "Generic Landscape",
            "Krea 2 NSFW V4",
        ]
        assert {profile.publication_id for profile in profiles} == {
            GENERIC_PUBLICATION_ID,
            KREA_PUBLICATION_ID,
        }
        generic = next(
            profile for profile in profiles if profile.display_name == "Generic Landscape"
        )
        krea = next(profile for profile in profiles if profile.display_name.startswith("Krea"))
        assert all(value["type"] != "choice" for value in generic.resolved_contract_json["inputs"])
        assert [value["id"] for value in krea.resolved_contract_json["inputs"][-2:]] == [
            "lora",
            "lora_strength",
        ]
        assert krea.resolved_contract_json["inputs"][-2]["choices"][1] == {
            "value": "knp_v3_1",
            "label": "KNP v3.1",
            "default_strength": 0.5,
        }
        assert krea.warnings_json == []
        assert [output["id"] for output in krea.resolved_contract_json["outputs"]] == [
            "base",
            "second_pass",
            "final",
        ]
        assert krea.readiness == "ready"
        health = session.get(ServiceHealth, "comfyui")
        assert health is not None
        assert health.available is True
        assert health.capabilities_json["ready_sources"] == 2
    engine.dispose()


def test_known_publication_tracks_editable_workflow_drift_as_current_warning_metadata() -> None:
    original = build_publication_bundle("krea")
    adapter = FixturePublicationAdapter(dict(original.files))
    registry, session_factory, engine = make_registry(adapter)
    assert [item.code for item in asyncio.run(registry.refresh())] == ["ready"]
    source_key = source_key_for("test-instance", original.manifest()["source_id"])

    with session_factory() as session:
        original_profile = registry.get_current(session, source_key)
        original_profile_id = original_profile.id
        original_ui_snapshot = original_profile.source_ui_json

    edited_workflow = original.workflow()
    edited_workflow["nodes"][0]["widgets_values"][0] = "locally edited prompt"
    drifted = PublicationBundle(
        **{
            **original.__dict__,
            "workflow_bytes": exact_json_bytes(edited_workflow),
        }
    )
    adapter.files = dict(drifted.files)

    diagnostics = asyncio.run(registry.refresh())

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.accepted is True
    assert diagnostic.code == "ready_with_warnings"
    assert diagnostic.details_json["warnings"] == [EDITABLE_WORKFLOW_DRIFT_WARNING]
    assert diagnostic.details_json["workflow_sha256"] == original.manifest()["workflow"]["sha256"]
    assert (
        diagnostic.details_json["observed_workflow_sha256"]
        != diagnostic.details_json["workflow_sha256"]
    )
    assert diagnostic.details_json["editable_workflow_drifted"] is True
    with session_factory() as session:
        drifted_profile = registry.get_current(session, source_key)
        assert drifted_profile.id == original_profile_id
        assert drifted_profile.publication_id == KREA_PUBLICATION_ID
        assert drifted_profile.readiness == "ready_with_warnings"
        assert drifted_profile.warnings_json == [EDITABLE_WORKFLOW_DRIFT_WARNING]
        assert drifted_profile.source_ui_json == original_ui_snapshot
        assert drifted_profile.source_api_json == original.api()
        assert drifted_profile.manifest_json == original.manifest()
        assert (
            drifted_profile.runtime_snapshot_json["stored_editable_workflow_matches_publication"]
            is True
        )

    adapter.files = dict(original.files)
    diagnostics = asyncio.run(registry.refresh())

    assert [item.code for item in diagnostics] == ["ready"]
    with session_factory() as session:
        restored_profile = registry.get_current(session, source_key)
        assert restored_profile.id == original_profile_id
        assert restored_profile.readiness == "ready"
        assert restored_profile.warnings_json == []
        assert restored_profile.source_ui_json == original_ui_snapshot
    engine.dispose()


def test_initial_editable_drift_tracks_the_observed_snapshot_separately() -> None:
    original = build_publication_bundle("krea")
    edited_workflow = original.workflow()
    edited_workflow["nodes"][0]["widgets_values"][0] = "unpublished editable prompt"
    drifted = PublicationBundle(
        **{
            **original.__dict__,
            "workflow_bytes": exact_json_bytes(edited_workflow),
        }
    )
    adapter = FixturePublicationAdapter(dict(drifted.files))
    registry, session_factory, engine = make_registry(adapter)

    diagnostics = asyncio.run(registry.refresh())

    assert [item.code for item in diagnostics] == ["ready_with_warnings"]
    diagnostic = diagnostics[0]
    source_key = source_key_for("test-instance", original.manifest()["source_id"])
    with session_factory() as session:
        profile = registry.get_current(session, source_key)
        assert profile.ui_graph_sha256 == original.manifest()["workflow"]["sha256"]
        assert (
            profile.runtime_snapshot_json["stored_editable_workflow_sha256"]
            == (diagnostic.details_json["observed_workflow_sha256"])
        )
        assert (
            profile.runtime_snapshot_json["stored_editable_workflow_matches_publication"] is False
        )
        assert profile.source_ui_json["nodes"][0]["widgets_values"][0] == (
            "unpublished editable prompt"
        )
        assert profile.source_api_json == original.api()
    engine.dispose()


def test_missing_choice_dependency_does_not_hide_nonchoice_source() -> None:
    object_info = object_info_fixture()
    object_info.pop("CIFChoiceParameter")
    adapter = FixturePublicationAdapter(object_info=object_info)
    registry, session_factory, engine = make_registry(adapter)

    diagnostics = asyncio.run(registry.refresh())

    assert [(item.basename, item.code) for item in diagnostics] == [
        ("Generic Landscape", "ready"),
        ("Krea 2 NSFW V4", "dependency_missing"),
    ]
    with session_factory() as session:
        assert [profile.display_name for profile in registry.list_current(session)] == [
            "Generic Landscape"
        ]
        dependency_diagnostic = next(
            item for item in registry.diagnostics(session) if item.code == "dependency_missing"
        )
        assert dependency_diagnostic.details_json["missing_class_types"] == ["CIFChoiceParameter"]
    engine.dispose()


def test_invalid_candidate_and_fetch_failure_do_not_hide_other_ready_sources() -> None:
    files = build_publication_files()
    files.update(
        {
            "workflows/comfyui-image-frontend/Invalid.api.json": b"{}",
            "workflows/comfyui-image-frontend/Invalid.interface.json": b"{}",
            "workflows/comfyui-image-frontend/Invalid.json": b"{}",
        }
    )
    generic = build_publication_bundle("generic")
    adapter = FixturePublicationAdapter(files)
    adapter.fetch_failures[generic.api_path] = AppError(
        "userdata_fetch_failed", "Fixture proxy refused this artifact."
    )
    registry, session_factory, engine = make_registry(adapter)

    diagnostics = asyncio.run(registry.refresh())

    assert {item.code for item in diagnostics} == {
        "ready",
        "manifest_invalid",
        "api_fetch_failed",
    }
    with session_factory() as session:
        profiles = registry.list_current(session)
        assert [profile.display_name for profile in profiles] == ["Krea 2 NSFW V4"]
    engine.dispose()


def test_pathological_and_invalid_unicode_candidates_do_not_abort_refresh() -> None:
    generic = build_publication_bundle("generic")
    files = dict(generic.files)
    files["workflows/comfyui-image-frontend/Deep.interface.json"] = (
        b'{"ignored":' + (b"[" * 200) + b"0" + (b"]" * 200) + b"}"
    )
    files["workflows/comfyui-image-frontend/Invalid-\ud800.interface.json"] = b"{}"
    adapter = FixturePublicationAdapter(files)
    registry, session_factory, engine = make_registry(adapter)

    diagnostics = asyncio.run(registry.refresh())

    assert [(item.basename, item.accepted, item.code) for item in diagnostics] == [
        ("invalid-manifest-path", False, "manifest_invalid"),
        ("Deep", False, "manifest_invalid"),
        ("Generic Landscape", True, "ready"),
    ]
    with session_factory() as session:
        assert [profile.display_name for profile in registry.list_current(session)] == [
            "Generic Landscape"
        ]
    engine.dispose()


def test_missing_publisher_dependency_marks_all_output_aware_sources_unavailable() -> None:
    object_info = object_info_fixture()
    object_info.pop("CIFPublishImage")
    adapter = FixturePublicationAdapter(object_info=object_info)
    registry, session_factory, engine = make_registry(adapter)

    diagnostics = asyncio.run(registry.refresh())

    assert {(item.basename, item.code) for item in diagnostics} == {
        ("Generic Landscape", "dependency_missing"),
        ("Krea 2 NSFW V4", "dependency_missing"),
    }
    with session_factory() as session:
        assert registry.list_current(session) == []
        unavailable = registry.unavailable_catalog_entries(session)
        assert {item["display_name"] for item in unavailable} == {
            "Generic Landscape",
            "Krea 2 NSFW V4",
        }
        assert all(item["available"] is False for item in unavailable)
        assert all(item["readiness"] == "dependency_missing" for item in unavailable)
        assert all(
            item["message"] == "Required ComfyUI node classes are unavailable for this source."
            for item in unavailable
        )
        assert "missing_class_types" not in str(unavailable)
        assert "source_id" not in str(unavailable)
        dependency_diagnostics = [
            item for item in registry.diagnostics(session) if item.code == "dependency_missing"
        ]
        assert len(dependency_diagnostics) == 2
        assert all(
            item.details_json["missing_class_types"] == ["CIFPublishImage"]
            for item in dependency_diagnostics
        )
    engine.dispose()


def test_dependency_loss_marks_accepted_revision_unavailable_until_runtime_recovers() -> None:
    generic = build_publication_bundle("generic")
    adapter = FixturePublicationAdapter(dict(generic.files))
    registry, session_factory, engine = make_registry(adapter)
    asyncio.run(registry.refresh())
    source_key = source_key_for("test-instance", generic.manifest()["source_id"])

    adapter.object_info.pop("CIFPublishImage")
    diagnostics = asyncio.run(registry.refresh())

    assert [item.code for item in diagnostics] == ["dependency_missing"]
    with session_factory() as session:
        assert registry.dependency_unavailable_source_keys(session) == {source_key}
        with pytest.raises(AppError) as exc:
            registry.get_current(session, source_key)
        assert exc.value.code == "source_dependency_missing"

    adapter.object_info["CIFPublishImage"] = {"input": {"required": {"images": ["IMAGE"]}}}
    diagnostics = asyncio.run(registry.refresh())
    assert [item.code for item in diagnostics] == ["ready"]
    with session_factory() as session:
        assert registry.dependency_unavailable_source_keys(session) == set()
        assert registry.get_current(session, source_key).publication_id == GENERIC_PUBLICATION_ID
    engine.dispose()


def test_bad_republication_retains_prior_revision_then_valid_revision_switches_atomically() -> None:
    original = build_publication_bundle("krea")
    adapter = FixturePublicationAdapter(dict(original.files))
    registry, session_factory, engine = make_registry(adapter)
    asyncio.run(registry.refresh())
    source_key = source_key_for("test-instance", original.manifest()["source_id"])

    with session_factory() as session:
        previous = registry.get_current(session, source_key)
        previous_id = previous.id
        assert previous.publication_id == KREA_PUBLICATION_ID

    rejected = build_publication_bundle(
        "krea",
        publication_id="33333333-3333-4333-8333-333333333333",
        mutate_manifest=lambda manifest: manifest["api"].__setitem__("node_count", 999),
    )
    adapter.files = dict(rejected.files)
    diagnostics = asyncio.run(registry.refresh())
    assert [item.code for item in diagnostics] == ["api_node_count_mismatch"]
    with session_factory() as session:
        retained = registry.get_current(session, source_key)
        assert retained.id == previous_id
        assert retained.publication_id == KREA_PUBLICATION_ID

    accepted = build_publication_bundle(
        "krea", publication_id="44444444-4444-4444-8444-444444444444"
    )
    adapter.files = dict(accepted.files)
    diagnostics = asyncio.run(registry.refresh())
    assert [item.code for item in diagnostics] == ["ready"]
    with session_factory() as session:
        current = registry.get_current(session, source_key)
        assert current.id != previous_id
        assert current.publication_id == "44444444-4444-4444-8444-444444444444"
        revisions = list(
            session.scalars(
                select(WorkflowProfile)
                .where(WorkflowProfile.source_key == source_key)
                .order_by(WorkflowProfile.publication_id)
            )
        )
        assert [(item.publication_id, item.is_current, item.state) for item in revisions] == [
            (KREA_PUBLICATION_ID, False, WorkflowState.STALE),
            ("44444444-4444-4444-8444-444444444444", True, WorkflowState.VALID),
        ]
    engine.dispose()


def test_api_hash_drift_publishes_an_observed_graph_revision_with_a_warning() -> None:
    original = build_publication_bundle("krea")
    adapter = FixturePublicationAdapter(dict(original.files))
    registry, session_factory, engine = make_registry(adapter)
    asyncio.run(registry.refresh())
    source_key = source_key_for("test-instance", original.manifest()["source_id"])

    with session_factory() as session:
        original_id = registry.get_current(session, source_key).id

    drifted = PublicationBundle(**{**original.__dict__, "api_bytes": original.api_bytes + b"\n"})
    adapter.files = dict(drifted.files)
    diagnostics = asyncio.run(registry.refresh())

    assert [item.code for item in diagnostics] == ["ready_with_warnings"]
    details = diagnostics[0].details_json
    assert details["warnings"] == [FROZEN_API_DRIFT_WARNING]
    assert details["api_drifted"] is True
    assert details["recorded_api_sha256"] == original.manifest()["api"]["sha256"]
    assert details["api_sha256"] == sha256_bytes(drifted.api_bytes)
    with session_factory() as session:
        current = registry.get_current(session, source_key)
        assert current.id != original_id
        assert current.api_graph_sha256 == sha256_bytes(drifted.api_bytes)
        assert current.source_api_json == original.api()
        assert current.warnings_json == [FROZEN_API_DRIFT_WARNING]
        assert (
            current.runtime_snapshot_json["recorded_api_sha256"]
            == (original.manifest()["api"]["sha256"])
        )
        assert current.runtime_snapshot_json["stored_api_matches_publication"] is False
    engine.dispose()


def test_transport_outage_retains_cached_current_revision() -> None:
    adapter = FixturePublicationAdapter(dict(build_publication_bundle("krea").files))
    registry, session_factory, engine = make_registry(adapter)
    asyncio.run(registry.refresh())
    adapter.probe_error = AppError("comfyui_unavailable", "Fixture ComfyUI is offline.")

    diagnostics = asyncio.run(registry.refresh())

    assert [item.code for item in diagnostics] == ["server_unreachable"]
    assert diagnostics[0].details_json == {"cached_sources": 1}
    with session_factory() as session:
        assert len(registry.list_current(session)) == 1
        health = session.get(ServiceHealth, "comfyui")
        assert health is not None
        assert health.available is False
        assert health.capabilities_json["catalog_state"] == "cached_offline"
    engine.dispose()


def test_concurrent_identical_refreshes_leave_one_immutable_current_revision() -> None:
    adapter = FixturePublicationAdapter(dict(build_publication_bundle("krea").files))
    registry, session_factory, engine = make_registry(adapter)

    async def refresh_twice() -> None:
        first, second = await asyncio.gather(registry.refresh(), registry.refresh())
        assert first[0].accepted is True
        assert second[0].accepted is True

    asyncio.run(refresh_twice())

    with session_factory() as session:
        profiles = list(session.scalars(select(WorkflowProfile)))
        assert len(profiles) == 1
        assert profiles[0].is_current is True
        assert profiles[0].publication_id == KREA_PUBLICATION_ID
    engine.dispose()
