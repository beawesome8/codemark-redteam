# -----------------------------------------------------------------------
# WORD LISTS
#
# These lists define what kind of task is being requested.
# The analyzer scans the prompt for these words and scores it.
#
# Why lists and not ML?
# A simple rule-based system is transparent, debuggable, fast,
# and good enough for a routing decision. You can explain exactly
# why a prompt got routed to a specific model — great for interviews.
# -----------------------------------------------------------------------

# Simple tasks: retrieve a fact, format something, translate, count
SIMPLE_TASK_WORDS = [
    "list", "what is", "what are", "when", "who", "where",
    "define", "spell", "count", "name", "translate",
    "format", "extract", "convert", "copy", "repeat",
    "find", "show", "tell me"
]

# Medium tasks: need some synthesis or generation
MEDIUM_TASK_WORDS = [
    "summarize", "summary", "explain", "describe",
    "classify", "categorize", "write", "draft",
    "rewrite", "edit", "improve", "suggest", "recommend",
    "paraphrase", "simplify", "outline"
]

# Complex tasks: need deep reasoning, judgment, or multi-step logic
COMPLEX_TASK_WORDS = [
    "analyze", "analysis", "compare", "contrast",
    "evaluate", "assess", "critique", "reason",
    "debate", "argue", "strategy", "plan",
    "predict", "forecast", "diagnose", "solve",
    "design", "architect", "review", "investigate",
    "prioritize", "optimize", "tradeoff"
]

# Risk override: these force Tier 3 regardless of score
# Correctness in these domains is non-negotiable
HIGH_RISK_KEYWORDS = [
    "legal", "law", "lawsuit", "contract", "compliance",
    "medical", "diagnosis", "prescription", "clinical",
    "financial", "investment", "tax", "audit",
    "confidential", "classified", "sensitive",
    "security", "vulnerability", "exploit"
]

# Structured output is harder for cheap models to produce reliably
STRUCTURED_OUTPUT_KEYWORDS = [
    "json", "table", "markdown", "csv", "xml",
    "structured", "formatted output", "schema",
    "bullet points", "numbered list"
]


def count_words(text: str) -> int:
    """
    Count the number of words in a string.

    Used to assess prompt length as a complexity signal.

    Args:
        text: Any string.

    Returns:
        int: Number of words (split by spaces).
    """
    return len(text.split())


def check_risk_keywords(text: str) -> bool:
    """
    Check if the prompt contains any high-risk domain keywords.

    If yes → return True → caller will force Tier 3 immediately.
    This is a HARD OVERRIDE. Score does not matter.

    Why? Because getting a legal or medical answer wrong costs far
    more than the price difference between Haiku and Opus.

    Args:
        text: The prompt text.

    Returns:
        bool: True if any risk keyword found.

    Example:
        "Draft a legal contract"    → True  (contains "legal")
        "What is the capital city?" → False
    """
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in HIGH_RISK_KEYWORDS)


def score_task_words(text: str) -> int:
    """
    Score the prompt based on the type of task being requested.

    Scans for known task words and adds points by category:
        Simple words  → +1 each
        Medium words  → +2 each
        Complex words → +3 each

    Args:
        text: The prompt text.

    Returns:
        int: Total task word score.

    Example:
        "Summarize and analyze this"
        → "summarize" (+2) + "analyze" (+3) = 5
    """
    text_lower = text.lower()
    score = 0

    for word in SIMPLE_TASK_WORDS:
        if word in text_lower:
            score += 1

    for word in MEDIUM_TASK_WORDS:
        if word in text_lower:
            score += 2

    for word in COMPLEX_TASK_WORDS:
        if word in text_lower:
            score += 3

    return score


def score_prompt_length(word_count: int) -> int:
    """
    Add complexity points based on how long the prompt is.

    Longer prompts usually mean more context, more nuance,
    and therefore a harder task for the model.

    Thresholds:
        Under 20 words  → 0 points  (very short, probably simple)
        20 to 80 words  → 2 points  (medium length)
        Over 80 words   → 5 points  (long, likely complex)

    Args:
        word_count: Number of words in the prompt.

    Returns:
        int: Length-based score addition.
    """
    if word_count < 20:
        return 0
    elif word_count < 80:
        return 2
    else:
        return 5


