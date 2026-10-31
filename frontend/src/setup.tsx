import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useAction } from "./hooks";
import { TailoredResumes } from "./features";
import { KnowledgeView } from "./knowledge";
import { ProfileProvider, ProfileSettings } from "./profile";
import { Actions, FormGrid } from "./setup-kit";
import { day } from "./format";
import { PAGE_TABS } from "./shell/nav";
import { Button, ConfirmButton, Dropzone, Empty, Input, Panel, RouteTabs } from "./ui/index";

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

// Resumes: its own page since 2026-10-31 (it was a tab of Profile). The old address, ?view=profile&tab=resumes, opens it.
export function ResumesView() {
  return (
    <div className="ax-page">
      <ProfileProvider>
        <Resumes />
        <TailoredResumes />
      </ProfileProvider>
    </div>
  );
}

function Resumes() {
  const { snapshot, refresh } = useWorkspace();
  const { pending, run } = useAction();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [roles, setRoles] = useState("");
  const resumes = snapshot.resumes || [];
  const full = resumes.length >= 10;
  return (
    <>
      <Panel title={`Library · ${resumes.length} of 10`}>
        {resumes.length ? (
          <ul className="ax-rows">
            {resumes.map((r) => (
              <li className="ax-row" key={r.id}>
                <span className="ax-row-main">
                  <strong>{r.name}</strong>
                  <small>
                    {(r.roles || []).length ? (r.roles || []).join(", ") + " · " : ""}added {day(r.created_at)} ·{" "}
                    <span className="mono">{(r.sha256 || "").slice(0, 10)}</span>
                  </small>
                </span>
                <span className="ax-row-end">
                  <a className="ax-btn is-sm" href={`/api/resumes/${r.id}/download`}>
                    <Download size={14} aria-hidden="true" /> Download
                  </a>
                  <ConfirmButton
                    label="Remove"
                    confirm="Remove resume"
                    note="The file stays with past receipts."
                    pending={pending === r.id}
                    onConfirm={() =>
                      run(
                        r.id,
                        async () => {
                          await api("/resumes/" + r.id, "DELETE");
                          await refresh();
                        },
                        "Resume removed from the library.",
                      )
                    }
                  />
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <Empty title="No resumes yet" hint="Add a text-based PDF below. Ranking and applications need at least one." />
        )}
      </Panel>
      <Panel title="Add a resume">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!file) return;
            const f = new FormData();
            f.append("file", file);
            f.append("name", name.trim());
            f.append("roles", roles);
            run(
              "upload",
              async () => {
                await api("/resumes", "POST", f);
                setFile(null);
                setName("");
                setRoles("");
                await refresh();
              },
              "Resume uploaded.",
            );
          }}
        >
          <FormGrid>
            <Dropzone
              label="Resume PDF"
              title="Drop a resume, or click to choose"
              hint="Text-based PDF, up to 5 MB and 20 pages."
              accept="application/pdf,.pdf"
              file={file}
              onFile={(f) => {
                setFile(f);
                if (f && !name) setName(f.name.replace(/\.pdf$/i, ""));
              }}
            />
            <Input
              label="Name"
              required
              maxLength={120}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Backend engineering"
            />
            <Input label="Role tags" hint="Comma separated" value={roles} onChange={(e) => setRoles(e.target.value)} placeholder="Backend, Data" />
          </FormGrid>
          <Actions note={full ? "The library is full. Remove a resume first." : undefined}>
            <Button
              type="submit"
              variant="primary"
              loading={pending === "upload"}
              disabled={!file || full || (pending !== null && pending !== "upload")}
            >
              Add to library
            </Button>
          </Actions>
        </form>
      </Panel>
    </>
  );
}
