import type { ReactNode } from "react";
import { dateTime, price, shortDate, usd } from "../../format";
import type { InsiderRow, InsiderSection, ValueOrigin } from "../../researchTypes";
import { Notes } from "./ui";

const ORIGIN_MARK: Record<ValueOrigin, string> = { bpiq: "B", sec: "S", "bpiq+sec": "B+S" };
const ORIGIN_TITLE: Record<ValueOrigin, string> = {
  bpiq: "Value from BPIQ",
  sec: "Value from the SEC filing",
  "bpiq+sec": "BPIQ and the SEC filing agree on this value",
};
const MATCH_LABEL: Record<string, string> = {
  matched: "Matched to SEC filing",
  ambiguous: "Ambiguous — unclassified",
  unmatched: "No SEC match — unclassified",
  insufficient: "Too little detail to match",
  not_attempted: "SEC enrichment off",
};
const AMENDMENT_LABEL: Record<string, string> = {
  amendment: "From amendment",
  added_by_amendment: "Added by amendment",
  amended_holdings_only: "Filing amended (holdings only)",
  unreconciled_amendment: "Unreconciled amendment — not counted",
};

function NotProvided({ row, field }: { row: InsiderRow; field: string }) {
  const unavailable = row.unavailable_fields.includes(field);
  return (
    <span className="muted" title={unavailable ? "The source does not provide this field" : "Not reported in the source"}>
      Not provided
    </span>
  );
}

/** A value with a small mark showing which provider supplied it. Missing values read "Not provided", never zero. */
function Val({ row, field, children }: { row: InsiderRow; field: string; children: ReactNode }) {
  const origin = row.field_sources[field];
  const missing = children == null || children === "";
  return (
    <>
      {missing ? <NotProvided row={row} field={field} /> : children}
      {!missing && origin && (
        <sup className="origin-mark" title={ORIGIN_TITLE[origin]}>
          {ORIGIN_MARK[origin]}
        </sup>
      )}
    </>
  );
}

function num(value: number | null): string | null {
  return value == null ? null : value.toLocaleString();
}

function Provenance({ row }: { row: InsiderRow }) {
  const bpiq = row.origin !== "sec" ? row.source : null;
  const sec = row.sec_source ?? (row.origin === "sec" ? row.source : null);
  return (
    <div className="cell-stack insider-stack">
      {row.match && row.origin !== "sec" && (
        <span className={`tag ${row.match.status === "matched" ? "" : "tag-warn"}`} title={row.match.note ?? matchDetail(row)}>
          {MATCH_LABEL[row.match.status]}
        </span>
      )}
      {bpiq && <span className="sub">BPIQ · retrieved {dateTime(bpiq.retrieved_at)}</span>}
      {sec && row.filing && (
        <span className="sub">
          SEC Form {row.filing.form} · retrieved {dateTime(sec.retrieved_at)}
        </span>
      )}
      {row.match?.status === "matched" && <span className="sub">{matchDetail(row)}</span>}
    </div>
  );
}

function matchDetail(row: InsiderRow): string {
  if (!row.match) return "";
  const parts = [`Compared: ${row.match.compared.join(", ")}`];
  if (row.match.not_compared.length) parts.push(`not compared: ${row.match.not_compared.join(", ")}`);
  return parts.join("; ");
}

