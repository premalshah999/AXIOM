import { routeUrl, useWorkspace, type View } from "../context";

export type TabItem = { id: string; label: string; count?: number };

/**
 * Underline tabs that set the route `tab` for `view`. They are real links (middle-click and copy-link work) and mark
 * the current one with aria-current. `current` is the active id (falls back to the first tab when the route's is unknown).
 */
export function RouteTabs({ view, tabs, label, current }: { view: View; tabs: TabItem[]; label: string; current?: string }) {
  const { route, go } = useWorkspace();
  const active = tabs.some((t) => t.id === (current ?? route.tab)) ? (current ?? route.tab) : tabs[0]?.id;
  return (
    <nav className="ax-tabs" aria-label={label}>
      {tabs.map((t) => (
        <a
          key={t.id}
          className="ax-tab"
          href={routeUrl({ view, tab: t.id })}
          aria-current={active === t.id ? "page" : undefined}
          onClick={(e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
            e.preventDefault();
            go({ view, tab: t.id });
          }}
        >
          {t.label}
          {t.count !== undefined && <span className="ax-tab-count">{t.count}</span>}
        </a>
      ))}
    </nav>
  );
}

/** The same look for local state (no route): a tablist of buttons. */
export function Tabs({
  tabs,
  value,
  onChange,
  label,
}: {
  tabs: TabItem[];
  value: string;
  onChange: (id: string) => void;
  label: string;
}) {
  return (
    <div className="ax-tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          role="tab"
          className="ax-tab"
          aria-selected={value === t.id}
          onClick={() => onChange(t.id)}
        >
          {t.label}
          {t.count !== undefined && <span className="ax-tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}
