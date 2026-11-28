# -*- coding: utf-8 -*-
"""
Slack 发送提醒服务

职责：
1. 通过 Slack Bot API 或 Incoming Webhook 发送 Slack 消息
   （同时配置时优先使用 Bot API，确保文本与图片发送到同一频道）
"""
import html
import ipaddress
import json
import logging
import re
from typing import Optional
from urllib.parse import urlsplit

import requests

from src.config import Config
from src.formatters import format_slack_mrkdwn, strip_hidden_markdown_metadata
from src.notification_sender.slack_market_digest import build_market_digest
from src.notification_sender.slack_stock_digest import build_stock_digest
from src.notification_sender.slack_blocks import context
from src.services.report_publications import RUN_MARKER, chart_links, link, publication, quote_evidence

logger = logging.getLogger(__name__)

# Slack Block Kit 中单个 section block 的 text 字段上限为 3000 字符
_BLOCK_TEXT_LIMIT = 3000
# Slack chat.postMessage / Webhook 的 text 字段上限约 40000 字符，保守取 39000
_TEXT_LIMIT = 39000

# The scheduler's optional merged notification uses these outer headings in every language.
_MARKET_WRAPPER = "# 📈 大盘复盘\n\n"
_STOCK_WRAPPER = "# 🚀 个股决策仪表盘\n\n"


def _text_parts(text: str, limit: int):
    """Keep whole lines together where possible, including every source character."""
    while len(text) > limit:
        split = text.rfind("\n", limit // 2, limit) + 1
        if not split:
            split = text.rfind(" ", limit // 2, limit) + 1 or limit
        yield text[:split]
        text = text[split:]
    if text:
        yield text


def _history_url(value: object) -> str:
    if not isinstance(value, str) or not value or len(html.escape(value, quote=False)) > 1900:
        return ""
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c in '<>|\\' for c in value):
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username is not None:
            return ""
        host = parsed.hostname.encode("idna").decode("ascii")
        if ":" in host:
            ipaddress.IPv6Address(host)
        elif not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                     for label in host.rstrip(".").split(".")):
            return ""
        # Accessing port also rejects non-numeric and out-of-range ports.
        if parsed.port == 0:
            return ""
    except (ValueError, UnicodeError):
        return ""
    return value


def _report_payload(digests: list, reports_url: str, language: Optional[str]) -> dict:
    blocks = []
    fallbacks = []
    for fallback, report_blocks in digests:
        if blocks:
            blocks.append({"type": "divider"})
        blocks.extend(report_blocks)
        fallbacks.append(fallback)
    if reports_url:
        if language not in ("en", "ko", "zh"):
            title = blocks[0]["text"]["text"]
            language = "en"
            if re.search(r"[가-힣]", title):
                language = "ko"
            elif re.search(r"[\u3400-\u9fff]", title):
                language = "zh"
        label = {"ko": "보고서 기록 열기", "zh": "查看历史报告", "en": "Open report history"}[language]
        blocks.append({"type": "context", "elements": [{
            "type": "mrkdwn", "verbatim": True,
            "text": f"<{html.escape(reports_url, quote=False)}|{label}>",
        }]})
        fallbacks.append(f"{label}: {reports_url}")
    return {"text": "\n\n".join(fallbacks), "blocks": blocks}


