"""Immutable report publications shared by the scheduler, web reader and Slack."""

import hashlib
import json
import logging
import re
from datetime import date as calendar_date, datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.analysis_context_pack_overview import extract_analysis_context_pack_overview
from src.schemas.decision_action import display_operation_advice_for_result
from src.storage import AnalysisHistory, ReportPublication, get_db

logger = logging.getLogger(__name__)
RUN_MARKER = re.compile(r"^\[dsa-report-run\]: # \(([0-9a-f]{32})\)$", re.M)
_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.-]{0,15}\Z")
_DATE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")


def link(base, **params):
    """Keep the configured origin and path. Replace only the named query keys."""
    if not isinstance(base, str) or not base or any(c.isspace() or c in '<>|\\' for c in base):
        return None
    try:
        u = urlsplit(base)
        if u.scheme not in ('https', 'http') or not u.hostname or u.username is not None or u.port == 0:
            return None
    except ValueError:
        return None
    query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k not in params]
    return urlunsplit((u.scheme, u.netloc, u.path or '/', urlencode(query + list(params.items())), ''))


def marked(markdown, run_id):
    return f"{markdown.rstrip()}\n\n[dsa-report-run]: # ({run_id})" if run_id else markdown


def _id(parts):
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()[:32]


def _block(result, key):
    overview = getattr(result, 'analysis_context_pack_overview', None)
    if not isinstance(overview, dict):
        return {}
    return next((b for b in overview.get('blocks') or [] if isinstance(b, dict) and b.get('key') == key), {})


def chart_cutoff(day, phase):
    """Resolve a completed daily bar. A pre-close report must not disclose that day's close."""
    session = calendar_date.fromisoformat(day)
    phase = phase if isinstance(phase, dict) else {}
    effective = phase.get('effective_daily_bar_date')
    market_day = phase.get('session_date')
    try:
        if market_day:
            session = min(session, calendar_date.fromisoformat(str(market_day)[:10]))
        bar = min(calendar_date.fromisoformat(str(effective)[:10]), session) if effective else None
    except ValueError:
        bar = None
    if phase.get('phase') in {'premarket', 'intraday', 'lunch_break', 'closing_auction', 'unknown'} or not phase.get('phase'):
        # Terminals resolve weekends and holidays to the last actual bar on or before this cutoff.
        prior = session - timedelta(days=1)
        return min(bar, prior).isoformat() if bar else prior.isoformat()
    return (bar or session).isoformat()


def stock_rows(results, language, day):
    rows = {}
    for result in results:
        code = str(result.code)
        q = _block(result, 'quote')
        phase = getattr(result, 'market_phase_summary', None)
        rows[code] = {
            'rating': display_operation_advice_for_result(result, report_language=language),
            'score': result.sentiment_score,
            'chart_date': chart_cutoff(day, phase),
            'market': phase.get('market') if isinstance(phase, dict) else None,
            'quote': {k: q[k] for k in ('status', 'source', 'provider_timestamp', 'fetched_at') if q.get(k)},
            'technical': _block(result, 'technical').get('status'),
        }
    return rows


def _changes(now, prior):
    changes = []
    for code, row in now['stocks'].items():
        old = prior['stocks'].get(code)
        if old and (old['rating'], old['score']) != (row['rating'], row['score']):
            changes.append(f"{code}: {old['rating']} {old['score']} → {row['rating']} {row['score']}")
        elif not old:
            changes.append(f"+ {code}")
    changes.extend(f"− {code}" for code in prior['stocks'] if code not in now['stocks'])
    for region, score in now['signals'].items():
        old = prior['signals'].get(region)
        if old is None:
            changes.append(f"{region.upper()}: — → {score}/100")
        elif old != score:
            changes.append(f"{region.upper()}: {old} → {score}/100")
    changes.extend(f"{region.upper()}: {score}/100 → —" for region, score in prior['signals'].items()
                   if region not in now['signals'])
    return changes