function TransactionCell({ row }: { row: InsiderRow }) {
  return (
    <div className="cell-stack insider-stack insider-tx">
      <span>
        <Val row={row} field={row.transaction_code ? "transaction_code" : "acquired_disposed"}>
          {row.transaction_label}
        </Val>
        {row.transaction_code && <code title="Raw SEC transaction code"> {row.transaction_code}</code>}
        {!row.transaction_code && row.acquired_disposed && <code title="BPIQ acquired/disposed flag"> {row.acquired_disposed}</code>}
      </span>
      <span className="sub">
        <Val row={row} field="security_type">{row.security_type}</Val>
        {row.table && ` · ${row.table === "derivative" ? "derivative table" : "non-derivative table"}`}
      </span>
      {row.amendment_status && AMENDMENT_LABEL[row.amendment_status] && <span className="tag tag-warn">{AMENDMENT_LABEL[row.amendment_status]}</span>}
      {row.note && <span className="sub wrap">{row.note}</span>}
      {row.footnotes.length > 0 && (
        <details className="sub wrap">
          <summary>Footnotes ({row.footnotes.length})</summary>
          <ul className="footnote-list">
            {row.footnotes.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function InsiderTable({ rows, label }: { rows: InsiderRow[]; label: string }) {
  return (
    <div className="mini-table-scroll" tabIndex={0} aria-label={label}>
      <table className="mini-table">
        <thead>
          <tr>
            <th scope="col">Insider</th>
            <th scope="col">Transaction</th>
            <th scope="col">Transaction date</th>
            <th scope="col">Filing date</th>
            <th scope="col" className="num">Shares</th>
            <th scope="col" className="num">Price</th>
            <th scope="col" className="num">Value</th>
            <th scope="col" className="num">Owned after</th>
            <th scope="col">Ownership</th>
            <th scope="col">Filing</th>
            <th scope="col">Source</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.filing?.accession_number ?? "bpiq"}-${i}`}>
              <td>
                <Val row={r} field="insider_name">{r.insider_name}</Val>
                {r.role && (
                  <span className="sub">
                    {" "}
                    · <Val row={r} field="role">{r.role}</Val>
                  </span>
                )}
              </td>
              <td>
                <TransactionCell row={r} />
              </td>
              <td>
                <Val row={r} field="transaction_date">{r.transaction_date && shortDate(r.transaction_date)}</Val>
              </td>
              <td>
                <Val row={r} field="filing_date">{r.filing_date && shortDate(r.filing_date)}</Val>
              </td>
              <td className="num">
                <Val row={r} field="shares">{num(r.shares)}</Val>
              </td>
              <td className="num">
                <Val row={r} field="price">{r.price == null ? null : price(r.price)}</Val>
              </td>
              <td className="num">{r.value_usd == null ? <span className="muted">Not calculated</span> : usd(r.value_usd)}</td>
              <td className="num">
                <Val row={r} field="shares_owned_after">{num(r.shares_owned_after)}</Val>
              </td>
              <td>
                <Val row={r} field="direct_or_indirect">
                  {r.direct_or_indirect && (r.direct_or_indirect === "D" ? "Direct" : `Indirect${r.nature_of_ownership ? ` (${r.nature_of_ownership})` : ""}`)}
                </Val>
              </td>
              <td>
                {r.source_url && r.filing ? (
                  <a className="link" href={r.source_url} target="_blank" rel="noreferrer noopener" title={`Accession ${r.filing.accession_number}`}>
                    Form {r.filing.form}
                  </a>
                ) : (
                  <NotProvided row={r} field="source_url" />
                )}
                {r.filing && <span className="sub"> · {r.filing.accession_number}</span>}
              </td>
              <td>
                <Provenance row={r} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function InsiderTransactions({ section, unavailable }: { section: InsiderSection; unavailable: ReactNode }) {
  const { mcp, sec, summary } = section;
  const bpiqRows = mcp.records as unknown as InsiderRow[];
  const secOk = sec.state === "ok";
  return (
    <>
      <p className={`callout ${secOk ? "" : "callout-warn"}`}>{section.message}</p>
      {!secOk && sec.reason && <p className="small muted">SEC filing enrichment: {sec.reason}</p>}

      {(bpiqRows.length > 0 || secOk) && (
        <ul className="small insider-summary">
          {secOk && (
            <li>
              {summary.code_p_purchases.label}: <strong>{summary.code_p_purchases.count}</strong>
              {summary.code_p_purchases.count > 0 && ` (${summary.code_p_purchases.shares.toLocaleString()} shares)`}. {summary.code_p_purchases.note}
            </li>
          )}
          <li>
            Unclassified BPIQ rows: {summary.unclassified_bpiq.acquired} acquired, {summary.unclassified_bpiq.disposed} disposed — transaction type unknown;
            excluded from purchase metrics.
          </li>
          {summary.excluded_unreconciled > 0 && <li>{summary.excluded_unreconciled} amended transaction(s) could not be reconciled and are not counted.</li>}
          {sec.superseded.length > 0 && <li>{sec.superseded.length} transaction(s) replaced by a later Form 4/A are hidden and not counted.</li>}
        </ul>
      )}

      <h4 className="section-subtitle">BPIQ insider transactions</h4>
      {mcp.state === "ok" ? (
        <div className="mini-table-wrap">
          <p className="small muted">
            {bpiqRows.length} transaction{bpiqRows.length === 1 ? "" : "s"}. Marks: <sup className="origin-mark">B</sup> BPIQ,{" "}
            <sup className="origin-mark">S</sup> SEC filing, <sup className="origin-mark">B+S</sup> both agree. Transaction and filing dates are
            separate columns.
          </p>
          <InsiderTable rows={bpiqRows} label="BPIQ insider transactions" />
          <Notes items={mcp.notes ?? []} tone="info" />
        </div>
      ) : (
        unavailable
      )}

      {secOk && (
        <>
          <h4 className="section-subtitle">SEC Form 4 transactions not matched to a BPIQ row</h4>
          <p className="small muted">
            {sec.filings_checked} filing(s) since {shortDate(sec.window_start)}, including {sec.amendments ?? 0} amendment(s). Index retrieved{" "}
            {dateTime(sec.index_retrieved_at)}. Shown separately rather than forced onto a BPIQ row.
          </p>
          {sec.records.length > 0 ? (
            <div className="mini-table-wrap">
              <InsiderTable rows={sec.records} label="SEC transactions not matched to BPIQ" />
            </div>
          ) : (
            <p className="small muted">None.</p>
          )}
        </>
      )}
      <Notes items={sec.issues ?? []} />
    </>
  );
}
