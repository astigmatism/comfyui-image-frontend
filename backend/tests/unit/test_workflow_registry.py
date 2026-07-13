from __future__ import annotations

import asyncio
from typing import Any

import httpx
from app.models import Base
from app.services.comfyui import ComfyCapabilities
from app.services.workflow_registry import WorkflowRegistry
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


class FetchFailingAdapter:
    async def probe(self) -> ComfyCapabilities:
        return ComfyCapabilities(
            object_info={},
            workflow_list_route="v2_query:/api/v2/userdata",
            workflow_get_route="path:/userdata/{path}",
            system={},
            assets=[],
            capabilities={"workflow_userdata": True},
        )

    async def list_workflow_files(self) -> list[str]:
        return ["nested/profile.api.json", "nested/profile.workflow.json"]

    async def get_workflow_file(self, relative_path: str) -> dict[str, Any]:
        request = httpx.Request("GET", "http://comfy.test/userdata/workflow-source")
        response = httpx.Response(404, request=request)
        response.raise_for_status()
        raise AssertionError("unreachable")


def test_http_fetch_failure_has_actionable_non_contract_diagnostic(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(engine, expire_on_commit=False, class_=Session)
    registry = WorkflowRegistry(session_factory, FetchFailingAdapter())  # type: ignore[arg-type]
    log_messages: list[str] = []

    def capture_warning(message: str, *args: object) -> None:
        log_messages.append(message % args)

    monkeypatch.setattr("app.services.workflow_registry.logger.warning", capture_warning)

    diagnostics = asyncio.run(registry.refresh())

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.basename == "nested/profile"
    assert diagnostic.code == "workflow_fetch_failed"
    assert diagnostic.message == (
        "ComfyUI could not return this workflow source file. "
        "Verify its presence and the configured user-data route."
    )
    assert diagnostic.details_json == {"route_mode": "path", "http_status": 404}
    assert "contract_invalid" not in {item.code for item in diagnostics}
    assert (
        "workflow_fetch_failed basename=nested/profile route_mode=path http_status=404"
        in log_messages
    )

    engine.dispose()
