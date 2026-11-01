// Settings: one page, a list of sections beside the one that is open. Each section is what a person recognises (a service, the
// mailbox, how it applies, their account), and the engine's own switches are last, under Advanced. Old addresses resolve in
// context.ts; the section is the route's `tab` (nav.ts lists them).
import "./identity/overview.css";
import { Fragment, useEffect, useRef, type ComponentType } from "react";
import { routeUrl, useWorkspace } from "./context";
import { AccountSettings } from "./accounts";
import { SignInSettings } from "./account";
import { ConnectionSettings } from "./connections";
import { DatabaseSettings } from "./database";
import { DiscoverySettings } from "./discovery";
import { MailSettings } from "./mail";
import { PAGE_TABS } from "./shell/nav";
import { AdvancedSettings, ApplyingSettings } from "./system";
import "./styles/settings.css";

// The Database section: where AXIOM keeps its data (database.tsx owns the choice and the move).
function DatabaseSection() {
  return (
    <section className="st-group" id="database" aria-labelledby="st-database">
      <div className="st-head">
        <div>
          <h2 id="st-database">Database</h2>
          <p>Where AXIOM keeps its data.</p>
        </div>
      </div>
      <DatabaseSettings />
    </section>
  );
}

const SECTIONS: Record<string, ComponentType> = {
  connections: ConnectionSettings,
  email: MailSettings,
  discovery: DiscoverySettings,
  applying: ApplyingSettings,
  accounts: AccountSettings,
  account: SignInSettings,
  database: DatabaseSection,
  system: AdvancedSettings,
};

// Where a rule falls between the sections that are about the work and the ones about the installation.
const RULE_BEFORE = "account";

export function SettingsView() {
  const { route, go } = useWorkspace();
  const tabs = PAGE_TABS.settings!;
  const tab = tabs.some((t) => t.id === route.tab) ? route.tab! : tabs[0]!.id;
  const Section = SECTIONS[tab] ?? ConnectionSettings;
  // On a phone the list of sections scrolls sideways: keep the open one in view.
  const nav = useRef<HTMLElement>(null);
  useEffect(() => {
    const now = nav.current?.querySelector<HTMLElement>('[aria-current="page"]');
    if (nav.current && now && nav.current.scrollWidth > nav.current.clientWidth)
      nav.current.scrollTo({ left: now.offsetLeft - 16, behavior: "auto" });
  }, [tab]);
  return (
    <div className="ax-page">
      <div className="st-layout">
        <nav className="st-nav" aria-label="Settings" ref={nav}>
          {tabs.map((t) => (
            <Fragment key={t.id}>
              {t.id === RULE_BEFORE && <hr />}
              <a
                href={routeUrl({ view: "settings", tab: t.id })}
                aria-current={tab === t.id ? "page" : undefined}
                onClick={(e) => {
                  if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;
                  e.preventDefault();
                  go({ view: "settings", tab: t.id });
                }}
              >
                {t.label}
              </a>
            </Fragment>
          ))}
        </nav>
        <div className="st-main" key={tab}>
          <Section />
        </div>
      </div>
    </div>
  );
}