def check_structured_output(text: str) -> bool:
    """
    Detect if the prompt asks for structured output (JSON, table, etc.)

    Cheap models are less reliable at producing valid structured output.
    If detected, we add extra points to push toward a better model.

    Args:
        text: The prompt text.

    Returns:
        bool: True if structured output is requested.

    Example:
        "Give me the results as JSON" → True
        "What is 2 + 2?"             → False
    """
    text_lower = text.lower()
    return any(kw in text_lower for kw in STRUCTURED_OUTPUT_KEYWORDS)


def score_to_tier(score: int) -> int:
    """
    Convert a numeric complexity score into a model tier.

    Tier thresholds:
        Score 0-3  → Tier 1 (cheapest — Haiku)
        Score 4-8  → Tier 2 (balanced — Sonnet)
        Score 9+   → Tier 3 (best — Opus)

    These thresholds were chosen to balance cost vs quality.
    Simple questions score low → cheap model.
    Reasoning tasks score high → best model.

    Args:
        score: The total complexity score.

    Returns:
        int: 1, 2, or 3.
    """
    if score <= 3:
        return 1
    elif score <= 8:
        return 2
    else:
        return 3


def analyze_complexity(prompt: str) -> dict:
    """
    Analyze a prompt and return the recommended model tier.

    This is the MAIN function called by the rest of the app.

    Decision flow:
        1. Check for risk keywords → force Tier 3 if found
        2. Score task words
        3. Add length score
        4. Add structured output score
        5. Convert total score to tier
        6. Return tier + full explanation

    Args:
        prompt: The already-optimized prompt text.

    Returns:
        dict: {
            "tier"             : int (1, 2, or 3),
            "reason"           : str (human-readable explanation),
            "word_count"       : int,
            "complexity_score" : int,
            "risk_detected"    : bool,
            "structured_output": bool
        }

    Examples:
        "What is 2+2?"
        → tier 1, score 1, no risk

        "Analyze the competitive strategy across three markets"
        → tier 3, score 11, no risk

        "Review this medical diagnosis"
        → tier 3, risk override, score irrelevant
    """

    word_count = count_words(prompt)
    risk_detected = check_risk_keywords(prompt)
    structured_output = check_structured_output(prompt)

    # ---------------------------------------------------------------
    # RULE 1: Risk override
    # If any high-risk keyword is found, immediately return Tier 3.
    # We do not even bother scoring. Safety first.
    # ---------------------------------------------------------------
    if risk_detected:
        return {
            "tier": 3,
            "reason": (
                "High-risk domain detected (legal / medical / financial). "
                "Forced to Tier 3 — accuracy is non-negotiable here."
            ),
            "word_count": word_count,
            "complexity_score": 999,
            "risk_detected": True,
            "structured_output": structured_output
        }

    # ---------------------------------------------------------------
    # RULE 2: Score the prompt across all signals
    # ---------------------------------------------------------------
    score = 0
    score += score_task_words(prompt)
    score += score_prompt_length(word_count)
    if structured_output:
        score += 3

    # ---------------------------------------------------------------
    # RULE 3: Convert score to tier and build a reason string
    # ---------------------------------------------------------------
    tier = score_to_tier(score)

    tier_names = {
        1: "Tier 1 — claude-haiku (cheapest)",
        2: "Tier 2 — claude-sonnet (balanced)",
        3: "Tier 3 — claude-opus (best)"
    }

    reason = (
        f"Complexity score: {score}. "
        f"Word count: {word_count}. "
        f"Structured output: {structured_output}. "
        f"Routed to {tier_names[tier]}."
    )

    return {
        "tier": tier,
        "reason": reason,
        "word_count": word_count,
        "complexity_score": score,
        "risk_detected": False,
        "structured_output": structured_output
    }