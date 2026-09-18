import { useId, useMemo, useState } from "react";
import { price } from "../../format";
import { KindBadge } from "./ui";

const DEFAULT_DAYS = 50;
const MIN_DAYS = 1;

interface Bar {
  session_date: string;
  close: number;
}

export function AveragePrice({ bars }: { bars: Bar[] }) {
  const id = useId();
  const sorted = useMemo(
    () => [...bars].sort((a, b) => a.session_date.localeCompare(b.session_date)),
    [bars],
  );
  const [days, setDays] = useState(DEFAULT_DAYS);

  if (sorted.length === 0) return null;

  const lookback = Math.min(Math.max(days, MIN_DAYS), sorted.length);
  const window = sorted.slice(-lookback);
  const mean = window.reduce((sum, bar) => sum + bar.close, 0) / window.length;

  return (
    <div className="avg-price">
      <div className="avg-price-head">
        <p className="avg-price-label">
          Average price, last {lookback} {lookback === 1 ? "day" : "days"} <KindBadge kind="calculation" />
        </p>
        <p className="avg-price-value tabular">{price(mean)}</p>
      </div>
      {sorted.length > 1 && (
        <label className="avg-price-control" htmlFor={id}>
          <span className="sr-only">Days in the average price</span>
          <span className="avg-price-end">1 day</span>
          <input
            id={id}
            className="avg-price-slider"
            type="range"
            min={MIN_DAYS}
            max={sorted.length}
            step={1}
            value={lookback}
            onChange={(event) => setDays(Number(event.target.value))}
          />
          <span className="avg-price-end">{sorted.length} days</span>
        </label>
      )}
    </div>
  );
}
