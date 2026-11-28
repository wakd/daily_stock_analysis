import type { SkillInfo } from '../api/agent';
import type { UiLanguage } from '../i18n/uiText';

const BUILTIN_SKILLS_EN: Record<string, Pick<SkillInfo, 'name' | 'description'>> = {
  bottom_volume: { name: "Volume at a market bottom", description: "Looks for a surge in volume after an extended decline as a possible sign of reversal." },
  box_oscillation: { name: "Range trading", description: "Identifies support and resistance in a sideways market." },
  dragon_head: { name: "Sector leaders", description: "Identifies leading stocks as sectors rotate or respond to a catalyst." },
  event_driven: { name: "Event-driven analysis", description: "Assesses the impact and risks of earnings, policy changes, orders, mergers, and product launches." },
  chan_theory: { name: "Chan theory", description: "Uses price swings, segments, and consolidation zones to assess trends and divergence." },
  expectation_repricing: { name: "Expectations and repricing", description: "Tracks shifts in earnings, policy, and valuation expectations." },
  growth_quality: { name: "Growth quality", description: "Reviews growth, return on equity, cash flow, and industry prospects." },
  emotion_cycle: { name: "Sentiment cycle", description: "Combines sentiment, turnover, and price-volume data to identify extremes." },
  bull_trend: { name: "Bullish trend", description: "Looks for bullish alignment, trend continuation, and pullbacks." },
  ma_golden_cross: { name: "Moving-average golden cross", description: "Checks for a bullish moving-average crossover confirmed by volume." },
  one_yang_three_yin: { name: "One bullish and three bearish candles", description: "Looks for a four-candle consolidation pattern associated with trend continuation." },
  hot_theme: { name: "Market themes", description: "Tracks policy and industry themes, sector breadth, and relative strength." },
  shrink_pullback: { name: "Low-volume pullback", description: "Checks for pullbacks to moving-average support on declining volume." },
  volume_breakout: { name: "Volume breakout", description: "Looks for price breaking above resistance on rising volume." },
  wave_theory: { name: "Elliott wave theory", description: "Uses impulse and corrective waves to assess price structure and potential targets." },
};

export function getLocalizedStrategySkill(skill: SkillInfo, language: UiLanguage): SkillInfo {
  const translation = language === 'en' ? BUILTIN_SKILLS_EN[skill.id] : undefined;
  return translation ? { ...skill, ...translation } : skill;
}
