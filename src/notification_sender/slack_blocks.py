"""Slack Block Kit text elements shared by scheduled report renderers."""


def _plain_text(text: str, limit: int) -> dict:
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return {"type": "plain_text", "text": text, "emoji": False}


def header(text: str) -> dict:
    return {"type": "header", "text": _plain_text(text, 150)}


def section(text: str) -> dict:
    return {"type": "section", "text": _plain_text(text, 3000)}


def context(text: str) -> dict:
    return {"type": "context", "elements": [_plain_text(text, 2000)]}
