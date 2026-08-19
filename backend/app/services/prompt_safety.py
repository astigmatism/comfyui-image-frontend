from __future__ import annotations

import re
import unicodedata

from ..errors import AppError

_MINOR_PATTERN = re.compile(
    r"\b(?:minor|underage|child|children|childhood|kid|kids|preteen|tween|"
    r"teen|teens|teenage|teenager|teenagers|adolescent|adolescents|toddler|"
    r"toddlers|infant|infants|baby|babies|schoolgirl|schoolgirls|schoolboy|"
    r"schoolboys|loli|shota)\b"
)
_MINOR_AGE_PATTERN = re.compile(
    r"\b(?:(?:age|aged)\s*)?(?:[0-9]|1[0-7])(?:\s*[- ]?year[- ]?old|\s*y\s*/?\s*o)\b"
)
_SEXUAL_PATTERN = re.compile(
    r"\b(?:sex|sexual|sexually|sexy|porn|porno|pornographic|pornography|xxx|"
    r"erotic|erotica|explicit|nude|nudity|naked|topless|bottomless|lingerie|"
    r"fetish|bdsm|seductive|seduction|genital|genitals|penis|vagina|vulva|"
    r"masturbate|masturbation|intercourse|orgasm|rape)\b"
)
_INHERENTLY_PROHIBITED_PATTERN = re.compile(
    r"\b(?:lolicon|shotacon|csam|child\s+(?:porn|pornography)|"
    r"child\s+sexual\s+abuse)\b"
)


def contains_sexual_content_involving_minors(*values: str) -> bool:
    """Conservatively identify text that combines minor and sexual concepts."""

    text = "\n".join(value for value in values if value)
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = re.sub(r"[_\W]+", " ", normalized)
    return bool(
        _INHERENTLY_PROHIBITED_PATTERN.search(normalized)
        or (
            (_MINOR_PATTERN.search(normalized) or _MINOR_AGE_PATTERN.search(normalized))
            and _SEXUAL_PATTERN.search(normalized)
        )
    )


def ensure_no_sexual_content_involving_minors(
    *values: str,
    field: str | None = None,
) -> None:
    if not contains_sexual_content_involving_minors(*values):
        return
    raise AppError(
        "sexual_minors_prohibited",
        "Prompt refinement and image generation cannot be used for sexual content involving "
        "minors.",
        status_code=422,
        fields={field: "Remove sexual content involving minors."} if field else None,
    )
