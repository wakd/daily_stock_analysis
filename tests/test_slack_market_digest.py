import unittest

from src.notification_sender.slack_market_digest import build_market_digest


class MarketDigestTest(unittest.TestCase):
    def test_archived_english_recap_localizes_indices_and_keeps_volatility_separate(self):
        fallback, blocks = build_market_digest("""🎯 Market Review

## 2026-11-27 US Market Recap
### 1. Market Summary
The Dow rose as technology shares slipped. Volatility also eased.
- **Market Signal**: 42/100 (mixed, balanced)
- **Drivers**: average major-index change -1.94%
### 2. Index Commentary
Markets were mixed. Due to data limitations, volume confirmation is unavailable.
| Index | Last | Change % | Open |
|-------|------|----------|------|
| 标普500指数 | 7182.78 | :red_circle: -0.25% | 7060.33 |
| 纳斯达克综合指数 | 23585.80 | 🔴 −0.62% | 22948.48 |
| 道琼斯工业指数 | 50182.95 | :large_green_circle: +0.33% | 49469.86 |
| VIX恐慌指数 | 24.87 | 🔴 -7.20% | 24.89 |
### 4. Outlook
The outlook remains mixed.
### 5. Risk Alerts
- **Index divergence:** Breadth data are unavailable.
""")
        self.assertEqual(blocks[0]["text"]["text"], "US Market Recap · 2026-11-27")
        for phrase in ("42/100 (mixed, balanced)", "S&P 500 · 7,182.78 · -0.25%",
                       "Nasdaq Composite · 23,585.80 · -0.62%", "50,182.95 · +0.33%", "24.87 · -7.20%",
                       "The outlook remains mixed.", "Breadth data are unavailable.",
                       "volume confirmation is unavailable."):
            self.assertIn(phrase, fallback)
        for excluded in ("标普", "-1.94%", ":red_circle:", "##", "**", "|"):
            self.assertNotIn(excluded, fallback)
        self.assertTrue(all(b["text"]["type"] == "plain_text" for b in blocks))

    def test_preserves_regional_headings_in_one_notification(self):
        parts = ["# 🎯 大盘复盘"]
        for i, name in enumerate(("A股", "美股", "港股", "日股", "韩股")):
            parts.append(f"""# {name}大盘复盘
## 2026-11-2{i} 大盘复盘
### 一、盘面总览
- **盘面信号**：{45+i}/100（震荡，均衡）
### 二、指数结构
| 指数 | 最新 | 涨跌幅 |
| ---- | ---- | ------ |
| 示例 | 100.00 | 🔴 +0.20% |
### 七、风险提示
留意成交量变化。""")
        fallback, blocks = build_market_digest("\n\n".join(parts))
        headers = [b["text"]["text"] for b in blocks if b["type"] == "header"]
        self.assertEqual(len(headers), 5)
        self.assertEqual(headers[1], "美股大盘复盘 · 2026-11-21")
        self.assertIn("49/100", fallback)
        self.assertLessEqual(len(blocks), 22)
        self.assertLessEqual(len(fallback), 1900)

    def test_metadata_and_missing_quote_columns_do_not_invent_a_close_or_date(self):
        fallback, _ = build_market_digest("""[dsa-market-region]: # (cn)
# 大盘复盘
## 指数表现
| 指数 | 涨跌幅 | 量能 |
| --- | --- | --- |
| 上证指数 | +0.8% | 放量 |
| 创业板指 | -0.2% | 缩量 |
## 明日观察
关注成交量。""")
        self.assertIn("A股大盘复盘", fallback)
        self.assertIn("创业板指 · -0.2%", fallback)
        self.assertNotIn("放量", fallback)
        self.assertNotRegex(fallback, r"20\d\d-\d\d-\d\d")

    def test_fallback_template_bullets_and_missing_benchmark(self):
        report = """## 2026-11-27 HK Market Recap
### 1. Market Summary
The benchmark move is unavailable for this session.
### 2. Major Indices
- No index data available
"""
        fallback, _ = build_market_digest(report)
        self.assertIn("Index quotes unavailable", fallback)
        self.assertNotIn("/100", fallback)
        with_index, _ = build_market_digest(
            report.replace("- No index data available", "- **HSI**: 26401.20 (🟢 +0.33%)"))
        self.assertIn("HSI · 26,401.20 · +0.33%", with_index)

    def test_does_not_consume_ordinary_notifications(self):
        for text in ("", "Check the market review tomorrow.", "Market Review", "# Decision Dashboard\n## Market Recap"):
            self.assertIsNone(build_market_digest(text))

    def test_unrecognized_region_preserves_combined_content_for_generic_delivery(self):
        text = """🎯 Market Review
# US Market Recap
### Market Summary
The US session was mixed.
# Japan Market Recap (速報)
## History
Unstructured report body."""
        self.assertIsNone(build_market_digest(text))

    def test_regional_korean_report_keeps_its_language(self):
        fallback, _ = build_market_digest("""# 🎯 시황 리뷰
## 2026-11-27 미국 시황 리뷰
### 시장 요약
- **시장 신호**: 55/100 (혼조, 균형)
### 주요 지수
| 지수 | 종가 | 등락률 |
| --- | --- | --- |
| S&P 500 | 7182.78 | -0.25% |
""")
        self.assertIn("미국 시황 리뷰", fallback)
        self.assertIn("시장 신호  55/100", fallback)
        self.assertIn("S&P 500 · 7,182.78 · -0.25%", fallback)
