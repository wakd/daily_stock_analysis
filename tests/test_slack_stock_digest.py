import unittest

from src.notification_sender.slack_stock_digest import build_stock_digest


class SlackStockDigestTests(unittest.TestCase):
    def test_exact_stock_labels_take_precedence_over_coarse_dashboard_counts(self):
        content = """# 🎯 2026-11-25 Decision Dashboard
> Analyzed **3** stocks | 🟢Buy:0 🟡Watch:1 🔴Sell:2
Market status: US · Pre-market
## 📊 Summary
⚪ **Vanguard S&P 500 ETF(VOO)**: Watch | Score 44 | Bearish
🟠 **Micron Technology, Inc.(MU)**: Reduce | Score 31 | Strong Bearish
🔴 **Advanced Micro Devices, Inc.(AMD)**: Sell | Score 15 | Strong Bearish
---
## 🔴 Advanced Micro Devices, Inc. (AMD)
**🚨 Risk Alerts**:
- Risk 1: Wide trading range.
### 🛡️ Phase Decision Guardrail
| Action Window | Current Action | Next Check |
|---------|---------|---------|
| Pre-market plan | Wait for intraday confirmation. | Regular session open |
**Data Limitations**:
- quote: fallback
- technical: partial
"""
        fallback, blocks = build_stock_digest(content.replace("\n", "\r\n"))
        self.assertIn("US · Pre-market · 3 stocks", fallback)
        self.assertIn("Reduce (1)   MU 31", fallback)
        self.assertIn("Sell (1)   AMD 15", fallback)
        self.assertIn("Watch (1)   VOO 44", fallback)
        self.assertLess(fallback.index("AMD 15"), fallback.index("VOO 44"))
        self.assertIn("AMD — Wait for intraday confirmation.", fallback)
        self.assertIn("Risk Alerts · AMD\nWide trading range.", fallback)
        self.assertIn("Next Check · Regular session open", fallback)
        self.assertIn("Fallback quotes (1/3)", fallback)
        self.assertIn("Partial technical data (1/3)", fallback)
        self.assertNotIn("|", fallback)
        self.assertEqual(blocks[0]["type"], "header")

    def test_brief_preserves_locale_and_source_conclusion(self):
        for title, score_label, signal, unit, conclusion in [
            ("决策简报", "评分", "减仓", "只", "等待开盘确认。"),
            ("결정 브리핑", "점수", "비중축소", "개", "시장 확인을 기다립니다."),
        ]:
            with self.subTest(title=title):
                content = (f"# 🎯 2026-11-25 {title}\n> 1 {unit} | 🟢0 🟡0 🔴1\n"
                           f"**Example(BRK.B)** 🟠 {signal} | {score_label} 30 | {conclusion}")
                fallback, _ = build_stock_digest(content)
                self.assertIn(f"{signal} (1)   BRK.B 30", fallback)
                self.assertIn(conclusion, fallback)
                self.assertNotIn("Signals", fallback)

    def test_single_stock_keeps_session_risks_and_original_date(self):
        content = """## 🔴 Example (EX.A)
> 2026-11-25 10:25 | Score: **15** | Bearish
- phase: premarket | market: us
- limitation: News feed delayed
### 📌 Core Conclusion
**Sell**: Wait until market open for confirmation.
### 📰 Key Updates
🚨 **Risk Alerts**:
- Elevated volatility
"""
        fallback, _ = build_stock_digest(content)
        self.assertIn("EX.A · Example", fallback)
        self.assertIn("2026-11-25 10:25", fallback)
        self.assertIn("Sell · Score 15 · Bearish", fallback)
        self.assertIn("Wait until market open for confirmation.", fallback)
        self.assertIn("Elevated volatility", fallback)
        self.assertIn("News feed delayed", fallback)

    def test_large_universe_retains_count_and_discloses_omissions(self):
        report = "# 🎯 2026-11-25 Decision Dashboard\n> Analyzed **80** stocks\n"
        report += "\n".join(f"⚪ **Example(EX{i})**: Watch | Score 45 | Bearish" for i in range(80))
        fallback, blocks = build_stock_digest(report)
        self.assertIn("Watch (80)", fallback)
        self.assertIn("… +72", fallback)
        self.assertLessEqual(len(blocks), 22)
        self.assertLessEqual(len(fallback), 1900)

    def test_unrecognized_incomplete_and_empty_reports_defer_to_generic_rendering(self):
        self.assertIsNone(build_stock_digest("Arbitrary alert"))
        self.assertIsNone(build_stock_digest("# 🎯 2026-11-25 Decision Dashboard\nNo analysis results"))
        self.assertIsNone(build_stock_digest("# 2026-11-25 Decision Dashboard\n> Analyzed **2** stocks\n"
                                             "⚪ **Example(EX)**: Watch | Score 45 | Bearish"))
