"""Render generated market recaps as short, source-backed Slack notifications."""

from __future__ import annotations

import re
from decimal import Decimal

from src.notification_sender.slack_blocks import header, section
from src.share_image import _market_segments, _parse_index_bullets, _parse_tables, _section


_TITLE = re.compile(r"(?:market (?:recap|review)|大盘复盘|市场复盘|시황\s*리뷰)\s*$", re.I)
_DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
_METADATA = re.compile(r"^\[dsa-market-region\]:\s*#\s*\(([a-z,]+)\)\s*$", re.M | re.I)
_SIGNAL = re.compile(r"(?:market signal|盘面信号|市场信号|시장 신호)\s*[:：]\s*(\d{1,3}/100(?:\s*[(（][^)）\n]+[)）])?)", re.I)
_METRIC_LINE = re.compile(
    r"^[-*]\s+\*{0,2}(?:Market Signal|Drivers|Guidance|Breadth|盘面信号|市场信号|信号依据|操作建议|"
    r"시장 신호|동인|운용 제안|가이던스)\b", re.I,
)
_COPY = {
    "en": ("Market signal", "Index moves", "Outlook", "Risk", "Data coverage",
           "Index quotes unavailable in this report."),
    "zh": ("盘面信号", "主要指数", "展望", "风险", "数据范围", "本报告未提供指数行情。"),
    "ko": ("시장 신호", "주요 지수", "전망", "위험", "데이터 범위", "이 보고서에는 지수 시세가 없습니다."),
}
_REGIONS = {
    "en": {"us": "US", "cn": "A-share", "hk": "HK", "jp": "Japan", "kr": "Korea"},
    "zh": {"us": "美股", "cn": "A股", "hk": "港股", "jp": "日股", "kr": "韩股"},
    "ko": {"us": "미국", "cn": "중국 A주", "hk": "홍콩", "jp": "일본", "kr": "한국"},
}


def _plain(line: str) -> str:
    # The Slack export uses :emoji_names: where the renderer emits Unicode markers.
    line = re.sub(r":[a-z][a-z0-9_+-]*:", "", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line).replace("**", "").strip("*` ")
    line = re.sub(r"^\s*(?:#{1,6}\s+|>\s*|[-*•]\s+)", "", line)
    return re.sub(r"^[^\w+−-]+", "", line).strip()


def _excerpt(body: str, limit: int = 230) -> str:
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(("|", "#", "---", "[dsa-", ":page_facing_up:")) or _METRIC_LINE.match(line):
            continue
        value = _plain(line)
        if not value or re.match(r"^(?:Review Time|复盘时间|仅供参考|For reference only)\b", value, re.I):
            continue
        first = re.split(r"(?<!\b[A-Z]\.)(?<=[.!?])\s+(?=[A-Z])|(?<=[。！？])", value, maxsplit=1)[0]
        if len(first) <= limit:
            return first
        shortened = first[:limit - 1]
        if " " in shortened:
            shortened = shortened.rsplit(" ", 1)[0]
        return shortened.rstrip(" ,，") + "…"
    return ""


def _index_moves(report: str, language: str) -> list[str]:
    index_section = _section(report, "index commentary", "major indices", "指数结构", "指数表现", "主要指数", "주요 지수", "지수 구조")
    quotes = []
    for table in _parse_tables(index_section or report):
        columns = [column.casefold() for column in table.headers]
        name = next((i for i, c in enumerate(columns) if c in {"index", "指数", "지수"}), None)
        if name is None:
            continue
        close = next((i for i, c in enumerate(columns) if c in {"last", "close", "最新", "收盘", "종가", "최신"}), None)
        move = next((i for i, c in enumerate(columns) if c in {"change %", "change (%)", "涨跌幅", "등락률", "change"}), None)
        if close is not None or move is not None:
            for row, raw in zip(table.rows, table.raw_rows):
                raw_move = raw[move] if move is not None and move < len(raw) else ""
                quotes.append((row[name], row[close] if close is not None else "", raw_move))
            break
    if not quotes:
        quotes = [(name, value, change) for name, value, change, _ in _parse_index_bullets(index_section)]

    rows = []
    for name, value, change in quotes[:8]:
        if not name:
            continue
        if language == "en" and re.search(r"[\u3400-\u9fff]", name):
            from data_provider.us_index_mapping import localize_archived_index_names
            name = localize_archived_index_names(name, "en")
        close = value.replace(",", "").strip()
        pct = re.search(r"([+−-]?\d+(?:\.\d+)?)\s*%", change)
        values = []
        if re.fullmatch(r"\d{1,16}(?:\.\d{1,8})?", close):
            values.append(format(Decimal(close), ",f"))
        if pct:
            percent = pct[1].replace("−", "-")
            values.append(("+" if percent[0] not in "+-" and Decimal(percent) > 0 else "") + percent + "%")
        if values:
            rows.append(" · ".join([name, *values]))
    return rows