def _capture(key, series, title, date, markdown, details):
    run_id = _id(key)
    with get_db().get_session() as db:
        # Atomically choose the predecessor and publish across scheduler/server processes.
        if db.get_bind().dialect.name == 'sqlite':
            db.execute(text('BEGIN IMMEDIATE'))
        existing = db.get(ReportPublication, run_id)
        if existing is not None:
            return run_id, existing.markdown
        previous = (db.query(ReportPublication).filter_by(series=series)
                    .order_by(ReportPublication.created_at.desc(), ReportPublication.id.desc()).first())
        details['previous_id'] = previous.id if previous else None
        details['changes'] = _changes(details, json.loads(previous.details_json)) if previous else []
        db.add(ReportPublication(id=run_id, series=series, as_of=date, title=title[:180],
                                 markdown=RUN_MARKER.sub('', markdown).strip(),
                                 details_json=json.dumps(details, ensure_ascii=False)))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = db.get(ReportPublication, run_id)
            if existing is None:
                raise
            return run_id, existing.markdown
    return run_id, markdown


def publish_stocks(markdown, results, language, variant, *, single=False):
    """The same analysis/format receives the same link across save, preview and delivery."""
    if not results or any(not isinstance(getattr(r, 'query_id', None), str) or not r.query_id for r in results):
        return markdown
    try:
        members = sorted((str(r.code), r.query_id) for r in results)
        kind = 'single' if single else 'stocks'
        key = [kind, variant, language, members]
        scope = sorted({code for code, _ in members})
        series = _id([kind, variant, language, scope])
        header = next((s.lstrip('# ').strip() for s in markdown.splitlines() if s.startswith('#')), 'Report')
        date = _DATE.search(markdown)
        day = date[0] if date else datetime.now().astimezone().date().isoformat()
        details = dict(kind=kind, language=language, stocks=stock_rows(results, language, day), signals={})
        run_id, archived = _capture(key, series, header, day, markdown, details)
        return marked(archived, run_id)
    except Exception:
        logger.exception("Could not archive the stock publication; original report is retained")
        return markdown


def publish_market(markdown, query_id, payload):
    try:
        regions = payload.get('markets') or {payload['region']: payload}
        language = payload.get('language') or 'en'
        series = _id(['market', language, sorted(regions)])
        signals = {region: r['market_light']['score'] for region, r in regions.items()
                   if isinstance((r.get('market_light') or {}).get('score'), int)}
        if len(regions) == 1 and not signals:
            match = re.search(r"(?:Market Signal|盘面信号|市场信号|시장 신호)[*\s:：]*(\d{1,3})/100",
                              markdown, re.I)
            if match and int(match[1]) <= 100:
                signals[next(iter(regions))] = int(match[1])
        md_date = _DATE.search(markdown)
        day = next((str(r['date'])[:10] for r in regions.values() if r.get('date')), None)
        day = day or (md_date[0] if md_date else datetime.now().astimezone().date().isoformat())
        details = dict(kind='market', language=language, stocks={}, signals=signals, regions=sorted(regions))
        return _capture(['market', series, query_id], series, payload.get('title') or payload.get('root_title')
                        or 'Market recap', day, markdown, details)[0]
    except Exception:
        logger.exception("Could not archive the market publication; original report is retained")
        return None


def _metadata(row):
    return dict(id=row.id, title=row.title, as_of=row.as_of,
                generated_at=row.created_at.replace(tzinfo=timezone.utc).isoformat())


def publication(run_id, include_body=True):
    with get_db().get_session() as db:
        row = db.get(ReportPublication, run_id)
        if row is None:
            return None
        data = {**_metadata(row), **json.loads(row.details_json)}
        if include_body:
            data['markdown'] = row.markdown
        return data


def latest_publications(limit):
    with get_db().get_session() as db:
        rows = db.query(ReportPublication.id, ReportPublication.title, ReportPublication.as_of,
                        ReportPublication.created_at).order_by(ReportPublication.created_at.desc(),
                                                               ReportPublication.id.desc()).limit(limit)
        return [_metadata(row) for row in rows]


def recent_history(limit):
    # History predates aggregate publications. Link individual and market records by their
    # original primary key; multiple runs on one date remain distinct.
    with get_db().get_session() as db:
        rows = db.query(AnalysisHistory.id, AnalysisHistory.name, AnalysisHistory.code,
                        AnalysisHistory.report_type, AnalysisHistory.created_at).filter(
            AnalysisHistory.created_at.isnot(None)
        ).order_by(AnalysisHistory.created_at.desc(), AnalysisHistory.id.desc()).limit(limit)
        return [
            {"id": f"history-{row.id}", "as_of": row.created_at.date().isoformat(),
             "generated_at": row.created_at.astimezone().isoformat(),
             "title": (row.name or "Market recap") if row.report_type == "market_review"
                      else " · ".join(dict.fromkeys(filter(None, [row.code, row.name])))} for row in rows
        ]


