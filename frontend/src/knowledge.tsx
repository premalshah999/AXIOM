// Profile › Knowledge: the optional questionnaires first, then what the app already knows about you (facts, employer
// memory, saved answers, policies). Questionnaire forms live in qform.tsx so the setup guide can embed the same ones.
import { useEffect, useState } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useDraft } from "./hooks";
import { Attestations, EmployerMemory, Facts, useFeature } from "./features";
import { LearnedAnswers, ProfileSettings, type ProfileSection } from "./autonomy";
import { ErrorLine, Loading } from "./ui";
import { Tabs } from "./ui/index";
import { ModuleForm, ModuleList, Unavailable, useQuestionnaires } from "./qform";
import "./styles/knowledge.css";

function Questionnaires() {
  const { data, error, reload, setModule } = useQuestionnaires();
  const [open, setOpen] = useDraft<string>("knowledge-module", "");
  const module = data?.modules.find((m) => m.id === open);
  // The form is its own focus: move to the top of the region when one opens or closes.
  useEffect(() => {
    document.querySelector(".ax-scroll")?.scrollTo({ top: 0 });
  }, [open]);
  if (error && !data) return <Unavailable error={error} retry={reload} />;
  if (!data) return <Loading label="Loading the questionnaires…" />;
  if (module) return <ModuleForm module={module} onModule={setModule} onClose={() => setOpen("")} />;
  const { answered, total } = data.completion;
  return (
    <div className="ax-kn-list">
      <p className="ax-kn-lead">
        Answer these once and every application uses them. All of it is optional: skip anything, come back any time, and
        nothing here stops an application from starting.{" "}
        <span className="ax-kn-count">
          {answered} of {total} answered.
        </span>
      </p>
      <ModuleList data={data} onOpen={setOpen} />
    </div>
  );
}

const PROFILE_PARTS: ProfileSection[] = ["facts", "policies", "rules"];

export function KnowledgeView() {
  const probe = useFeature<{
    categories: Record<string, Record<string, number>>;
    employers: number;
    store_version: number;
  }>("knowledge", (signal) => api("/v2/knowledge/summary", "GET", undefined, signal), 120000);
  const { route } = useWorkspace();
  const [tab, setTab] = useDraft("knowledge-view", "questionnaires");
  // Facts, policies and option rules moved here from the old Profile page; they still share its one form.
  useEffect(() => {
    if (route.filter && PROFILE_PARTS.includes(route.filter as ProfileSection)) setTab(route.filter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.filter]);
  const full = !probe.unavailable;
  const tabs = [
    { id: "questionnaires", label: "Questionnaires" },
    ...(full
      ? [
          { id: "summary", label: "Fact summary" },
          { id: "employers", label: "Employer memory", count: probe.data?.employers },
        ]
      : []),
    { id: "answers", label: "Saved answers" },
    { id: "facts", label: "Facts" },
    { id: "policies", label: "Policies" },
    { id: "rules", label: "When options don't match" },
  ];
  const current = tabs.some((t) => t.id === tab) ? tab : "questionnaires";
  return (
    <div className="ax-kn">
      <Tabs tabs={tabs} value={current} onChange={setTab} label="Knowledge views" />
      {current === "questionnaires" && <Questionnaires />}
      {PROFILE_PARTS.includes(current as ProfileSection) && <ProfileSettings sections={[current as ProfileSection]} />}
      {current === "answers" && (
        <>
          {!full && (
            <p className="ax-kn-lead">
              Categorized facts and employer memory appear here once the knowledge service is enabled. Saved answers work
              as before.
            </p>
          )}
          <LearnedAnswers />
        </>
      )}
      {full && (current === "summary" || current === "employers") &&
        (probe.data ? (
          current === "summary" ? (
            <>
              <Attestations />
              <Facts summary={probe.data.categories} />
            </>
          ) : (
            <EmployerMemory />
          )
        ) : probe.error ? (
          <ErrorLine error={probe.error} retry={probe.refresh} />
        ) : (
          <Loading />
        ))}
    </div>
  );
}