def _published_digest(digest, record, reports_url, terminal_url):
    fallback, blocks = digest
    language = record.get('language')
    words = {
        'zh': ('发布于', '与覆盖相同标的的上次报告比较', '评分未变', '打开此报告', '图表', '报价时间未知', '最旧报价', '基准代理'),
        'ko': ('발행', '동일 종목 범위의 직전 보고서 대비', '평가 변동 없음', '이 보고서 열기', '차트', '시세 시각 미상', '가장 오래된 시세', '벤치마크 대용'),
        'en': ('Published', 'Since previous report with matching coverage', 'Ratings unchanged', 'Open this report', 'Charts',
               'quote time unreported', 'oldest quote', 'benchmark proxy'),
    }
    words = words.get(language, words['en'])
    published, since, unchanged, open_label, chart_label, unknown_age, oldest, proxy = words
    info = [f"{published} {record['generated_at'][:16].replace('T', ' ')} UTC"]
    if record.get('previous_id') and (record['stocks'] or record.get('signals')):
        changes = record.get('changes') or []
        line = ' · '.join(changes[:4]) if changes else unchanged
        if len(changes) > 4:
            line += f" (+{len(changes) - 4})"
        info.append(f"{since} · {line}")
    evidence = quote_evidence(record['stocks'], record['generated_at'])
    if evidence:
        providers = sorted({q['source'] for q in evidence if q['source']})
        ages = [q['age_minutes'] for q in evidence if q['age_minutes'] is not None]
        coverage = [', '.join(providers[:3])] if providers else []
        if ages:
            coverage.append(f"{oldest} {max(ages)} min")
        if len(ages) < len(evidence):
            coverage.append(f"{unknown_age} {len(evidence)-len(ages)}/{len(evidence)}")
        for status in ('stale', 'missing', 'unavailable'):
            affected = sum(q['status'] == status for q in evidence)
            if affected:
                coverage.append(f"{status} {affected}/{len(evidence)}")
        if coverage:
            info.append(' · '.join(coverage))
    info_text = '\n'.join(info)
    blocks = [blocks[0], context(info_text), *blocks[1:]]
    fallback += '\n\n' + info_text
    destinations = []
    exact = link(reports_url, report=record['id'])
    if exact:
        destinations.append((open_label, exact))
    charts = chart_links(record, _history_url(terminal_url))
    for ticker, url in list(charts.items())[:8]:
        label = f"{ticker} ({proxy})" if record['kind'] == 'market' else ticker
        destinations.append((f"{chart_label} · {label}", url))
    if destinations:
        native = '   '.join(f"<{html.escape(u, quote=False)}|{html.escape(label)}>" for label, u in destinations)
        # A single context element is limited to 2000 characters.
        if len(native) > 1900:
            label, url = destinations[0]
            native = f"<{html.escape(url, quote=False)}|{html.escape(label)}>"
        blocks.append({'type': 'context', 'elements': [{'type': 'mrkdwn', 'verbatim': True, 'text': native}]})
        if exact:
            fallback += f"\n{open_label}: {exact}"
    return fallback, blocks


def build_slack_payloads(content: str, *, reports_url: str = "", language: Optional[str] = None,
                         publications: Optional[dict] = None, terminal_url: str = "") -> list[dict]:
    """Build complete text notifications without contacting Slack."""
    clean = content.replace("\r\n", "\n").strip()
    if not clean:
        return []
    parts = [clean]
    if clean.startswith(_MARKET_WRAPPER):
        market, separator, stocks = clean[len(_MARKET_WRAPPER):].partition("\n\n---\n\n" + _STOCK_WRAPPER)
        parts = [market, stocks] if separator else [market]
    elif clean.startswith(_STOCK_WRAPPER):
        parts = [clean[len(_STOCK_WRAPPER):]]

    history = _history_url(reports_url)
    digests = []
    resolved = []
    for part in parts:
        run_ids = RUN_MARKER.findall(part)
        record = (publications or {}).get(run_ids[-1]) if run_ids else None
        # A retry or another channel may render the same run again; the notification must
        # describe the saved body to which its link points.
        source = record.get('markdown') if record else None
        text = RUN_MARKER.sub('', source or part).strip()
        digest = build_stock_digest(strip_hidden_markdown_metadata(text)) or build_market_digest(text)
        if record and not digest:
            # Even an unfamiliar report layout retains an exact link to its full contents.
            from src.notification_sender.slack_blocks import header, section
            excerpt = strip_hidden_markdown_metadata(text).strip()[:950]
            digest = (excerpt, [header(record['title']), section(excerpt or record['title'])])
        if digest and record:
            digest = _published_digest(digest, record, history, terminal_url)
        digests.append(digest)
        resolved.append(bool(record))
    if all(digests):
        return [_report_payload(digests, "" if all(resolved) else history, language)]
    if not any(digests):
        parts, digests = [clean], [None]

    payloads = []
    for index, (part, digest) in enumerate(zip(parts, digests)):
        if digest:
            payloads.append(_report_payload([digest], "" if resolved[index] else history, language))
            continue
        # Preserve unrecognized alerts, diagnostics and report formats in full.
        # Escape user/LLM-provided Slack control sequences before formatting Markdown.
        if len(parts) == 2:
            part = (_MARKET_WRAPPER if index == 0 else _STOCK_WRAPPER) + part
        visible = strip_hidden_markdown_metadata(part).strip()
        if not visible:
            continue
        native = format_slack_mrkdwn(html.escape(visible, quote=False))
        # Markdown links can produce Slack angle-bracket syntax. Only HTTP(S)
        # links should become clickable, never a mention from report prose.
        native = re.sub(r"<(?!https?://)([^<>\n]+)>", r"&lt;\1&gt;", native, flags=re.I)
        for chunk in _text_parts(native, _TEXT_LIMIT):
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": piece, "verbatim": True}}
                for piece in _text_parts(chunk, _BLOCK_TEXT_LIMIT)
            ]
            plain = re.sub(r"<(https?://[^<>|]+)\|([^<>]+)>", r"\2 (\1)", chunk, flags=re.I)
            plain = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", plain)
            payloads.append({"text": html.unescape(plain), "blocks": blocks})
    return payloads


