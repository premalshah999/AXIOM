// Setup › Knowledge: the optional questionnaires first, then what the app already knows about you (facts, employer
// memory, saved answers). Questionnaire forms live in qform.tsx so the setup guide can embed the same ones.
import { useEffect, useState } from "react";
import { api } from "./api";
import { useDraft } from "./hooks";
import { Attestations, EmployerMemory, Facts, useFeature } from "./features";
import { LearnedAnswers } from "./autonomy";
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

export function KnowledgeView() {
  const probe = useFeature<{
    categories: Record<string, Record<string, number>>;
    employers: number;
    store_version: number;
  }>("knowledge", (signal) => api("/v2/knowledge/summary", "GET", undefined, signal), 120000);
  const [tab, setTab] = useDraft("knowledge-view", "questionnaires");
  const full = !probe.unavailable;
  const tabs = [
    { id: "questionnaires", label: "Questionnaires" },
    ...(full
      ? [
          { id: "facts", label: "Facts" },
          { id: "employers", label: "Employer memory", count: probe.data?.employers },
        ]
      : []),
    { id: "answers", label: "Saved answers" },
  ];
  const current = tabs.some((t) => t.id === tab) ? tab : "questionnaires";
  return (
    <div className="ax-kn">
      <Tabs tabs={tabs} value={current} onChange={setTab} label="Knowledge views" />
      {current === "questionnaires" && <Questionnaires />}
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
      {full && current !== "questionnaires" && current !== "answers" &&
        (probe.data ? (
          current === "facts" ? (
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
