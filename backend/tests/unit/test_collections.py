from __future__ import annotations

from unittest.mock import Mock

import pytest
from app.errors import AppError
from app.models import Base, Generation, GenerationStatus, User
from app.schemas import CollectionCreate, CollectionUpdate
from app.services.collections import CollectionService
from app.services.generations import GenerationService
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def collection_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    user = User(
        username="collection.owner",
        username_normalized="collection.owner",
        password_hash="test-hash",
        must_change_password=False,
    )
    session.add(user)
    session.commit()
    session.info["owner_id"] = user.id
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _service() -> CollectionService:
    return CollectionService(Mock(spec=GenerationService))


def _create(
    service: CollectionService,
    session: Session,
    name: str,
    parent_id: str | None = None,
) -> str:
    result = service.create(
        session,
        owner_id=str(session.info["owner_id"]),
        payload=CollectionCreate(name=name, parent_id=parent_id),
    )
    return result.id


def test_create_rejects_a_child_below_level_five(collection_session: Session) -> None:
    service = _service()
    parent_id = None
    for level in range(1, 6):
        parent_id = _create(service, collection_session, f"Level {level}", parent_id)

    with pytest.raises(AppError) as exc:
        _create(service, collection_session, "Level 6", parent_id)

    assert exc.value.code == "collection_depth"
    assert exc.value.status_code == 409


def test_move_rejects_self_and_descendant_cycles(collection_session: Session) -> None:
    service = _service()
    owner_id = str(collection_session.info["owner_id"])
    parent_id = _create(service, collection_session, "Parent")
    child_id = _create(service, collection_session, "Child", parent_id)

    for target_id in (parent_id, child_id):
        with pytest.raises(AppError) as exc:
            service.update(
                collection_session,
                owner_id=owner_id,
                collection_id=parent_id,
                payload=CollectionUpdate(parent_id=target_id),
            )
        assert exc.value.code == "collection_cycle"


def test_move_revalidates_deepest_descendant_level(collection_session: Session) -> None:
    service = _service()
    owner_id = str(collection_session.info["owner_id"])
    deep_parent = None
    for level in range(1, 5):
        deep_parent = _create(service, collection_session, f"Destination {level}", deep_parent)
    subtree = _create(service, collection_session, "Subtree")
    _create(service, collection_session, "Subtree child", subtree)

    with pytest.raises(AppError) as exc:
        service.update(
            collection_session,
            owner_id=owner_id,
            collection_id=subtree,
            payload=CollectionUpdate(parent_id=deep_parent),
        )

    assert exc.value.code == "collection_depth"


def test_flat_list_count_excludes_pending_delete_generations(
    collection_session: Session,
) -> None:
    service = _service()
    owner_id = str(collection_session.info["owner_id"])
    collection_id = _create(service, collection_session, "Counted")
    common = {
        "owner_id": owner_id,
        "collection_id": collection_id,
        "status": GenerationStatus.SUCCEEDED,
        "comfyui_instance_id": "default",
        "comfyui_instance_label": "Default",
        "workflow_profile_id": "profile",
        "workflow_id": "workflow",
        "workflow_display_name": "Workflow",
        "workflow_version": "1",
        "contract_schema_version": "1",
        "adapter_version": "1",
        "ui_graph_sha256": "a" * 64,
        "api_graph_sha256": "b" * 64,
        "contract_sha256": "c" * 64,
        "resolved_contract_json": {},
        "requested_controls_json": {},
        "effective_controls_json": {},
        "final_prompt": "prompt",
        "compiled_graph_json": {},
        "compiled_graph_sha256": "d" * 64,
    }
    collection_session.add_all(
        [
            Generation(queue_seq=1, pending_delete=False, **common),
            Generation(queue_seq=2, pending_delete=True, **common),
        ]
    )
    collection_session.commit()

    result = service.list(collection_session, owner_id=owner_id)

    assert len(result) == 1
    assert result[0].generation_count == 1
    assert result[0].previews == []