class SlackSender:

    def __init__(self, config: Config):
        """
        初始化 Slack 配置

        Args:
            config: 配置对象
        """
        self._slack_webhook_url = getattr(config, 'slack_webhook_url', None)
        self._slack_bot_token = getattr(config, 'slack_bot_token', None)
        self._slack_channel_id = getattr(config, 'slack_channel_id', None)
        self._webhook_verify_ssl = getattr(config, 'webhook_verify_ssl', True)
        self._slack_reports_url = getattr(config, 'slack_reports_url', None)
        self._terminal_url = getattr(config, 'terminal_chart_url', None)
        self._slack_report_language = getattr(config, 'report_language', None)

    @property
    def _use_bot(self) -> bool:
        """Bot 配置完整时优先走 Bot API，保证文本和图片使用同一传输通道。"""
        return bool(self._slack_bot_token and self._slack_channel_id)

    def _is_slack_configured(self) -> bool:
        """检查 Slack 配置是否完整（支持 Webhook 或 Bot API）"""
        return self._use_bot or bool(self._slack_webhook_url)

    def send_to_slack(self, content: str, *, timeout_seconds: Optional[float] = None) -> bool:
        """
        推送消息到 Slack（支持 Webhook 和 Bot API）

        传输优先级与 _send_slack_image() 保持一致：Bot > Webhook，
        避免文本走 Webhook、图片走 Bot 导致消息落入不同频道。

        Args:
            content: Markdown 格式的消息内容

        Returns:
            是否发送成功
        """
        if not self._is_slack_configured():
            logger.warning("Slack 配置不完整，跳过推送")
            return False
        records = {}
        for run_id in set(RUN_MARKER.findall(content)):
            try:
                records[run_id] = publication(run_id)
            except Exception:
                logger.warning("Published report lookup failed; using standard Slack report", exc_info=True)
        payloads = build_slack_payloads(
            content, reports_url=self._slack_reports_url, language=self._slack_report_language,
            publications=records, terminal_url=self._terminal_url,
        )
        if not payloads:
            logger.warning("Slack 消息内容为空，跳过推送")
            return False

        send = self._send_slack_bot if self._use_bot else self._send_slack_webhook
        for payload in payloads:
            payload.update(mrkdwn=False, unfurl_links=False, unfurl_media=False)
            if not send(payload, timeout_seconds=timeout_seconds):
                return False
        return True

    def _send_slack_webhook(self, payload: dict, *, timeout_seconds: Optional[float] = None) -> bool:
        """
        使用 Incoming Webhook 发送消息到 Slack

        Args:
            payload: 带通知预览和 Block Kit 区块的消息

        Returns:
            是否发送成功
        """
        try:
            response = requests.post(
                self._slack_webhook_url,
                data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                headers={'Content-Type': 'application/json; charset=utf-8'},
                timeout=timeout_seconds or 15,
                verify=self._webhook_verify_ssl,
            )
            if response.status_code == 200 and response.text == "ok":
                logger.info("Slack Webhook 消息发送成功")
                return True
            logger.error(f"Slack Webhook 发送失败: HTTP {response.status_code} {response.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"Slack Webhook 发送异常: {e}")
            return False

    def _send_slack_bot(self, payload: dict, *, timeout_seconds: Optional[float] = None) -> bool:
        """
        使用 Bot API (chat.postMessage) 发送消息到 Slack

        Args:
            payload: 带通知预览和 Block Kit 区块的消息

        Returns:
            是否发送成功
        """
        try:
            headers = {
                'Authorization': f'Bearer {self._slack_bot_token}',
                'Content-Type': 'application/json; charset=utf-8',
            }
            response = requests.post(
                'https://slack.com/api/chat.postMessage',
                data=json.dumps({"channel": self._slack_channel_id, **payload}, ensure_ascii=False).encode('utf-8'),
                headers=headers,
                timeout=timeout_seconds or 15,
            )
            result = response.json()
            if result.get("ok"):
                logger.info("Slack Bot 消息发送成功")
                return True
            logger.error(f"Slack Bot 发送失败: {result.get('error', 'unknown')}")
            return False
        except Exception as e:
            logger.error(f"Slack Bot 发送异常: {e}")
            return False

    def _send_slack_image(self, image_bytes: bytes, fallback_content: str = "") -> bool:
        """
        发送图片到 Slack

        Bot 模式下使用 files.getUploadURLExternal + files.completeUploadExternal
        (Slack 新版文件上传 API)；Webhook 模式下回退为文本。

        Args:
            image_bytes: PNG 图片字节
            fallback_content: 图片发送失败时的回退文本

        Returns:
            是否发送成功
        """
        # Bot 模式：使用新版文件上传 API
        if self._use_bot:
            headers = {'Authorization': f'Bearer {self._slack_bot_token}'}
            try:
                # Step 1: 获取上传 URL
                resp1 = requests.post(
                    'https://slack.com/api/files.getUploadURLExternal',
                    headers=headers,
                    data={
                        'filename': 'report.png',
                        'length': len(image_bytes),
                    },
                    timeout=30,
                )
                result1 = resp1.json()
                if not result1.get("ok"):
                    logger.error("Slack 获取上传 URL 失败: %s", result1.get('error', 'unknown'))
                    raise RuntimeError(result1.get('error', 'unknown'))

                upload_url = result1['upload_url']
                file_id = result1['file_id']

                # Step 2: 上传文件内容（raw body，不能用 multipart）
                resp2 = requests.post(
                    upload_url,
                    data=image_bytes,
                    headers={'Content-Type': 'application/octet-stream'},
                    timeout=30,
                )
                if resp2.status_code != 200:
                    logger.error("Slack 文件上传失败: HTTP %s", resp2.status_code)
                    raise RuntimeError(f"HTTP {resp2.status_code}")

                # Step 3: 完成上传并分享到频道
                resp3 = requests.post(
                    'https://slack.com/api/files.completeUploadExternal',
                    headers={**headers, 'Content-Type': 'application/json'},
                    json={
                        'files': [{'id': file_id, 'title': '股票分析报告'}],
                        'channel_id': self._slack_channel_id,
                    },
                    timeout=30,
                )
                result3 = resp3.json()
                if result3.get("ok"):
                    logger.info("Slack Bot 图片发送成功")
                    return True
                logger.error("Slack 完成上传失败: %s", result3.get('error', 'unknown'))
            except Exception as e:
                logger.error("Slack Bot 图片发送异常: %s", e)

        # Webhook 模式或 Bot 上传失败：回退为文本
        if fallback_content:
            logger.info("Slack 图片不支持或失败，回退为文本发送")
            return self.send_to_slack(fallback_content)

        logger.warning("Slack 图片发送失败，且无回退内容")
        return False
