"""An account's own choice of model for Creative Direction.

The LLM Router's catalog is the only source of choices and it changes over time, so a saved choice
is stored as a service ID and compared with the current catalog when it is used. A choice that is
no longer published is kept rather than discarded: it answers again as soon as the router publishes
that service again.
"""

from __future__ import annotations

import re

# The panel's first option: let this application pick, as it did before the choice existed.
ASSISTANT_MODEL_AUTOMATIC = "auto"

# A router service ID as it appears in a capabilities document: a short lowercase slug.
ASSISTANT_MODEL_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def normalize_assistant_model(value: object) -> str | None:
    """A saved model choice as a service ID, or ``None`` for the automatic choice.

    Accepts everything the control panel sends — nothing selected, ``auto``, or a service ID — and
    treats a value that is not shaped like a service ID as no choice at all, so a damaged setting
    never puts a name the router cannot match in front of a request.
    """

    if value is None:
        return None
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or candidate == ASSISTANT_MODEL_AUTOMATIC:
        return None
    if not ASSISTANT_MODEL_SLUG_PATTERN.fullmatch(candidate):
        return None
    return candidate
