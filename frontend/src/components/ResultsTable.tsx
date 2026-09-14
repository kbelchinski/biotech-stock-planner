import { useMemo, useState, type KeyboardEvent } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Search } from "lucide-react";
import { EligibilityBadge } from "./StatusBadge";
import { CRITERION_LABEL, ELIGIBILITY_LABEL, STATUS_LABEL, catalystLabel, price, primaryCatalyst, shortDate, usd } from "../format";
import type { CatalystEvaluation, CompanyResult, Eligibility, ScanRun } from "../types";

type SortKey = "ticker" | "days" | "market_cap" | "price" | "turnover" | "eligibility";
type Filter = "all" | Eligibility;

const ELIGIBILITY_RANK: Record<Eligibility, number> = { qualifies: 0, insufficient_data: 1, does_not_qualify: 2 };

interface Row {
  result: CompanyResult;
  primary: CatalystEvaluation | null;
}

interface Props {
  run: ScanRun;
  selectedTicker: string | null;
  onSelect: (ticker: string) => void;
}

const COLUMNS: { label: string; sort?: SortKey; className?: string }[] = [
  { label: "Company", sort: "ticker" },
  { label: "Catalyst" },
  { label: "Date" },
  { label: "Days", sort: "days", className: "num" },
  { label: "Market cap", sort: "market_cap", className: "num" },
  { label: "Price", sort: "price", className: "num" },
  { label: "Avg weekly turnover", sort: "turnover", className: "num" },
  { label: "Result", sort: "eligibility" },
];

function sortValue(row: Row, key: SortKey): number | string | null {
  const r = row.result;
  switch (key) {
    case "ticker":
      return r.ticker;
    case "days":
      return row.primary?.days_until ?? null;
    case "market_cap":
      return r.market_cap_usd;
    case "price":
      return r.price;
    case "turnover":
      return r.avg_weekly_turnover_usd;
    case "eligibility":
      return ELIGIBILITY_RANK[r.eligibility];
  }
}

function compare(a: Row, b: Row, key: SortKey, dir: 1 | -1): number {
  const va = sortValue(a, key);
  const vb = sortValue(b, key);
  if (va === null && vb !== null) return 1; // nulls last in both directions
  if (vb === null && va !== null) return -1;
  if (va !== null && vb !== null && va !== vb) return (va < vb ? -1 : 1) * dir;
  const da = a.primary?.days_until ?? Infinity;
  const db = b.primary?.days_until ?? Infinity;
  return da !== db ? da - db : a.result.ticker.localeCompare(b.result.ticker);
}