def _one_market(report: str, region: str) -> tuple[str, list[str]]:
    titles = [_plain(line) for line in report.splitlines() if len(line) <= 150 and _TITLE.search(line)]
    if not titles:
        return "", []
    title = next((t for t in titles if _DATE.search(t)), titles[0])
    language = "en" if "market " in title.lower() else "ko" if "리뷰" in title else "zh"
    # A dated generic heading can sit below a region-specific heading.
    date = _DATE.search(title)
    caption = _DATE.sub("", title).strip()
    if _TITLE.fullmatch(caption):
        scoped = next((t for t in titles if not _TITLE.fullmatch(_DATE.sub("", t).strip())), "")
        prefix = _REGIONS[language].get(region, "")
        caption = _DATE.sub("", scoped).strip() or (prefix + (" " if language != "zh" and prefix else "") + caption)
    title = f"{caption} · {date[0]}" if date else caption
    signal_label, indices_label, outlook_label, risk_label, coverage_label, no_quotes = _COPY[language]
    content = []
    signal = _SIGNAL.search(report.replace("**", ""))
    summary = _excerpt(_section(report, "market summary", "盘面总览", "市场综述", "시장 요약"))
    if not summary:
        quoted = next((line for line in report.splitlines() if line.startswith("> ")
                       and not re.search(r"next market recap|下一市场|다음 시장", line, re.I)), "")
        summary = _excerpt(quoted)
    lead = []
    if signal:
        lead.append(f"{signal_label}  {signal[1]}")
    if summary:
        lead.append(summary)
    if lead:
        content.append("\n".join(lead))
    moves = _index_moves(report, language)
    if moves:
        content.append("\n".join([indices_label, *moves]))
    elif re.search(r"no index data|benchmark move is unavailable|暂无指数数据|基准指数行情暂缺|지수 데이터가 없", report, re.I):
        content.append(no_quotes)
    outlook = _excerpt(_section(report, "outlook", "明日观察", "明日交易计划", "전망", "다음 거래"))
    if not outlook:
        guidance = re.search(r"(?:Guidance|操作建议|운용 제안)\*{0,2}\s*[:：]([^\n]+)", report, re.I)
        if guidance:
            outlook = _excerpt(guidance[1])
    risk = _excerpt(_section(report, "risk alert", "risk warning", "风险提示", "风险警报", "리스크", "위험 경고"))
    coverage = _excerpt(_section(report, "data limitations", "data coverage", "数据局限", "数据范围", "데이터 한계"))
    if not coverage:
        disclosure = re.search(r"(?:^|(?<=[.!?。！？])\s+)((?:Due to data limitations|由于数据限制)[^\n]+)", report, re.M | re.I)
        coverage = _excerpt(disclosure[1]) if disclosure else ""
    notes = ((outlook_label, outlook), (risk_label, risk), (coverage_label, coverage))
    if any(value for _, value in notes):
        content.append("\n\n".join(f"{label}\n{value}" for label, value in notes if value))
    return title, content


def build_market_digest(content: str) -> tuple[str, list[dict]] | None:
    """Keep each market and its session separate, including five-market runs."""
    metadata = _METADATA.search(content)
    report = _METADATA.sub("", content).strip()
    first_line = report.partition("\n")[0]
    if not _TITLE.search(first_line) or len(first_line) > 150:
        return None
    segments = _market_segments(report)
    markets = [s.markdown for s in segments] if segments else [report]
    if len(markets) > 5:
        return None
    blocks = []
    fallback = []
    for market in markets:
        region = metadata[1] if metadata and len(markets) == 1 else ""
        title, paragraphs = _one_market(market, region)
        if not paragraphs:
            if len(markets) > 1 and len(market.strip().splitlines()) == 1:
                continue  # a standalone wrapper heading ahead of the regional headings
            return None  # preserve the complete message if one market cannot be summarized
        blocks.append(header(title))
        blocks.extend(section(paragraph) for paragraph in paragraphs)
        if len(markets) == 1:
            fallback.append("\n".join([title, *paragraphs]))
        else:
            fallback.append(f"{title}\n{paragraphs[0][:210]}")
    if not blocks:
        return None
    return "\n\n".join(fallback)[:1900], blocks
