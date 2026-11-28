import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.notification_sender.slack_sender import build_slack_payloads
from src.services import report_publications as runs
from src.storage import ReportPublication


def stock(query, score):
    return SimpleNamespace(code="NVDA", query_id=query, action="reduce", action_label="Reduce",
                           operation_advice="Reduce", sentiment_score=score, report_language="en",
                           market_phase_summary={"phase": "premarket", "session_date": "2026-11-27",
                                                 "effective_daily_bar_date": "2026-11-25"},
                           analysis_context_pack_overview={"blocks": [
                               {"key": "quote", "status": "stale", "source": "test feed",
                                "provider_timestamp": "2026-11-25T21:00:00Z"},
                               {"key": "technical", "status": "partial"}]})


def dashboard(score):
    return f"""# 🎯 2026-11-27 Decision Dashboard
> Analyzed **1** stocks | Reduce:1
Market status: US · Pre-market
## 📊 Summary
🟠 **NVIDIA(NVDA)**: Reduce | Score {score} | Bearish
"""


class PublicationTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        ReportPublication.__table__.create(engine)
        self.session = sessionmaker(bind=engine)
        self.addCleanup(engine.dispose)
        db = SimpleNamespace(get_session=self.session)
        self.patcher = patch.object(runs, "get_db", return_value=db)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_end_to_end_snapshots_links_and_quote_time(self):
        first = runs.publish_stocks(dashboard(31), [stock("one", 31)], "en", "simple")
        old_id = runs.RUN_MARKER.search(first)[1]
        second = runs.publish_stocks(dashboard(35), [stock("two", 35)], "en", "simple")
        new_id = runs.RUN_MARKER.search(second)[1]
        repeated = runs.publish_stocks(dashboard(39), [stock("two", 39)], "en", "simple")
        self.assertEqual(repeated, second)
        self.assertEqual(len(runs.latest_publications(30)), 2)
        self.assertNotEqual(old_id, new_id)
        record = runs.read_report(new_id, "https://example.org/terminal/")
        earlier = runs.read_report(old_id, "https://example.org/terminal/")
        self.assertEqual(record["previous_id"], old_id)
        self.assertIn("NVDA: Reduce 31 → Reduce 35", record["changes"])
        self.assertIn("Score 35", record["markdown"])
        self.assertIn("Score 31", earlier["markdown"])
        self.assertNotIn("dsa-report-run", record["markdown"])
        args = parse_qs(urlsplit(record["charts"]["NVDA"]).query)
        self.assertEqual(args, {"symbol": ["NVDA"], "report": [new_id], "as_of": ["2026-11-25"]})
        payload = build_slack_payloads(second, reports_url="https://example.org/",
                                       terminal_url="https://example.org/terminal/", publications={new_id: record})[0]
        self.assertIn("Open this report: https://example.org/?report=" + new_id, payload["text"])
        self.assertIn("test feed", payload["text"])
        self.assertIn("31 → Reduce 35", payload["text"])
        self.assertIn("as_of=2026-11-25", json.dumps(payload["blocks"]))
        self.assertNotIn("dsa-report-run", json.dumps(payload))
        self.assertEqual(earlier["previous_id"], None)

    def test_identical_market_bodies_distinct_runs_and_same_day_score_change(self):
        ids = []
        for query, score in (("morning", 62), ("late", 48), ("recheck", 48)):
            payload = {"region":"us", "language":"en", "date":"2026-11-25", "market_light":{"score":score},
                       "title":"US market recap"}
            content = f"# US Market Recap\n\nMarket Signal: {score}/100"
            ids.append(runs.publish_market(content, query, payload))
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(runs.publication(ids[1])["changes"], ["US: 62 → 48/100"])
        self.assertEqual(runs.publication(ids[2])["changes"], [])
        self.assertEqual(runs.publication(ids[2])["previous_id"], ids[1])

    def test_conservative_cutoff_and_provider_age(self):
        self.assertEqual(runs.chart_cutoff("2026-11-27", {"phase":"intraday",
                         "effective_daily_bar_date":"2026-11-27"}), "2026-11-26")
        self.assertEqual(runs.chart_cutoff("2026-11-27", {"phase":"postmarket",
                         "effective_daily_bar_date":"2026-11-27"}), "2026-11-27")
        rows={"NVDA":{"quote":{"status":"fallback","source":"alt","fetched_at":"2026-11-27T14:00:00Z"}},
              "VOO":{"quote":{"status":"ok","provider_timestamp":"2026-11-27T13:40:00Z"}}}
        data=runs.quote_evidence(rows, "2026-11-27T14:00:00Z")
        self.assertIsNone(data[0]["age_minutes"])
        self.assertEqual(data[1]["age_minutes"],20)

    def test_legacy_history_reference(self):
        history=SimpleNamespace(get_history_detail_by_id=lambda _: {"stock_code":"NVDA","stock_name":"NVIDIA",
            "created_at":"2026-11-27T12:00:00+00:00","report_type":"simple","sentiment_score":31,
            "action_label":"Reduce","market_phase_summary":{"phase":"premarket"}},
            get_markdown_report=lambda _: "Historical report")
        with patch("src.services.history_service.HistoryService", return_value=history):
            result = runs.read_report("history-123", "https://example.org/terminal/")
        self.assertEqual(result["markdown"],"Historical report")
        self.assertIn("report=history-123", result["charts"]["NVDA"])
        self.assertIsNone(result["evidence"][0]["age_minutes"])


    def test_historical_record_without_raw_data_or_timestamps_keeps_available_evidence(self):
        from src.services.history_service import MarkdownReportGenerationError
        history = SimpleNamespace(
            get_history_detail_by_id=lambda _: {"stock_code": "NVDA", "analysis_summary": "Archived thesis"},
            get_markdown_report=lambda _: None,
        )
        with patch("src.services.history_service.HistoryService", return_value=history), patch.object(
            history, "get_markdown_report", side_effect=MarkdownReportGenerationError("No raw report")
        ):
            result = runs.read_report("history-17", "https://example.org/terminal/")
        self.assertEqual(result["markdown"], "Archived thesis")
        self.assertIsNone(result["generated_at"])
        self.assertIsNone(result["evidence"][0]["age_minutes"])
        self.assertEqual(result["charts"], {})

    def test_terminal_links_only_for_supported_us_equities(self):
        rec = {"id": "a" * 32, "kind": "stocks", "as_of": "2026-11-25",
               "stocks": {"NVDA": {}, "BRK.B": {}, "^GSPC": {}, "SPX": {},
                          "600519": {}, "HK00700": {}, "FOO.L": {"market": "uk"}}}
        self.assertEqual(set(runs.chart_links(rec, "https://example.org/terminal/")), {"NVDA", "BRK.B"})
