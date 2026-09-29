from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.models import (
    Base,
    ComfyUIInstanceHealth,
    Generation,
    GenerationStatus,
    ServiceHealth,
    WorkflowProfile,
    WorkflowState,
)
from app.services.worker_pool import ImageWorkerPool
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

PRIMARY = "primary"
WORKER = "worker-2"
OUTSIDE = "promptgen"


def _pool(*members: str, text: str | None = OUTSIDE) -> ImageWorkerPool:
    configs = {
        instance_id: SimpleNamespace(id=instance_id, label=instance_id.title(), concurrency=1)
        for instance_id in (PRIMARY, WORKER, OUTSIDE)
    }
    instances = SimpleNamespace(
        configs=list(configs.values()),
        config=configs.get,
        default_id=PRIMARY,
        image_pool_ids=(PRIMARY, *members),
        settings=SimpleNamespace(comfyui_text_instance_id=text),
    )
    return ImageWorkerPool(instances)


def _factory(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(f"sqlite:///{tmp_path / 'pool.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _profile(instance_id: str, *, api: str = "a" * 64, source: str = "workflows/source.json"):
    return WorkflowProfile(
        identity_key=f"{instance_id}:{api}:{source}",
        basename="source",
        workflow_id=f"{instance_id}-key",
        display_name="Source",
        workflow_version="publication-1",
        contract_schema_version="interface/v1",
        adapter_version="1",
        ui_graph_sha256="u" * 64,
        api_graph_sha256=api,
        contract_sha256="m" * 64,
        source_ui_json={},
        source_api_json={},
        manifest_json={},
        resolved_contract_json={},
        runtime_snapshot_json={},
        instance_id=instance_id,
        source_key=f"{instance_id}-key",
        source_id=source,
        publication_id="publication-1",
        manifest_sha256="m" * 64,
        state=WorkflowState.VALID,
        is_current=True,
    )


def _generation(profile: WorkflowProfile, *, status=GenerationStatus.QUEUED, instance=None):
    return Generation(
        owner_id="owner-a",
        status=status,
        queue_seq=1,
        comfyui_instance_id=instance,
        comfyui_instance_label=instance,
        workflow_profile_id=profile.id,
        workflow_id=str(profile.workflow_id),
        workflow_display_name="Source",
        workflow_version=str(profile.workflow_version),
        contract_schema_version="interface/v1",
        adapter_version="1",
        ui_graph_sha256=str(profile.ui_graph_sha256),
        api_graph_sha256=str(profile.api_graph_sha256),
        contract_sha256=str(profile.contract_sha256),
        resolved_contract_json={},
        requested_controls_json={},
        effective_controls_json={},
        final_prompt="prompt",
        compiled_graph_json={},
        compiled_graph_sha256="c" * 64,
    )


def _claimable(session: Session, pool: ImageWorkerPool, worker: str) -> set[str]:
    return set(
        session.scalars(
            select(Generation.id).where(
                Generation.status == GenerationStatus.QUEUED,
                pool.claimable_clause(worker),
            )
        )
    )


def test_a_worker_may_only_claim_revisions_it_carries(tmp_path) -> None:
    factory = _factory(tmp_path)
    pool = _pool(WORKER)
    with factory() as session:
        shared = _profile(PRIMARY)
        replica = _profile(WORKER)
        drifted_source = _profile(PRIMARY, source="workflows/other.json")
        session.add_all([shared, replica, drifted_source])
        session.flush()
        mirrored = _generation(shared)
        exclusive = _generation(drifted_source)
        session.add_all([mirrored, exclusive])
        session.commit()

        # Both workers carry the mirrored publication; only the primary has the
        # source that was never published to the worker.
        assert _claimable(session, pool, PRIMARY) == {mirrored.id, exclusive.id}
        assert _claimable(session, pool, WORKER) == {mirrored.id}


def test_a_republished_source_does_not_strand_an_accepted_generation(tmp_path) -> None:
    factory = _factory(tmp_path)
    pool = _pool(WORKER)
    with factory() as session:
        accepted = _profile(PRIMARY)
        replica = _profile(WORKER)
        session.add_all([accepted, replica])
        session.flush()
        generation = _generation(accepted)
        session.add(generation)
        # Republication retires the accepted revision on both runtimes; the
        # immutable compiled graph must still dispatch.
        accepted.is_current = False
        accepted.state = WorkflowState.STALE
        replica.is_current = False
        replica.state = WorkflowState.STALE
        session.commit()

        assert _claimable(session, pool, PRIMARY) == {generation.id}
        assert _claimable(session, pool, WORKER) == {generation.id}


def test_a_pre_publication_row_stays_with_its_accepting_catalog(tmp_path) -> None:
    factory = _factory(tmp_path)
    pool = _pool(WORKER)
    with factory() as session:
        legacy = _profile(PRIMARY)
        legacy.source_id = None
        legacy.publication_id = None
        replica = _profile(WORKER)
        session.add_all([legacy, replica])
        session.flush()
        generation = _generation(legacy)
        session.add(generation)
        session.commit()

        assert _claimable(session, pool, PRIMARY) == {generation.id}
        assert _claimable(session, pool, WORKER) == set()


def test_a_pinned_job_is_claimable_only_by_its_own_worker(tmp_path) -> None:
    factory = _factory(tmp_path)
    pool = _pool(WORKER)
    with factory() as session:
        shared = _profile(PRIMARY)
        replica = _profile(WORKER)
        session.add_all([shared, replica])
        session.flush()
        pinned = _generation(shared, instance=WORKER)
        session.add(pinned)
        session.commit()

        assert _claimable(session, pool, PRIMARY) == set()
        assert _claimable(session, pool, WORKER) == {pinned.id}


def test_snapshot_reports_idle_busy_and_offline_workers(tmp_path) -> None:
    factory = _factory(tmp_path)
    pool = _pool(WORKER)
    with factory() as session:
        shared = _profile(PRIMARY)
        session.add(shared)
        session.flush()
        session.add_all(
            [
                _generation(shared, status=GenerationStatus.RUNNING, instance=PRIMARY),
                _generation(shared),
                _generation(shared, status=GenerationStatus.RUNNING, instance=OUTSIDE),
            ]
        )
        session.add_all(
            [
                ComfyUIInstanceHealth(
                    instance_id=PRIMARY, available=True, checked_at=datetime.now(UTC)
                ),
                ComfyUIInstanceHealth(
                    instance_id=WORKER, available=True, checked_at=datetime.now(UTC)
                ),
            ]
        )
        session.commit()

        snapshot = pool.snapshot(session)
        assert snapshot.payload() == {
            "worker_count": 2,
            "available_count": 2,
            "idle_count": 1,
            "busy_count": 1,
            "free_slot_count": 1,
            "unassigned_queued_count": 1,
        }
        # Work on an instance outside the pool never counts as pool occupancy.
        assert [worker.id for worker in snapshot.workers] == [PRIMARY, WORKER]
        assert pool.available_member_ids(session) == (PRIMARY, WORKER)

        offline = session.get(ComfyUIInstanceHealth, WORKER)
        assert offline is not None
        offline.available = False
        session.commit()
        degraded = pool.snapshot(session)
        assert degraded.available_count == 1
        assert degraded.idle_count == 0
        assert degraded.free_slot_count == 0
        assert pool.available_member_ids(session) == (PRIMARY,)


def test_legacy_service_health_keeps_the_primary_usable(tmp_path) -> None:
    factory = _factory(tmp_path)
    pool = _pool()
    with factory() as session:
        session.add(ServiceHealth(service="comfyui", available=True, checked_at=datetime.now(UTC)))
        session.commit()
        assert pool.available_member_ids(session) == (PRIMARY,)
        assert pool.snapshot(session).idle_count == 1


def test_roles_describe_image_text_and_unused_instances() -> None:
    pool = _pool(WORKER)
    assert pool.role(PRIMARY) == "image"
    assert pool.role(WORKER) == "image"
    assert pool.role(OUTSIDE) == "text"
    assert _pool(text=None).role(OUTSIDE) == "unused"
    assert pool.is_member(None) is False
    assert pool.capacity(PRIMARY) == 1
