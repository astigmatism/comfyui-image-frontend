from __future__ import annotations

from app.main import create_app
from app.models import Artifact, ArtifactState, Favorite, Generation, GenerationStatus
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from tests.conftest import change_password, create_user, login
from tests.helpers import (
    USER_TEMP,
    create_generation,
    login_ready_admin,
    provision_user,
    restore_cookie,
)


def _statement_count_for_page(
    client: TestClient, owner_id: str, limit: int
) -> tuple[int, list[str]]:
    statements: list[str] = []
    engine = client.app.state.container.db.engine

    def record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        with client.app.state.container.db.session_factory() as session:
            page = client.app.state.container.generations.list_page(
                session,
                owner_id=owner_id,
                cursor=None,
                limit=limit,
            )
            assert len(page.items) == limit
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
    return len(statements), statements


def _statement_count_for_favorites(
    client: TestClient, owner_id: str, limit: int
) -> tuple[int, list[str]]:
    statements: list[str] = []
    engine = client.app.state.container.db.engine

    def record_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        with client.app.state.container.db.session_factory() as session:
            page = client.app.state.container.generations.list_favorites(
                session,
                owner_id=owner_id,
                cursor=None,
                limit=limit,
            )
            assert len(page.items) == limit
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
    return len(statements), statements


def test_gallery_query_count_is_constant_and_detail_json_is_not_selected(
    settings_factory, fake_state
) -> None:
    del fake_state
    with TestClient(create_app(settings_factory(enable_background_worker=False))) as client:
        user, owner_cookie = provision_user(client, username="query.performance")
        generations = [
            create_generation(client, f"query performance {index}", seed=index)
            for index in range(24)
        ]

        login_ready_admin(client)
        create_user(client, "query.other", USER_TEMP)
        client.cookies.clear()
        login(client, "query.other", USER_TEMP)
        change_password(client, "QueryOtherPermanent123!")
        other = create_generation(client, "other owner's generation", seed=900)
        restore_cookie(client, owner_cookie)

        one_count, one_statements = _statement_count_for_page(client, str(user["id"]), 1)
        page_count, page_statements = _statement_count_for_page(client, str(user["id"]), 24)
        assert one_count == page_count == 6

        with client.app.state.container.db.session_factory() as session:
            session.add_all(
                Favorite(owner_id=str(user["id"]), generation_id=item["id"]) for item in generations
            )
            session.commit()
        one_favorite_count, one_favorite_statements = _statement_count_for_favorites(
            client, str(user["id"]), 1
        )
        favorite_page_count, favorite_page_statements = _statement_count_for_favorites(
            client, str(user["id"]), 24
        )
        assert one_favorite_count == favorite_page_count == 5
        assert other["id"] not in {
            item["id"] for item in client.get("/api/generations?limit=60").json()["items"]
        }

        sql = "\n".join(
            [
                *one_statements,
                *page_statements,
                *one_favorite_statements,
                *favorite_page_statements,
            ]
        ).casefold()
        for detail_only_column in (
            "compiled_graph_json",
            "submitted_graph_json",
            "raw_history_json",
            "internal_diagnostics_json",
            "declared_outputs_json",
            "unmapped_outputs_json",
            "result_warnings_json",
            "result_errors_json",
            "comfyui_status_json",
        ):
            assert detail_only_column not in sql

        # Invalid detail-only JSON would fail ORM JSON deserialization if the list query loaded
        # complete Generation entities. The explicit summary projection never fetches it.
        with client.app.state.container.db.session_factory() as session:
            session.execute(
                text(
                    "UPDATE generations SET compiled_graph_json = :invalid, "
                    "raw_history_json = :invalid WHERE id = :generation_id"
                ),
                {"invalid": "{not-valid-json", "generation_id": generations[0]["id"]},
            )
            session.commit()
            page = client.app.state.container.generations.list_page(
                session,
                owner_id=str(user["id"]),
                cursor=None,
                limit=24,
            )
        assert len(page.items) == 24


def test_batched_gallery_summary_matches_single_item_semantics_with_artifacts_and_favorite(
    settings_factory, fake_state
) -> None:
    del fake_state
    with TestClient(create_app(settings_factory(enable_background_worker=False))) as client:
        user, _ = provision_user(client, username="query.contract")
        generation = create_generation(client, "summary contract", seed=81)
        container = client.app.state.container

        with container.db.session_factory() as session:
            stored = session.get(Generation, generation["id"])
            assert stored is not None
            stored.status = GenerationStatus.RUNNING
            stored.progress_json = {
                "kind": "node",
                "node_id": "54",
                "display_node_id": "54",
                "real_node_id": "54",
                "parent_node_id": None,
                "label": "Main sampling",
                "value": 12,
                "maximum": 24,
                "fraction": 0.5,
                "updated_at": "2026-07-17T12:34:56.789Z",
            }
            fallback = Artifact(
                generation_id=stored.id,
                owner_id=str(user["id"]),
                output_id="preview",
                role="preview",
                kind="image",
                state=ArtifactState.PROVISIONAL,
                sequence=900,
                batch_index=0,
                storage_path=f"assets/{stored.id}/fallback.png",
                thumbnail_path=f"assets/{stored.id}/fallback.webp",
                mime_type="image/png",
                byte_size=100,
                width=768,
                height=512,
                sha256="1" * 64,
            )
            canonical = Artifact(
                generation_id=stored.id,
                owner_id=str(user["id"]),
                output_id="final",
                role="final",
                kind="image",
                state=ArtifactState.FINAL,
                sequence=100,
                batch_index=1,
                storage_path=f"assets/{stored.id}/canonical.png",
                thumbnail_path=f"assets/{stored.id}/canonical.webp",
                mime_type="image/png",
                byte_size=200,
                width=512,
                height=512,
                sha256="2" * 64,
                canonical=True,
                best_available=True,
            )
            session.add_all([fallback, canonical])
            session.flush()
            stored.artifact_count = 2
            stored.final_artifact_count = 1
            stored.best_available_artifact_id = fallback.id
            stored.canonical_artifact_id = canonical.id
            session.add(Favorite(owner_id=str(user["id"]), generation_id=stored.id))
            session.commit()

        with container.db.session_factory() as session:
            stored = container.generations.get_owned(session, str(user["id"]), generation["id"])
            expected = container.generations.summary(session, stored)
            actual = container.generations.list_page(
                session,
                owner_id=str(user["id"]),
                cursor=None,
                limit=24,
            ).items[0]

        assert actual.model_dump(mode="json") == expected.model_dump(mode="json")
        assert actual.display_artifact is not None
        assert actual.display_artifact.id == canonical.id
        assert actual.image_count == 2
        assert actual.is_favorite is True
        assert actual.cancel_allowed is True
        assert actual.progress is not None
        assert actual.progress.label == "Main sampling"
        assert actual.progress.fraction == 0.5
        assert actual.expected_width == 512
        assert actual.expected_height == 512

        with container.db.session_factory() as session:
            stored = session.get(Generation, generation["id"])
            assert stored is not None
            stored.canonical_artifact_id = "missing-canonical-artifact"
            session.commit()
            expected_fallback = container.generations.summary(session, stored)
            actual_fallback = container.generations.list_page(
                session,
                owner_id=str(user["id"]),
                cursor=None,
                limit=24,
            ).items[0]
        assert actual_fallback.model_dump(mode="json") == expected_fallback.model_dump(mode="json")
        assert actual_fallback.display_artifact is not None
        assert actual_fallback.display_artifact.id == fallback.id
