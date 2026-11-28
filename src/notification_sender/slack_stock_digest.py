"""Compact Slack views of the stock report formats emitted by NotificationService."""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from src.notification_sender.slack_blocks import context, header, section
from src.report_language import SUPPORTED_REPORT_LANGUAGES, get_report_labels, localize_operation_advice


_TICKER = r"[^\s()|*]{1,32}"
_SCORE = r"\d{1,3}(?:\.\d+)?"
_STOCK_HEADING = re.compile(rf"^## (?!#)([^\n]+)\(({_TICKER})\)[ \t]*$", re.M)
_ICON = re.compile(r"^(?:(?::[\w+-]+:|[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f])\s*)+")
_MAX_NAMES_PER_GROUP = 8
_QUALITY_FLAGS = {
    "quote: fallback": {"en": "Fallback quotes", "zh": "行情使用回退数据", "ko": "대체 시세"},
    "quote: stale": {"en": "Stale quotes", "zh": "行情已过期", "ko": "오래된 시세"},
    "quote: missing": {"en": "Missing quotes", "zh": "行情缺失", "ko": "시세 누락"},
    "quote: unavailable": {"en": "Unavailable quotes", "zh": "行情不可用", "ko": "시세 사용 불가"},
    "technical: partial": {"en": "Partial technical data", "zh": "技术指标不完整", "ko": "기술 데이터 일부 누락"},
}


def _plain(value: str) -> str:
    value = re.sub(r"\[([^\]\n]+)\]\([^\s)]+\)", r"\1", value)
    value = value.replace("**", "").replace(r"\*", "*").strip()
    return _ICON.sub("", value.lstrip("> ").strip())


