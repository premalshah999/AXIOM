import { useEffect } from "react";
import { useWorkspace } from "./context";
import { KnowledgeView } from "./knowledge";
import { ProfileProvider, ProfileSettings } from "./profile";
import { PAGE_TABS } from "./shell/nav";
import { RouteTabs } from "./ui/index";

// Settings is in settings.tsx; main.tsx still loads it from here.
export { SettingsView } from "./settings";

// Profile is one page with tabs (nav.ts lists them; context.ts sends old Setup links to the right one).
export function ProfileView() {
  const { route } = useWorkspace();
  const tabs = PAGE_TABS.profile!;
  const tab = tabs.some((t) => t.id === route.tab) ? route.tab : tabs[0]!.id;
  // Links such as ?view=profile&tab=experience&filter=preferences land on that section.
  useEffect(() => {
    if (tab === "experience" && route.filter === "preferences")
      document.getElementById("profile-preferences")?.scrollIntoView({ block: "start" });
  }, [tab, route.filter]);
  return (
    <div className="ax-page">
      <RouteTabs view="profile" tabs={tabs} label="Profile" current={tab} />
      <ProfileProvider>
        {tab === "identity" && <ProfileSettings sections={["identity"]} />}
        {tab === "experience" && <ProfileSettings sections={["experience"]} />}
        {tab === "knowledge" && <KnowledgeView />}
      </ProfileProvider>
    </div>
  );
}
