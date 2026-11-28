import { formatUiText, type UiLanguage } from '../i18n/uiText';
import { PORTFOLIO_ENTRY_TEXT } from '../locales/portfolioEntryText';
import type {
  PortfolioCashDirection,
  PortfolioCorporateActionType,
  PortfolioFxRefreshResponse,
  PortfolioImportCommitResponse,
  PortfolioImportParseResponse,
  PortfolioPositionItem,
  PortfolioSide,
} from '../types/portfolio';
import { toDateInputValue } from './format';

export type FxRefreshFeedback = {
  tone: 'neutral' | 'success' | 'warning';
  text: string;
};

export type PortfolioAlertVariant = 'info' | 'success' | 'warning' | 'danger';

export function getTodayIso(): string {
  return toDateInputValue(new Date());
}

export function formatMoney(value: number | undefined | null, currency = 'CNY'): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${currency} ${Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function formatPct(value: number | undefined | null): string {
  if (value == null || Number.isNaN(value)) return '--';
  return `${value.toFixed(2)}%`;
}

export function formatSignedPct(value: number | undefined | null): string {
  if (value == null || Number.isNaN(value)) return '--';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

export function hasPositionPrice(row: PortfolioPositionItem): boolean {
  return row.priceAvailable !== false && row.priceSource !== 'missing';
}

export function formatPositionPrice(row: PortfolioPositionItem): string {
  if (!hasPositionPrice(row)) return '--';
  return row.lastPrice.toFixed(4);
}

export function formatPositionMoney(value: number, row: PortfolioPositionItem): string {
  if (!hasPositionPrice(row)) return '--';
  return formatMoney(value, row.valuationCurrency);
}

export function getPositionPriceLabel(row: PortfolioPositionItem, language: UiLanguage = 'zh'): string {
  const copy = PORTFOLIO_ENTRY_TEXT[language];
  if (!hasPositionPrice(row)) return copy.missingPrice;
  if (row.priceSource === 'realtime_quote') {
    return row.priceProvider ? `${copy.livePrice} · ${row.priceProvider}` : copy.livePrice;
  }
  if (row.priceSource === 'history_close') {
    return row.priceStale && row.priceDate ? `${copy.closePrice} · ${row.priceDate}` : copy.closePrice;
  }
  return row.priceSource || copy.unknownSource;
}

export function formatSideLabel(value: PortfolioSide, language: UiLanguage = 'zh'): string {
  const copy = PORTFOLIO_ENTRY_TEXT[language];
  return value === 'buy' ? copy.buy : copy.sell;
}

export function formatCashDirectionLabel(value: PortfolioCashDirection, language: UiLanguage = 'zh'): string {
  const copy = PORTFOLIO_ENTRY_TEXT[language];
  return value === 'in' ? copy.inflow : copy.outflow;
}

export function formatCorporateActionLabel(value: PortfolioCorporateActionType, language: UiLanguage = 'zh'): string {
  const copy = PORTFOLIO_ENTRY_TEXT[language];
  return value === 'cash_dividend' ? copy.cashDividend : copy.split;
}

const BROKER_NAMES_EN: Record<string, string> = { huatai: 'Huatai', citic: 'CITIC', cmb: 'China Merchants' };

export function formatBrokerLabel(value: string, displayName?: string, language: UiLanguage = 'zh'): string {
  if (language === 'en') return BROKER_NAMES_EN[value] ?? (displayName?.trim() || value);
  if (displayName && displayName.trim()) return `${value}（${displayName.trim()}）`;
  if (value === 'huatai') return 'huatai（华泰）';
  if (value === 'citic') return 'citic（中信）';
  if (value === 'cmb') return 'cmb（招商）';
  return value;
}

export function buildFxRefreshFeedback(data: PortfolioFxRefreshResponse, language: UiLanguage = 'zh'): FxRefreshFeedback {
  const copy = PORTFOLIO_ENTRY_TEXT[language];
  if (data.refreshEnabled === false) {
    return {
      tone: 'neutral',
      text: copy.fxDisabled,
    };
  }

  if (data.pairCount === 0) {
    return {
      tone: 'neutral',
      text: copy.noPairs,
    };
  }

  if (data.updatedCount > 0 && data.staleCount === 0 && data.errorCount === 0) {
    return {
      tone: 'success',
      text: formatUiText(copy.updatedPairs, { count: data.updatedCount }),
    };
  }

  const summary = formatUiText(copy.fxCounts, { updated: data.updatedCount, stale: data.staleCount, failed: data.errorCount });
  if (data.staleCount > 0) {
    return {
      tone: 'warning',
      text: formatUiText(copy.stalePairs, { summary }),
    };
  }

  return {
    tone: 'warning',
    text: formatUiText(copy.fxPartial, { summary }),
  };
}

export function getFxRefreshFeedbackVariant(tone: FxRefreshFeedback['tone']): PortfolioAlertVariant {
  if (tone === 'success') return 'success';
  if (tone === 'warning') return 'warning';
  return 'info';
}

export function getCsvParseVariant(result: PortfolioImportParseResponse): PortfolioAlertVariant {
  return result.errorCount > 0 || result.skippedCount > 0 ? 'warning' : 'info';
}

export function getCsvCommitVariant(result: PortfolioImportCommitResponse, isDryRun: boolean): PortfolioAlertVariant {
  if (isDryRun) return 'info';
  return result.failedCount > 0 || result.duplicateCount > 0 ? 'warning' : 'success';
}
