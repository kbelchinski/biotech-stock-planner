import { useMemo, useState } from "react";
import { price, shortDate } from "../format";
import type { PriceBar } from "../types";

type Range = "5d" | "1m" | "3m" | "6m" | "ytd" | "1y";

const RANGES: { id: Range; label: string; sessions?: number }[] = [
  { id: "5d", label: "5D", sessions: 5 },
  { id: "1m", label: "1M", sessions: 21 },
  { id: "3m", label: "3M", sessions: 63 },
  { id: "6m", label: "6M", sessions: 126 },
  { id: "ytd", label: "YTD" },
  { id: "1y", label: "1Y", sessions: 252 },
];

function barsForRange(bars: PriceBar[], range: Range): PriceBar[] {
  if (range === "ytd") {
    const year = bars[bars.length - 1]?.session_date.slice(0, 4);
    return year ? bars.filter((bar) => bar.session_date >= `${year}-01-01`) : bars;
  }
  const count = RANGES.find((item) => item.id === range)?.sessions;
  return count ? bars.slice(-count) : bars;
}

interface Props {
  ticker: string;
  bars: PriceBar[];
}

export function PriceChart({ ticker, bars }: Props) {
  const sorted = useMemo(
    () => [...bars].sort((a, b) => a.session_date.localeCompare(b.session_date)),
    [bars],
  );
  const [range, setRange] = useState<Range>("1m");
  const [hover, setHover] = useState<number | null>(null);

  const series = useMemo(() => barsForRange(sorted, range), [sorted, range]);

  if (sorted.length === 0) {
    return (
      <section className="price-chart" aria-label={`${ticker} price chart`}>
        <h3 className="section-title">Stock price</h3>
        <p className="muted">No Alpaca daily bars for this symbol.</p>
      </section>
    );
  }

  const last = series[series.length - 1];
  const prior = series.length > 1 ? series[series.length - 2] : null;
  const change = prior ? last.close - prior.close : 0;
  const changePct = prior && prior.close ? (change / prior.close) * 100 : 0;
  const up = change > 0;
  const down = change < 0;
  const tone = up ? "up" : down ? "down" : "flat";
  const active = hover != null ? series[hover] : last;

  const width = 600;
  const height = 220;
  const pad = { top: 16, right: 12, bottom: 28, left: 52 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const closes = series.map((bar) => bar.close);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const span = max - min || Math.max(Math.abs(max) * 0.02, 0.01);
  const lo = min - span * 0.08;
  const hi = max + span * 0.08;
  const x = (i: number) => pad.left + (series.length === 1 ? innerW / 2 : (i / (series.length - 1)) * innerW);
  const y = (value: number) => pad.top + ((hi - value) / (hi - lo)) * innerH;
  const line = series.map((bar, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(bar.close).toFixed(1)}`).join(" ");
  const area = `${line} L ${x(series.length - 1).toFixed(1)} ${pad.top + innerH} L ${x(0).toFixed(1)} ${pad.top + innerH} Z`;
  const prevClose = prior?.close;
  const ticks = [hi, (hi + lo) / 2, lo];

  return (
    <section className="price-chart" aria-label={`${ticker} daily price chart`}>
      <div className="price-chart-head">
        <div>
          <p className="price-chart-last tabular">
            {price(active.close)} <span className="price-chart-ccy">USD</span>
          </p>
          {prior && (
            <p className={`price-chart-change ${tone}`}>
              {up ? "↑" : down ? "↓" : "→"} {change >= 0 ? "+" : ""}
              {change.toFixed(4)} ({changePct >= 0 ? "+" : ""}
              {changePct.toFixed(2)}%)
              {hover == null ? " vs prior session" : ""}
            </p>
          )}
          <p className="price-chart-meta">
            Daily SIP close · {shortDate(active.session_date)}
            {hover != null && ` · O ${price(active.open)}  H ${price(active.high)}  L ${price(active.low)}`}
          </p>
        </div>
        <div className="price-chart-ranges" role="group" aria-label="Chart range">
          {RANGES.map((item) => (
            <button
              key={item.id}
              type="button"
              className={range === item.id ? "is-active" : ""}
              aria-pressed={range === item.id}
              onClick={() => {
                setRange(item.id);
                setHover(null);
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <svg
        className={`price-chart-svg is-${tone}`}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${ticker} close from ${shortDate(series[0].session_date)} to ${shortDate(last.session_date)}`}
        onPointerLeave={() => setHover(null)}
        onPointerMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const svgX = ((event.clientX - rect.left) / rect.width) * width;
          let nearest = 0;
          let best = Infinity;
          series.forEach((_, i) => {
            const dx = Math.abs(x(i) - svgX);
            if (dx < best) {
              best = dx;
              nearest = i;
            }
          });
          setHover(nearest);
        }}
      >
        {ticks.map((tick) => (
          <g key={tick}>
            <line className="price-chart-grid" x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} />
            <text className="price-chart-axis" x={pad.left - 8} y={y(tick) + 4} textAnchor="end">
              {tick.toFixed(tick >= 10 ? 2 : 3)}
            </text>
          </g>
        ))}
        {prevClose != null && range === "5d" && (
          <line className="price-chart-prev" x1={pad.left} x2={width - pad.right} y1={y(prevClose)} y2={y(prevClose)} />
        )}
        <path className="price-chart-area" d={area} />
        <path className="price-chart-line" d={line} />
        {hover != null && (
          <>
            <line className="price-chart-cross" x1={x(hover)} x2={x(hover)} y1={pad.top} y2={pad.top + innerH} />
            <circle className="price-chart-dot" cx={x(hover)} cy={y(series[hover].close)} r="4" />
          </>
        )}
        <text className="price-chart-axis" x={pad.left} y={height - 8}>
          {shortDate(series[0].session_date)}
        </text>
        <text className="price-chart-axis" x={width - pad.right} y={height - 8} textAnchor="end">
          {shortDate(last.session_date)}
        </text>
      </svg>
      <p className="price-chart-note">
        Alpaca daily SIP closes, up to 1 year. Not a live 1D tape — we do not fetch minute bars.
      </p>
    </section>
  );
}
