"""Accept a logical submission and its receipt in the same SQLite transaction."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm import Session

from ..blocking import run_blocking
from ..errors import AppError
from ..models import Generation, GenerationRunMember, GenerationSubmission, User, UserState
from ..schemas import (
    GenerationBatchCreate,
    GenerationBatchItem,
    GenerationBatchResult,
    GenerationCreate,
    GenerationSummary,
)
from .events import event_payload
from .generation_activity import begin_run
from .user_state import lock_user_state

if TYPE_CHECKING:
    from .generations import GenerationService

logger = logging.getLogger(__name__)


def project_receipt(
    service: GenerationService, session: Session, receipt: GenerationSubmission
) -> GenerationSummary | GenerationBatchResult:
    items = []
    for outcome in receipt.outcomes:
        if "error" in outcome:
            items.append(GenerationBatchItem(error=outcome["error"]))
        else:
            generation = session.get(Generation, outcome["generation_id"])
            if generation is None or generation.owner_id != receipt.owner_id:
                raise AppError(
                    "submission_result_unavailable",
                    "This submission was accepted, but its result has been deleted.",
                    status_code=410,
                )
            items.append(GenerationBatchItem(generation=service.summary(session, generation)))
    if receipt.endpoint == "single":
        assert items[0].generation is not None
        return items[0].generation
    return GenerationBatchResult(items=items)


def lookup(service: GenerationService, owner_id: str, key: str) -> dict[str, Any]:
    with service.session_factory() as session:
        receipt = session.get(GenerationSubmission, (owner_id, key))
        if receipt is None:
            raise AppError(
                "submission_not_found", "No accepted submission is recorded yet.", status_code=404
            )
        return {
            "key": key,
            "endpoint": receipt.endpoint,
            "result": project_receipt(service, session, receipt).model_dump(mode="json"),
        }


async def accept(
    service: GenerationService,
    owner_id: str,
    request: GenerationCreate | GenerationBatchCreate,
    key: str | None = None,
) -> GenerationSummary | GenerationBatchResult:
    batch = isinstance(request, GenerationBatchCreate)
    endpoint = "batch" if batch else "single"
    digest = hashlib.sha256(
        json.dumps(
            {"endpoint": endpoint, "payload": request.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    def transaction() -> tuple[GenerationSummary | GenerationBatchResult, list[dict[str, Any]]]:
        with service.session_factory() as session:
            # Acquire the SQLite writer before reading the receipt. Concurrent
            # identical requests serialize here, including requests after a lost reply.
            lock_user_state(session)
            receipt = session.get(GenerationSubmission, (owner_id, key)) if key else None
            if receipt is not None:
                if receipt.request_digest != digest or receipt.endpoint != endpoint:
                    raise AppError(
                        "idempotency_conflict",
                        "This submission ID belongs to a different request.",
                        status_code=409,
                    )
                return project_receipt(service, session, receipt), []
            user = session.get(User, owner_id)
            if user is None or user.state != UserState.ACTIVE:
                raise AppError("authentication_required", "Sign in is required.", status_code=401)
            requests = request.items if isinstance(request, GenerationBatchCreate) else [request]
            run = begin_run(session, owner_id, len(requests))
            outcomes: list[dict[str, Any]] = []
            events: list[dict[str, Any]] = []
            for item in requests:
                try:
                    with session.begin_nested():
                        generation, event = service._prepare_accept(
                            session, user=user, request=item
                        )
                        session.add(GenerationRunMember(generation_id=generation.id, run_id=run.id))
                    outcomes.append({"generation_id": generation.id})
                    events.append(event_payload(event))
                except AppError as error:
                    if not batch:
                        raise
                    run.submission_failed_count += 1
                    outcomes.append(
                        {
                            "error": {
                                "code": error.code,
                                "message": error.message,
                                "fields": error.fields,
                                "details": error.details,
                                "status": error.status_code,
                            }
                        }
                    )
            receipt = GenerationSubmission(
                owner_id=owner_id,
                key=key or "",
                endpoint=endpoint,
                request_digest=digest,
                outcomes=outcomes,
            )
            if key:
                session.add(receipt)
            # Projection belongs to the transaction: a failure cannot leave an
            # accepted generation without the result needed for its receipt.
            result = project_receipt(service, session, receipt)
            session.commit()
            return result, events

    result, events = await run_blocking(transaction)
    for event in events:
        try:
            await service.broker.publish(owner_id, event)
        except Exception:
            logger.exception("submission_notification_failed")
    return result
