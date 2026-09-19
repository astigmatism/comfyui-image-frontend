from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.prompt_instructions import DEFAULT_PROMPT_INSTRUCTIONS
from ..errors import AppError
from ..models import PromptAssistantRun
from ..schemas import PromptComposeRequest, PromptComposeResponse
from .ollama import MAX_CREATE_EXCLUSIONS

if TYPE_CHECKING:
    from ..container import AppContainer

PROMPT_HISTORY_SCAN_LIMIT = 64


async def compose_prompt(
    container: AppContainer, session: Session, owner_id: str, payload: PromptComposeRequest
) -> PromptComposeResponse:
    if payload.mode == "refine" and not payload.prompt.strip():
        raise AppError(
            "prompt_required",
            "Refine mode requires a current prompt.",
            fields={"prompt": "Enter a prompt first."},
        )
    if not payload.creative_direction.strip() and payload.mode == "create":
        raise AppError(
            "direction_required",
            "Create mode requires a creative direction.",
            fields={"creative_direction": "Describe the intended image."},
        )
    excluded_prompts: list[str] = []
    if payload.mode == "create":
        with container.db.session_factory() as history_session:
            recent_runs = history_session.scalars(
                select(PromptAssistantRun)
                .where(
                    PromptAssistantRun.owner_id == owner_id,
                    PromptAssistantRun.ollama_output.is_not(None),
                    # Only successful runs seed the distinctness baseline; rejected or
                    # errored runs must never pollute the exclusion set.
                    PromptAssistantRun.error_code.is_(None),
                )
                .order_by(PromptAssistantRun.created_at.desc(), PromptAssistantRun.id.desc())
                .limit(PROMPT_HISTORY_SCAN_LIMIT)
            ).all()
        excluded_prompts = [
            run.ollama_output
            for run in recent_runs
            if run.mode == "create"
            and run.creative_direction == payload.creative_direction
            and run.ollama_output
        ][:MAX_CREATE_EXCLUSIONS]

    # Authentication and prompt-history reads are complete. Release their pooled
    # connection before waiting on the external model; this Session can be reused
    # afterward to record the result.
    session.close()
    try:
        result = await container.ollama.compose(
            mode=payload.mode,
            prompt=payload.prompt,
            direction=payload.creative_direction,
            think=payload.think,
            excluded_prompts=excluded_prompts,
            instructions=payload.instructions,
        )
    except AppError as exc:
        session.add(
            PromptAssistantRun(
                owner_id=owner_id,
                mode=payload.mode,
                thinking_enabled=payload.think,
                # Failed composition diagnostics are intentionally metadata-only.
                prompt_before="",
                creative_direction="",
                model_name=(
                    exc.details.get("model") if isinstance(exc.details.get("model"), str) else None
                ),
                template_version=container.settings.prompt_template_version,
                raw_response_json={"error_details": exc.details},
                error_code=exc.code,
                error_message=exc.message,
            )
        )
        session.commit()
        raise
    run = PromptAssistantRun(
        owner_id=owner_id,
        mode=payload.mode,
        thinking_enabled=payload.think,
        prompt_before=payload.prompt,
        instructions=payload.instructions or DEFAULT_PROMPT_INSTRUCTIONS[payload.mode],
        creative_direction=payload.creative_direction,
        model_name=result.model,
        template_version=container.settings.prompt_template_version,
        ollama_output=result.prompt,
        raw_response_json=result.raw_response,
        duration_ms=result.duration_ms,
    )
    session.add(run)
    session.commit()
    return PromptComposeResponse(
        composition_id=run.id,
        prompt=result.prompt,
        model=result.model,
        template_version=run.template_version,
    )
