import type { CSSProperties, ReactNode } from "react";
import { Button } from "./Button";

export type Column<T> = {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  /** Right-align (numbers). */
  num?: boolean;
  /** CSS width for the column (and the truncation width). */
  width?: number | string;
  /** One line, ellipsis at `width` (default 280px). Without it the cell is nowrap and sizes to its content. */
  truncate?: boolean;
  /** Let the cell wrap onto several lines (rows grow past 40px). */
  wrap?: boolean;
};

/**
 * Table shell: sticky head, one-line 40px rows, row hover, optional selection with a bulk-action bar, and loading,
 * empty and error states. Scrolls sideways inside its own panel. For a sticky head against the page, put the table on
 * a page with className "ax-page ax-page-fill" and pass `fill`: the table then is the page's only vertical scroller.
 */
export function Table<T>({
  columns,
  rows,
  rowKey,
  label,
  onRowClick,
  selected,
  onSelectedChange,
  bulk,
  loading,
  error,
  empty,
  fill,
  minWidth,
  canSelect,
  busy,
  rowLabel,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Accessible name of the table. */
  label: string;
  onRowClick?: (row: T) => void;
  /** Selected row keys. Passing onSelectedChange turns on the checkbox column. */
  selected?: ReadonlySet<string>;
  onSelectedChange?: (next: Set<string>) => void;
  /** Bulk actions (buttons) shown beside "N selected" when anything is selected. */
  bulk?: ReactNode;
  loading?: boolean;
  error?: ReactNode;
  /** Shown when there are no rows (an <Empty/>). */
  empty?: ReactNode;
  fill?: boolean;
  minWidth?: number;
  /** Rows this returns false for show a disabled checkbox and are left out of select-all. */
  canSelect?: (row: T) => boolean;
  /** A new page or filter is loading over the rows shown: they dim and stay in place, so the table never jumps. */
  busy?: boolean;
  /** Accessible name for a row's checkbox ("Select ExampleSoft"). */
  rowLabel?: (row: T) => string;
}) {
  const selectable = !!onSelectedChange;
  const chosen = selected ?? new Set<string>();
  const keys = rows.filter((r) => !canSelect || canSelect(r)).map(rowKey);
  const all = keys.length > 0 && keys.every((k) => chosen.has(k));
  const toggle = (k: string) => {
    const next = new Set(chosen);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    onSelectedChange?.(next);
  };
  const state = error ? "error" : loading && rows.length === 0 ? "loading" : rows.length === 0 ? "empty" : "rows";
  return (
    <div className={"ax-table-wrap" + (fill ? " is-fill" : "")}>
      {selectable && chosen.size > 0 && (
        <div className="ax-bulk" role="status">
          <span className="ax-bulk-count">{chosen.size} selected</span>
          <div className="ax-bulk-actions">
            {bulk}
            <Button size="sm" variant="ghost" onClick={() => onSelectedChange?.(new Set())}>
              Clear
            </Button>
          </div>
        </div>
      )}
      <div className="ax-table-scroll">
        <table
          className={"ax-table" + (busy ? " is-busy" : "")}
          aria-label={label}
          aria-busy={loading || busy || undefined}
          style={minWidth ? { minWidth } : undefined}
        >
          <thead>
            <tr>
              {selectable && (
                <th className="is-check" scope="col">
                  <input
                    type="checkbox"
                    aria-label="Select all rows"
                    disabled={keys.length === 0}
                    checked={all}
                    onChange={() => onSelectedChange?.(all ? new Set() : new Set(keys))}
                  />
                </th>
              )}
              {columns.map((c) => (
                <th key={c.key} scope="col" className={c.num ? "is-num" : undefined} style={c.width ? { width: c.width } : undefined}>
                  {c.header}
                </th>
              ))}
            </tr>
          </thead>
          {state === "rows" && (
            <tbody>
              {rows.map((row) => {
                const k = rowKey(row);
                return (
                  <tr
                    key={k}
                    className={onRowClick ? "is-click" : undefined}
                    aria-selected={selectable ? chosen.has(k) : undefined}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                  >
                    {selectable && (
                      <td className="is-check" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="checkbox"
                          aria-label={rowLabel ? "Select " + rowLabel(row) : "Select row"}
                          disabled={!!canSelect && !canSelect(row)}
                          checked={chosen.has(k)}
                          onChange={() => toggle(k)}
                        />
                      </td>
                    )}
                    {columns.map((c) => {
                      const style: CSSProperties | undefined = c.truncate ? { maxWidth: c.width ?? 280 } : undefined;
                      return (
                        <td
                          key={c.key}
                          className={[c.num ? "is-num" : "", c.truncate ? "is-trunc" : "", c.wrap ? "is-wrap" : ""].filter(Boolean).join(" ") || undefined}
                          style={style}
                        >
                          {c.cell(row)}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          )}
        </table>
        {state === "loading" && (
          <div className="ax-table-state" role="status" aria-label="Loading">
            {[0, 1, 2, 3, 4].map((i) => (
              <span key={i} className="ax-skeleton" style={{ marginBottom: 14, width: `${88 - i * 9}%` }} />
            ))}
          </div>
        )}
        {state === "empty" && <div className="ax-table-state">{empty}</div>}
        {state === "error" && (
          <div className="ax-table-state" role="alert">
            {error}
          </div>
        )}
      </div>
    </div>
  );
}
