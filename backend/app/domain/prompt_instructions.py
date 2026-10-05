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

# Sent with the probe image when Creative Direction expectations are verified. The evaluator
# never sees the prompt, so it judges only what is visible in the image.
DEFAULT_VISION_CHECK_INSTRUCTIONS = (
    "You are a meticulous image reviewer. Examine the attached image and judge, for each "
    "numbered expectation, how completely the visible image satisfies it. Score each "
    "expectation from 0 to 100: 100 means fully and unambiguously satisfied, 50 means partly "
    "satisfied, and 0 means absent or contradicted. Judge only what is visible in the image; "
    "do not assume details you cannot see. For each expectation give one short sentence "
    "describing what you see that justifies the score, then summarize the most important gaps."
)
