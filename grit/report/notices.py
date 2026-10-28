"""Standing notices on a brief. A notice is a label, not a fact about a particular week."""

DISCLAIMER = (
    "GRIT-Scan AI-generated suggestions for consideration. "
    "The scan can miss a source, misread a page, or leave out context. "
    "A named person reviews this before anyone acts on it."
)

SUGGESTION_LABEL = "Suggestion for the business area to consider."
HYPOTHESIS_LABEL = "Hypothesis: to validate with BAs."


def suggestion_line(action: str) -> str:
    """Mark an existing action as a suggestion. The function adds no action of its own."""
    text = " ".join(str(action or "").split())
    if not text:
        return ""
    if text.startswith(SUGGESTION_LABEL):
        return text
    return f"{SUGGESTION_LABEL} {text}"
