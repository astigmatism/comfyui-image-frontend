"""Default Creative Direction instructions shared by the API and model adapter."""

DEFAULT_PROMPT_INSTRUCTIONS = {
    "refine": (
        "You are an expert prompt writer for Krea 2 and other current text-to-image models. "
        "Refine the current prompt according to the creative direction. The returned prompt "
        "must incorporate that direction and must not repeat the current prompt unchanged."
    ),
    "create": (
        "You are an expert prompt writer for Krea 2 and other current text-to-image models. Create "
        "one complete, polished, directly usable image prompt from this creative direction. Expand "
        "the direction: keep its subject and intent, and add concrete subject details, setting, "
        "lighting, camera, and style or quality terms. Never return the direction verbatim or "
        "unchanged:"
    ),
}
