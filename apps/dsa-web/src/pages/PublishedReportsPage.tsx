import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import apiClient from '../api';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { ReportMarkdownBody } from '../components/report/ReportMarkdownBody';

interface Run {
  id: string;
  title: string;
  as_of: string | null;
  generated_at: string | null;
}
interface Report extends Run {
  kind: string;
  markdown: string;
  previous_id?: string;
  changes: string[];
  signals: Record<string, number>;
  charts: Record<string, string>;
  stocks: Record<string, {rating: string; score: number; chart_date?: string}>;
  evidence: {
    symbol: string;
    source: string | null;
    status: string | null;
    technical: string | null;
    provider_time: string | null;
    age_minutes: number | null;
  }[];
}

export default function PublishedReportsPage({ id }: { id: string | null }) {
  const { language } = useUiLanguage();
  const label = (en: string, zh: string) => language === 'en' ? en : zh;
  const [result, setResult] = useState<{ report?: Report; items?: Run[]; history?: Run[]; error?: boolean }>({});
  useEffect(() => {
    const controller = new AbortController();
    const options = { signal: controller.signal };
    const request = id === null
      ? apiClient.get<{ items: Run[]; history?: Run[] }>('/api/v1/publications', options).then(r => ({ items: r.data.items, history: r.data.history }))
      : apiClient.get<Report>(`/api/v1/publications/${encodeURIComponent(id)}`, options).then(r => ({ report: r.data }));
    void request.then(data => { if (!controller.signal.aborted) setResult(data); })
      .catch(() => { if (!controller.signal.aborted) setResult({ error: true }); });
    return () => controller.abort();
  }, [id]);
  const time = (iso: string | null) => iso ? new Date(iso).toLocaleString(language === 'en' ? 'en-GB' : 'zh-CN', { timeZoneName: 'short' }) : '—';
  const report = result.report;
  const destination = (key: string) => `/?report=${encodeURIComponent(key)}`;
  const runLinks = (items: Run[]) => items.map(r => (
    <Link key={r.id} className="block border-b border-border py-3 hover:text-primary" to={destination(r.id)}>
      <strong>{r.title}</strong><br /><time className="text-sm text-secondary-text" dateTime={r.generated_at ?? undefined}>{time(r.generated_at)}</time>
    </Link>
  ));
  return (
    <main className="mx-auto max-w-5xl space-y-5 px-4 py-6 text-foreground">
      <nav className="flex flex-wrap gap-5 text-sm text-primary">
        <Link to="/">{label('Analysis dashboard', '分析面板')}</Link>
        {id !== null && <Link to="/?reports=1">{label('Published reports', '已发布报告')}</Link>}
      </nav>
      {result.error ? <p role="alert">{label('This report could not be opened.', '无法打开此报告。')}</p> :
        result.items ? <section>
          <h1 className="mb-4 text-2xl font-semibold">{label('Published reports', '已发布报告')}</h1>
          {result.items.length === 0 && <p>{label('New publication snapshots will appear here.', '新发布的报告将显示在此处。')}</p>}
          {runLinks(result.items)}
          {!!result.history?.length && <>
            <h2 className="mt-7 text-lg font-semibold">{label('Individual and market report history', '个股及市场报告历史')}</h2>
            {runLinks(result.history)}
          </>}
        </section> : report ? <>
          <header>
            <h1 className="text-2xl font-semibold">{report.title}</h1>
            <p className="mt-1 text-sm text-secondary-text">{label('Published', '发布时间')} <time dateTime={report.generated_at ?? undefined}>{time(report.generated_at)}</time>
              {' · '}{label('Report date', '报告日期')} {report.as_of ?? '—'}</p>
          </header>
          {report.previous_id && <section>
            <h2 className="font-semibold">{label('Since the previous report with matching coverage', '与覆盖相同标的的上次报告比较')}</h2>
            <p>{report.changes.length ? report.changes.join(' · ') :
              (Object.keys(report.stocks).length || Object.keys(report.signals).length)
                ? label('Ratings unchanged', '评级未变') : label('Comparable scores unavailable', '没有可比较的评分')}</p>
            <Link to={destination(report.previous_id)} className="text-sm text-primary">{label('Read previous report', '阅读上次报告')}</Link>
          </section>}
          {report.evidence.length > 0 && <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <caption className="mb-2 text-left font-semibold">{label('Ratings and quote evidence at publication', '发布时的评级和行情数据')}</caption>
              <thead className="text-secondary-text"><tr>{[
                label('Ticker', '代码'), label('Rating', '评级'), label('Quote status', '行情状态'), label('Provider', '数据源'),
                label('Provider time / age', '行情时间及数据时长'), label('Technicals', '技术数据'),
              ].map(v => <th className="px-2 py-2" key={v}>{v}</th>)}</tr></thead>
              <tbody>{report.evidence.map(e => <tr key={e.symbol} className="border-t border-border">
                <td className="px-2 py-2">{report.charts[e.symbol] ? <a className="text-primary underline" title={report.stocks[e.symbol]?.chart_date} href={report.charts[e.symbol]}>{e.symbol}</a> : e.symbol}</td>
                <td className="px-2">{report.stocks[e.symbol]?.rating} {report.stocks[e.symbol]?.score}</td>
                <td className="px-2">{e.status || '—'}</td><td className="px-2">{e.source || '—'}</td>
                <td className="px-2">{e.provider_time || '—'} {e.age_minutes !== null && `(${e.age_minutes} min)`}</td>
                <td className="px-2">{e.technical || '—'}</td>
              </tr>)}</tbody>
            </table>
          </div>}
          {report.kind === 'market' && report.charts.VOO && <a className="block text-primary underline" href={report.charts.VOO}>{label('Open US benchmark proxy chart (VOO)', '打开美股基准代理图表 (VOO)')}</a>}
          <div className="border-t border-border pt-5"><ReportMarkdownBody content={report.markdown} /></div>
        </> : <p role="status">{label('Loading reports…', '正在加载报告…')}</p>}
    </main>
  );
}
