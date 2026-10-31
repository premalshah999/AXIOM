import { useEffect, useState } from "react";

export function usePagination<T>(items: T[], filterKey = "", initialSize = 25) {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(initialSize);
  // A new filter starts at page 1; polling that changes the list length only
  // clamps the current page, so the reader keeps their place.
  useEffect(() => setPage(1), [filterKey, size]);
  const pages = Math.max(1, Math.ceil(items.length / size));
  const current = Math.min(page, pages);
  return {
    items: items.slice((current - 1) * size, current * size),
    total: items.length,
    page: current,
    pages,
    size,
    initialSize,
    setPage,
    setSize,
  };
}

export type Paging = ReturnType<typeof usePagination>;

function pageList(page: number, pages: number) {
  const set = new Set([1, page - 1, page, page + 1, pages]);
  const shown = [...set].filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b);
  // Never hide a single page behind an ellipsis.
  const out: number[] = [];
  for (const n of shown) {
    const last = out[out.length - 1];
    if (last && n - last === 2) out.push(last + 1);
    out.push(n);
  }
  return out;
}

export function Pagination({
  paging,
  label = "List pages",
  sizes = true,
}: {
  paging: Paging;
  label?: string;
  sizes?: boolean;
}) {
  const { page, pages, total, size, setPage, setSize } = paging;
  if (!total) return null;
  const numbers = pageList(page, pages);
  return (
    <nav className="ax-pager" aria-label={label}>
      <span className="ax-pager-range">
        {(page - 1) * size + 1}–{Math.min(page * size, total)} of{" "}
        {total.toLocaleString()}
      </span>
      {pages > 1 && (
        <div className="ax-pager-pages">
          <button
            className="ax-pager-btn"
            disabled={page === 1}
            onClick={() => setPage(page - 1)}
            aria-label="Previous page"
          >
            ‹
          </button>
          {numbers.map((n, i) => (
            <span key={n} className="ax-pager-item">
              {i > 0 && n - numbers[i - 1] > 1 && (
                <span className="ax-pager-gap" aria-hidden="true">
                  …
                </span>
              )}
              <button
                className="ax-pager-btn"
                aria-label={`Page ${n}`}
                aria-current={page === n ? "page" : undefined}
                onClick={() => setPage(n)}
              >
                {n}
              </button>
            </span>
          ))}
          <button
            className="ax-pager-btn"
            disabled={page === pages}
            onClick={() => setPage(page + 1)}
            aria-label="Next page"
          >
            ›
          </button>
        </div>
      )}
      {sizes && total > Math.min(25, paging.initialSize) && (
        <label className="ax-pager-size">
          <span>Rows</span>
          <select
            aria-label="Rows per page"
            value={size}
            onChange={(e) => setSize(Number(e.target.value))}
          >
            {[...new Set([paging.initialSize, 25, 50, 100])]
              .sort((a, b) => a - b)
              .map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
          </select>
        </label>
      )}
    </nav>
  );
}
