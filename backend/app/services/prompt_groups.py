from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import DateTime, and_, case, func, or_, select
from sqlalchemy.orm import Session

from ..errors import AppError
from ..models import Collection, Generation
from ..schemas import (
    GenerationPage,
    PromptChangePart,
    PromptChanges,
    PromptGroupMembership,
    PromptGroupSummary,
)
from .generations import (
    GenerationService,
    _decode_cursor,
    _encode_cursor,
    _summary_projection,
    _summary_row,
)


def group_members(owner_id: str, collection_id: str | None) -> Any:
    """Find runs before applying page/ID filters; never read full prompts here."""
    order = (Generation.accepted_at, Generation.id)
    previous = (
        select(
            Generation.id,
            Generation.accepted_at,
            Generation.prompt_fingerprint,
            func.lag(Generation.prompt_fingerprint).over(order_by=order).label("previous_prompt"),
            func.lag(Generation.id).over(order_by=order).label("previous_id"),
        )
        .where(
            Generation.owner_id == owner_id,
            Generation.collection_id == collection_id,
            Generation.pending_delete.is_(False),
        )
        .cte("prompt_previous")
    )
    runs = select(
        previous,
        func.sum(case((previous.c.prompt_fingerprint == previous.c.previous_prompt, 0), else_=1))
        .over(order_by=(previous.c.accepted_at, previous.c.id))
        .label("run"),
    ).cte("prompt_runs")
    partition: dict[str, Any] = {
        "partition_by": runs.c.run,
        "order_by": (runs.c.accepted_at, runs.c.id),
    }
    return select(
        runs.c.id,
        runs.c.accepted_at,
        func.first_value(runs.c.id).over(**partition).label("group_id"),
        func.first_value(runs.c.accepted_at, type_=DateTime(timezone=True))
        .over(**partition)
        .label("group_time"),
        func.first_value(runs.c.previous_id).over(**partition).label("previous_id"),
        func.count().over(partition_by=runs.c.run).label("generation_count"),
    ).cte("prompt_members")


def lookup_groups(
    session: Session, owner_id: str, collection_id: str | None, ids: list[str]
) -> list[PromptGroupMembership]:
    if collection_id is not None and not session.scalar(
        select(Collection.id).where(Collection.id == collection_id, Collection.owner_id == owner_id)
    ):
        raise AppError("not_found", "Collection was not found.", status_code=404)
    members = group_members(owner_id, collection_id)
    rows = session.execute(select(members).where(members.c.id.in_(ids))).all()
    return [
        PromptGroupMembership(
            generation_id=row.id,
            group=PromptGroupSummary(
                id=row.group_id,
                generation_count=row.generation_count,
                previous_generation_id=row.previous_id,
                after_cursor=_encode_cursor(row.group_time, row.group_id),
            ),
        )
        for row in rows
    ]


def owned_group(
    session: Session, owner_id: str, collection_id: str | None, generation_id: str
) -> PromptGroupSummary:
    groups = lookup_groups(session, owner_id, collection_id, [generation_id])
    if not groups:
        raise AppError(
            "not_found", "Prompt group was not found. Refresh the gallery.", status_code=404
        )
    return groups[0].group


def member_page(
    session: Session,
    service: GenerationService,
    owner_id: str,
    collection_id: str | None,
    generation_id: str,
    *,
    cursor: str | None = None,
    selection: bool = False,
) -> GenerationPage:
    group = owned_group(session, owner_id, collection_id, generation_id)
    if selection and group.generation_count > 500:
        raise AppError(
            "selection_limit",
            "This group exceeds the 500-card selection limit. Select individual cards.",
            status_code=422,
        )
    members = group_members(owner_id, collection_id)
    statement = (
        select(*_summary_projection())
        .join(members, members.c.id == Generation.id)
        .where(members.c.group_id == group.id)
    )
    if cursor and not selection:
        time, item_id = _decode_cursor(cursor)
        statement = statement.where(
            or_(
                Generation.accepted_at < time,
                and_(Generation.accepted_at == time, Generation.id < item_id),
            )
        )
    limit = 500 if selection else 60
    found = session.execute(
        statement.order_by(Generation.accepted_at.desc(), Generation.id.desc()).limit(limit + 1)
    ).all()
    # Resolve the selection in this read transaction; later arrivals aren't selected.
    if selection and len(found) > 500:
        raise AppError("selection_limit", "Select at most 500 cards at a time.", status_code=422)
    rows = [_summary_row(row) for row in found[:limit]]
    context = service._summary_context(session, owner_id=owner_id, rows=rows)
    return GenerationPage(
        items=[service._project_summary(row, context) for row in rows],
        next_cursor=_encode_cursor(rows[-1].accepted_at, rows[-1].id)
        if len(found) > limit and rows
        else None,
    )


def prompt_changes(before: str, after: str) -> PromptChanges:
    # Keep whitespace tokens: punctuation, line breaks and formatting are real edits too.
    tokens_before = re.findall(r"\s+|\w+|[^\w\s]", before)
    tokens_after = re.findall(r"\s+|\w+|[^\w\s]", after)
    matcher = SequenceMatcher(None, tokens_before, tokens_after, autojunk=True)
    groups = list(matcher.get_grouped_opcodes(10))
    snippets = []
    shown = 0
    for group in groups[:3]:
        parts = []
        if group[0][1] or group[0][3]:
            parts.append(PromptChangePart(kind="context", text="…"))
        visible = group[:7]
        for tag, i, j, k, end in visible:
            if tag == "equal":
                context = "".join(tokens_after[k:end])
                if len(context) > 160:
                    context = context[:79] + "…" + context[-80:]
                parts.append(PromptChangePart(kind="context", text=context))
            else:
                shown += 1
                for kind, tokens in (
                    ("removed", tokens_before[i:j]),
                    ("added", tokens_after[k:end]),
                ):
                    if tokens:
                        value = "".join(tokens)
                        # Bound popover size even if a whole novel was replaced.
                        if len(value) > 300:
                            value = value[:297] + "…"
                        if not value.strip():
                            value = (
                                "[whitespace: "
                                + value.replace("\n", "↵").replace("\t", "⇥").replace(" ", "·")
                                + "]"
                            )
                        parts.append(PromptChangePart(kind=kind, text=value))
        if visible[-1][2] < len(tokens_before) or visible[-1][4] < len(tokens_after):
            parts.append(PromptChangePart(kind="context", text="…"))
        snippets.append(parts)
    count = sum(tag != "equal" for tag, *_ in matcher.get_opcodes())
    return PromptChanges(edit_count=count, snippets=snippets, omitted_edits=count - shown)


def changes_for_group(
    session: Session, owner_id: str, collection_id: str | None, generation_id: str
) -> PromptChanges:
    group = owned_group(session, owner_id, collection_id, generation_id)
    if group.previous_generation_id is None:
        return PromptChanges(first_prompt=True)
    prompts = {
        row.id: row.final_prompt
        for row in session.execute(
            select(Generation.id, Generation.final_prompt).where(
                Generation.owner_id == owner_id,
                Generation.id.in_([group.id, group.previous_generation_id]),
            )
        ).all()
    }
    return prompt_changes(prompts[group.previous_generation_id], prompts[group.id])