def chart_links(publication, base):
    if not publication.get('as_of') or not base:
        return {}
    symbols = list(publication['stocks'])
    if publication['kind'] == 'market' and 'us' in publication.get('regions', []):
        symbols.append('VOO')
    from data_provider.us_index_mapping import is_us_index_code, is_us_stock_code
    charts = {}
    for symbol in symbols:
        market = publication['stocks'].get(symbol, {}).get('market')
        is_us_equity = market == 'us' if market else is_us_stock_code(symbol)
        if not _SYMBOL.fullmatch(symbol) or not is_us_equity or is_us_index_code(symbol):
            continue
        cutoff = publication['stocks'].get(symbol, {}).get('chart_date') or publication['as_of']
        if symbol == 'VOO' and publication['kind'] == 'market' and publication['generated_at']:
            from zoneinfo import ZoneInfo
            generated = datetime.fromisoformat(publication['generated_at']).astimezone(ZoneInfo('America/New_York'))
            if cutoff >= generated.date().isoformat() and generated.hour < 16:
                cutoff = (generated.date() - timedelta(days=1)).isoformat()
        url = link(base, symbol=symbol, as_of=cutoff, report=publication['id'])
        if url:
            charts[symbol] = url
    return charts


def quote_evidence(rows, generated_at):
    """Age is measured at publication against provider time, never retrieval time."""
    try:
        now = datetime.fromisoformat((generated_at or '').replace('Z', '+00:00'))
    except ValueError:
        now = None
    evidence = []
    for symbol, row in rows.items():
        q = row.get('quote') or {}
        provider_time = q.get('provider_timestamp') or ''
        age = None
        try:
            observed = datetime.fromisoformat(provider_time.replace('Z', '+00:00'))
            if now is not None and now.utcoffset() is not None and observed.utcoffset() is not None and observed <= now:
                age = int((now - observed).total_seconds() // 60)
        except (ValueError, TypeError):
            pass
        evidence.append(dict(symbol=symbol, source=q.get('source'), status=q.get('status'),
                             provider_time=provider_time or None, age_minutes=age, technical=row.get('technical')))
    return evidence


def _historical(record_id):
    from src.services.history_service import HistoryService, MarkdownReportGenerationError
    history = HistoryService()
    data = history.get_history_detail_by_id(record_id)
    if not data:
        return None
    generated = data.get("created_at")
    code = data.get("stock_code") or data.get("storage_stock_code")
    is_market = data.get("report_type") == "market_review"
    snapshot = data.get("context_snapshot") or {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    overview = extract_analysis_context_pack_overview(snapshot) or {}
    blocks = {b["key"]: b for b in overview.get("blocks", [])}
    try:
        text = history.get_markdown_report(str(record_id))
    except MarkdownReportGenerationError:
        text = None
    # Early history can predate the raw structured payload needed by the renderer.
    text = text or data.get("analysis_summary") or data.get("news_content") or ""
    report_day = generated[:10] if isinstance(generated, str) and _DATE.fullmatch(generated[:10]) else None
    phase = data.get("market_phase_summary")
    stocks = {} if is_market or not code else {code: {
        "rating": data.get("action_label") or data.get("operation_advice"), "score": data.get("sentiment_score"),
        "chart_date": chart_cutoff(report_day, phase) if report_day else None,
        "market": phase.get("market") if isinstance(phase, dict) else None,
        "quote": blocks.get("quote") or {}, "technical": (blocks.get("technical") or {}).get("status"),
    }}
    payload = snapshot.get("market_review_payload") or {}
    day = payload.get("date") if is_market and isinstance(payload, dict) else None
    region = snapshot.get("market_review_region")
    return {"id": f"history-{record_id}", "kind": "market" if is_market else "single", "stocks": stocks,
            "title": data.get("stock_name") or code or f"Report {record_id}", "generated_at": generated, "as_of": day or report_day,
            "markdown": text, "regions": region.split(",") if isinstance(region, str) else [],
            "previous_id": None, "changes": [], "signals": {}, "historical": True}


def read_report(run_id, charts_url):
    if re.fullmatch(r"history-[1-9][0-9]{0,16}", run_id):
        data = _historical(int(run_id.split("-")[1]))
    elif re.fullmatch(r"[0-9a-f]{32}", run_id):
        data = publication(run_id)
    else:
        data = None

    if data is None:
        return None
    return {**data, "charts": chart_links(data, charts_url),
            "evidence": quote_evidence(data["stocks"], data["generated_at"])}
