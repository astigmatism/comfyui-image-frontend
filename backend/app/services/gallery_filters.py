"""One membership predicate for gallery pages, counts, groups and bulk actions."""

import json

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from ..models import Favorite, Generation, UserPreference
from ..schemas import GalleryFilters


def gallery_filter_predicate(
    session: Session, owner_id: str, filters: GalleryFilters
) -> ColumnElement[bool]:
    predicates: list[ColumnElement[bool]] = []
    if filters.favorites_only or filters.unfavorited_only:
        favorite = (
            select(Favorite.id)
            .where(Favorite.owner_id == owner_id, Favorite.generation_id == Generation.id)
            .correlate(Generation)
            .exists()
        )
        predicates.append(favorite if filters.favorites_only else ~favorite)
    excluded = set(filters.excluded_checkpoint_ranks)
    if excluded:
        preference = session.get(UserPreference, owner_id)
        tiers = preference.checkpoint_tiers_json if preference else {}
        ranks: dict[str, str] = {}
        for grade in ("A", "B", "C", "D", "F"):
            for identity in tiers.get(grade, []):
                ranks.setdefault(identity, grade)
        # C includes unassigned checkpoints and legacy rows without an identity.
        # A JSON table uses one bind even with the maximum 25,000 ranked models.
        identities = [
            identity
            for identity, grade in ranks.items()
            if (grade not in excluded if "C" in excluded else grade in excluded)
        ]
        values = func.json_each(json.dumps(identities)).table_valued("value")
        matches = Generation.checkpoint_id.in_(select(values.c.value))
        predicates.append(
            matches if "C" in excluded else or_(Generation.checkpoint_id.is_(None), ~matches)
        )
    return and_(*predicates) if predicates else true()