def _short(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


def _digest(title: str, meta: str, sections: list[str]) -> tuple[str, list[dict]]:
    title = _short(title, 150)
    blocks = [header(title)]
    if meta:
        blocks.append(context(meta))
    blocks.extend(section(item) for item in sections if item)
    fallback = "\n\n".join(filter(None, [title, meta, *sections]))
    return _short(fallback, 1900), blocks


def _bullets(text: str, label: str) -> list[str]:
    match = re.search(rf"{re.escape(label)}\**\s*[:：][ \t]*\n((?:[ \t]*[-•] [^\n]+(?:\n|$))+)", text)
    return [_plain(line.strip()[2:]) for line in match[1].splitlines()] if match else []


def _phase_row(text: str, labels: dict) -> tuple[str, str]:
    table = re.search(
        rf"^\|\s*{re.escape(labels['action_window_label'])}\s*\|[^\n]*\n"
        r"\|[ :|\-]+\|\s*\n\|[^\n|]*\|([^\n|]*)\|([^\n|]*)\|",
        text, re.M,
    )
    if not table:
        return "", ""
    return tuple("" if cell.strip() in {"-", "—", "N/A"} else _plain(cell) for cell in table.groups())


def _names(items: list[str]) -> str:
    shown = " · ".join(items[:_MAX_NAMES_PER_GROUP])
    return shown if len(items) <= _MAX_NAMES_PER_GROUP else f"{shown}  … +{len(items) - _MAX_NAMES_PER_GROUP}"


def _aggregate(content: str, title: str, language: str, labels: dict) -> tuple[str, list[dict]] | None:
    # Both the Jinja and fallback renderers emit the same marked-up ticker, action and score.
    # Parse the summary before the per-stock writeups, where advisory phrases may be repeated.
    headings = list(_STOCK_HEADING.finditer(content))
    overview = content[:headings[0].start()] if headings else content
    row = re.compile(
        rf"^[^\n*]*\*\*[^\n]*\(({_TICKER})\)\*\*[ \t]*[:：]?[ \t]*([^|\n]+)"
        rf"\|[ \t]*{re.escape(labels['score_label'])}[ \t]+({_SCORE})[ \t]*\|([^\n]*)$", re.M,
    )
    signals = [(code, _plain(action), score, _plain(tail)) for code, action, score, tail in row.findall(overview)]
    expected = re.search(rf"(\d+)\**\s*{re.escape(labels['stock_unit_compact'])}", overview)
    if not signals or (expected and int(expected[1]) != len(signals)):
        return None

    by_action = defaultdict(list)
    for ticker, action, score, _ in signals:
        by_action[action].append(f"{ticker} {score}")
    # Show reductions and exits first. Use the individual report labels because the
    # historical report headlines can combine several distinct ratings.
    order = ["Strong Sell", "Sell", "Reduce", "Strong Buy", "Buy", "Hold", "Watch"]
    ranks = {localize_operation_advice(action, language): i for i, action in enumerate(order)}
    ordered = sorted(by_action, key=lambda action: ranks.get(action, len(ranks)))
    signal_lines = [f"{action} ({len(by_action[action])})   {_names(by_action[action])}" for action in ordered]
    score_title = {"en": "Signals · sentiment scores", "zh": "信号 · 情绪评分", "ko": "신호 · 심리 점수"}[language]
    sections = [score_title + "\n" + "\n".join(signal_lines)]

    details = {
        match[2]: content[match.end():headings[i + 1].start() if i + 1 < len(headings) else len(content)]
        for i, match in enumerate(headings)
    }
    actions_now = defaultdict(list)
    quality = Counter()
    focus = ""
    first_limitation = ""
    for ticker, _, _, tail in sorted(signals, key=lambda item: ranks.get(item[1], len(ranks))):
        detail = details.get(ticker, "")
        current, check = _phase_row(detail, labels)
        if current:
            actions_now[current].append(ticker)
        limits = _bullets(detail, labels["data_limitations_label"])
        quality.update(set(_QUALITY_FLAGS[v.lower()][language] for v in limits if v.lower() in _QUALITY_FLAGS))
        if limits and not first_limitation:
            first_limitation = f"{ticker} — {_short(limits[0], 180)}"
        if not focus:
            risks = _bullets(detail, labels["risk_alerts_label"])
            if risks:
                risk = re.sub(r"^Risk \d+\s*[:：]\s*", "", risks[0], flags=re.I)
                focus = f"{labels['risk_alerts_label']} · {ticker}\n{_short(risk, 220)}"
                if check:
                    focus += f"\n{labels['next_check_time_label']} · {_short(check, 90)}"
            elif tail and title.startswith(labels["brief_title"]):
                focus = f"{labels['core_conclusion_heading']} · {ticker}\n{_short(tail, 180)}"

    if actions_now:
        groups = list(actions_now.items())
        action_lines = [f"{_names(tickers)} — {_short(action, 145)}" for action, tickers in groups[:3]]
        if len(groups) > 3:
            action_lines.append(f"… +{sum(len(tickers) for _, tickers in groups[3:])}")
        sections.append(labels["immediate_action_label"] + "\n" + "\n".join(action_lines))
    if focus:
        sections.append(focus)
    if quality:
        sections.append(labels["data_limitations_label"] + "\n" + " · ".join(
            f"{flag} ({count}/{len(signals)})" for flag, count in sorted(quality.items())
        ))
    elif first_limitation:
        sections.append(labels["data_limitations_label"] + "\n" + first_limitation)
    market = re.search(r"(?:Market status|市场状态)\s*[:：]\s*([^\n]+)", overview)
    unit = "stock" if language == "en" and len(signals) == 1 else labels["stock_unit"]
    count = f"{len(signals)} {unit}"
    meta = " · ".join(filter(None, [_short(_plain(market[1]), 120) if market else "", count]))
    return _digest(title, meta, sections)


def _single(content: str, heading: re.Match) -> tuple[str, list[dict]] | None:
    for language in SUPPORTED_REPORT_LANGUAGES:
        labels = get_report_labels(language)
        meta = re.search(
            rf"^>\s*(\d{{4}}-\d\d-\d\d \d\d:\d\d)\s*\|\s*{re.escape(labels['score_label'])}"
            rf"[:：]\s*\*\*({_SCORE})\*\*\s*\|\s*([^\n]*)", content, re.M,
        )
        if meta:
            break
    else:
        return None
    core = re.search(
        rf"^### [^\n]*{re.escape(labels['core_conclusion_heading'])}\s*\n+\*\*([^*\n]+)\*\*[:：]\s*([^\n]+)",
        content, re.M,
    )
    if not core:
        return None
    title = f"{heading[2]} · {_plain(heading[1])}"
    details = [f"{_plain(core[1])} · {labels['score_label']} {meta[2]} · {_plain(meta[3])}",
               _short(_plain(core[2]), 280)]
    phase = re.search(r"^- (phase|阶段)[:：]([^\n]+)", content, re.M)
    if phase:
        details.append(_short(phase[0][2:].replace(" | ", " · "), 150))
    risks = _bullets(content, labels["risk_alerts_label"])
    if risks:
        details.append(f"{labels['risk_alerts_label']}\n{_short(risks[0], 220)}")
    limitations = re.findall(r"^- (?:limitation|限制)[:：]\s*([^\n]+)", content, re.M)
    if limitations:
        details.append(f"{labels['data_limitations_label']}\n" + " · ".join(_short(_plain(v), 100) for v in limitations[:2]))
    return _digest(title, meta[1], details)


def build_stock_digest(content: str) -> tuple[str, list[dict]] | None:
    """Return a Slack digest for a recognized report, or defer to the generic sender."""
    content = content.replace("\r\n", "\n").strip()
    single = _STOCK_HEADING.match(content)
    if single:
        return _single(content, single)
    heading = re.match(r"^#\s+[^\n]*?(\d{4}-\d\d-\d\d)\s+([^\n]+)", content)
    if not heading:
        return None
    for language in SUPPORTED_REPORT_LANGUAGES:
        labels = get_report_labels(language)
        if heading[2].strip() in (labels["dashboard_title"], labels["brief_title"]):
            return _aggregate(content, f"{heading[2].strip()} · {heading[1]}", language, labels)
    return None