export function ResultsTable({ run, selectedTicker, onSelect }: Props) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "eligibility", dir: 1 });

  const rows = useMemo<Row[]>(() => run.results.map((result) => ({ result, primary: primaryCatalyst(result) })), [run]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rows
      .filter(({ result }) => filter === "all" || result.eligibility === filter)
      .filter(({ result }) => {
        if (!q) return true;
        const haystack = [
          result.ticker,
          result.name ?? "",
          ...result.catalysts.flatMap((e) => [e.catalyst.drug_name ?? "", ...e.catalyst.indications]),
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(q);
      })
      .sort((a, b) => compare(a, b, sort.key, sort.dir));
  }, [rows, query, filter, sort]);

  const counts = useMemo(() => {
    const c: Record<Filter, number> = { all: rows.length, qualifies: 0, insufficient_data: 0, does_not_qualify: 0 };
    rows.forEach(({ result }) => (c[result.eligibility] += 1));
    return c;
  }, [rows]);

  const toggleSort = (key: SortKey) =>
    setSort((current) => (current.key === key ? { key, dir: current.dir === 1 ? -1 : 1 } : { key, dir: 1 }));

  const onRowKey = (event: KeyboardEvent<HTMLTableRowElement>, ticker: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(ticker);
    }
  };

  if (run.results.length === 0) return null;

  return (
    <section className="panel results" id="results" aria-labelledby="results-title">
      <div className="results-toolbar">
        <h2 id="results-title">Results</h2>
        <div className="segmented" role="group" aria-label="Filter by result">
          {(["all", "qualifies", "insufficient_data", "does_not_qualify"] as Filter[]).map((value) => (
            <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>
              {value === "all" ? "All" : ELIGIBILITY_LABEL[value]}
              <span className="count">{counts[value]}</span>
            </button>
          ))}
        </div>
        <label className="search">
          <Search aria-hidden size={16} />
          <span className="sr-only">Search results</span>
          <input type="search" placeholder="Search ticker, company, drug…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
      </div>

      <div className="table-wrap" role="region" aria-labelledby="results-title" tabIndex={0}>
        <table className="results-table">
          <thead>
            <tr>
              {COLUMNS.map((col) => {
                const active = col.sort && sort.key === col.sort;
                return (
                  <th
                    key={col.label}
                    scope="col"
                    className={col.className}
                    aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : undefined}
                  >
                    {col.sort ? (
                      <button type="button" className="th-sort" onClick={() => toggleSort(col.sort!)}>
                        {col.label}
                        {active ? (
                          sort.dir === 1 ? <ArrowUp aria-hidden size={13} /> : <ArrowDown aria-hidden size={13} />
                        ) : (
                          <ArrowUpDown aria-hidden size={13} className="sort-idle" />
                        )}
                      </button>
                    ) : (
                      col.label
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {visible.map(({ result, primary }) => (
              <tr
                key={result.ticker}
                tabIndex={0}
                className={`row-${result.eligibility}${selectedTicker === result.ticker ? " is-selected" : ""}`}
                onClick={() => onSelect(result.ticker)}
                onKeyDown={(e) => onRowKey(e, result.ticker)}
                aria-label={`${result.ticker}, ${ELIGIBILITY_LABEL[result.eligibility]}. Open details.`}
              >
                <td>
                  <div className="cell-company">
                    <span className="ticker">{result.ticker}</span>
                    <span className="company-name">{result.name ?? "—"}</span>
                  </div>
                </td>
                <td>
                  {primary ? (
                    <div className="cell-catalyst">
                      <span className={primary.catalyst.catalyst_type ? "" : "muted"}>{catalystLabel(primary)}</span>
                      {result.catalysts.length > 1 && (
                        <span className="tag" title={`${result.catalysts.length} catalysts for this company`}>
                          +{result.catalysts.length - 1}
                        </span>
                      )}
                    </div>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="nowrap">{shortDate(primary?.catalyst.catalyst_date)}</td>
                <td className="num">{primary?.days_until ?? "—"}</td>
                <td className="num">{usd(result.market_cap_usd)}</td>
                <td className="num">
                  <div className="cell-stack">
                    <span>{price(result.price)}</span>
                    {result.price_date && <span className="sub">{shortDate(result.price_date)}</span>}
                  </div>
                </td>
                <td className="num">
                  <TurnoverCell result={result} />
                </td>
                <td>
                  <div className="cell-result">
                    <EligibilityBadge value={result.eligibility} />
                    <span className="dots" aria-hidden>
                      {result.criteria
                        .filter((c) => c.key !== "runway")
                        .map((c) => (
                          <span key={c.key} className={`dot dot-${c.status}`} title={`${CRITERION_LABEL[c.key]}: ${STATUS_LABEL[c.status]}`} />
                        ))}
                    </span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {visible.length === 0 && (
          <div className="table-empty">
            No companies match{query ? ` “${query}”` : " this filter"}.{" "}
            <button type="button" className="link" onClick={() => { setQuery(""); setFilter("all"); }}>
              Clear filters
            </button>
          </div>
        )}
      </div>
      <p className="table-foot">
        {visible.length} of {run.results.length} companies · Select a row for the full explanation of each criterion
      </p>
    </section>
  );
}

function TurnoverCell({ result }: { result: CompanyResult }) {
  if (result.avg_weekly_turnover_usd == null) return <span className="muted">Insufficient history</span>;
  const approx = result.turnover_method?.includes("APPROXIMATION");
  return (
    <span className="cell-inline" title={result.turnover_method ?? undefined}>
      {result.turnover_is_lower_bound && "≥ "}
      {usd(result.avg_weekly_turnover_usd)}
      {approx && <span className="tag" title="Includes close × volume approximation">≈</span>}
    </span>
  );
}
