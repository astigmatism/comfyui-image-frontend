from __future__ import annotations

import json
from typing import Any

from app.main import create_app
from fastapi.testclient import TestClient


def test_health_and_structured_shutdown_logs_survive_migration_logging(
    settings_factory, fake_state, capsys
) -> None:
    del fake_state
    with TestClient(create_app(settings_factory(log_level="INFO"))) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": True}

    records: list[dict[str, Any]] = []
    for line in capsys.readouterr().out.splitlines():
        if line.startswith("{"):
            records.append(json.loads(line))
    messages = [record["message"] for record in records]

    expected = [
        "application_shutdown_started",
        "worker_cancellation_complete",
        "external_clients_closed",
        "database_closed",
        "application_shutdown_complete",
    ]
    assert expected == [message for message in messages if message in expected]
    completion = next(
        record for record in records if record["message"] == "application_shutdown_complete"
    )
    assert completion["shutdown_duration_seconds"] >= 0
