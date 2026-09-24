from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from ..blocking import run_blocking as _run_blocking
from ..domain.prompt_instructions import DEFAULT_PROMPT_INSTRUCTIONS
from ..errors import AppError
from ..models import GenerationPreparation, PromptAssistantRun
from ..schemas import PromptComposeRequest, PromptComposeResponse
from .ollama import MAX_CREATE_EXCLUSIONS
from .user_state import lock_user_state

if TYPE_CHECKING:
    from ..container import AppContainer

PROMPT_HISTORY_SCAN_LIMIT = 64


async def compose_prompt(
    container: AppContainer,
    owner_id: str,
    payload: PromptComposeRequest,
    *,
    preparation_id: str | None = None,
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

        def load_history() -> list[PromptAssistantRun]:
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
            return list(recent_runs)

        recent_runs = await _run_blocking(load_history)
        excluded_prompts = [
            run.ollama_output
            for run in recent_runs
            if run.mode == "create"
            and run.creative_direction == payload.creative_direction
            and run.ollama_output
        ][:MAX_CREATE_EXCLUSIONS]

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
        record = PromptAssistantRun(
            owner_id=owner_id,
            mode=payload.mode,
            thinking_enabled=payload.think,
            prompt_before="",
            creative_direction="",
            model_name=exc.details.get("model")
            if isinstance(exc.details.get("model"), str)
            else None,
            template_version=container.settings.prompt_template_version,
            raw_response_json={"error_details": exc.details},
            error_code=exc.code,
            error_message=exc.message,
        )
        await _run_blocking(_save_run, container, record)
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
    await _run_blocking(_save_run, container, run, preparation_id)
    return PromptComposeResponse(
        composition_id=run.id,
        prompt=result.prompt,
        model=result.model,
        template_version=run.template_version,
    )


def _save_run(
    container: AppContainer, run: PromptAssistantRun, preparation_id: str | None = None
) -> None:
    with container.db.session_factory() as session:
        lock_user_state(session)
        session.add(run)
        session.flush()
        if preparation_id:
            prepared = session.get(GenerationPreparation, preparation_id)
            if (
                prepared
                and prepared.owner_id == run.owner_id
                and prepared.status in {"preparing", "refining"}
            ):
                prepared.assistant_run_id = run.id
                prepared.prompt = run.ollama_output
                prepared.status = "ready"
        session.commit()
