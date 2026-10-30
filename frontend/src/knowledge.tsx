// Profile › Knowledge: one page, no inner tabs. The questionnaires and the policies are always in view; what AXIOM
// already knows (facts, employer memory, saved answers, option rules) sits in folds that load when opened.
// Questionnaire forms live in qform.tsx so the setup guide can embed the same ones.
import { useEffect, type ReactNode } from "react";
import { api } from "./api";
import { useWorkspace } from "./context";
import { useDraft } from "./hooks";
import { Attestations, EmployerMemory, Facts, useFeature } from "./features";
import { LearnedAnswers } from "./autonomy";
import { ProfileSettings } from "./profile";
import { ErrorLine, Loading, Panel } from "./ui/index";
import { ModuleForm, ModuleList, Unavailable, useQuestionnaires } from "./qform";

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
    <Panel title="Questionnaires" actions={<span className="ax-hint">{answered} of {total} answered</span>}>
      <ModuleList data={data} onOpen={setOpen} />
    </Panel>
  );
}

/** A section that loads only when opened. The page remembers which ones are open while you move around. */
function Fold({
  id,
  title,
  note,
  open,
  onToggle,
  children,
}: {
  id: string;
  title: string;
  note?: string;
  open: boolean;
  onToggle: (open: boolean) => void;
  children: ReactNode;
}) {
  return (
    <details
      id={"knowledge-" + id}
      className="ax-panel ax-fold"
      open={open}
      onToggle={(e) => {
        if (e.currentTarget.open !== open) onToggle(e.currentTarget.open);
      }}
    >
      <summary>
        <strong>{title}</strong>
        {note && <span className="ax-hint">{note}</span>}
      </summary>
      <div className="ax-panel-body">{open && children}</div>
    </details>
  );
}

// ?view=profile&tab=knowledge&filter=facts|policies|rules lands on that section.
const FILTER_TARGET: Record<string, string> = { facts: "facts-json", policies: "policies", rules: "rules" };

export function KnowledgeView() {
  const probe = useFeature<{
    categories: Record<string, Record<string, number>>;
    employers: number;
    store_version: number;
  }>("knowledge", (signal) => api("/v2/knowledge/summary", "GET", undefined, signal), 120000);
  const { route } = useWorkspace();
  const [openIds, setOpenIds] = useDraft<Record<string, boolean>>("knowledge-open", {});
  const fold = (id: string) => ({ id, open: !!openIds[id], onToggle: (open: boolean) => setOpenIds((o) => ({ ...o, [id]: open })) });
  const full = !probe.unavailable;
  useEffect(() => {
    const target = FILTER_TARGET[route.filter || ""];
    if (!target) return;
    if (target !== "policies") setOpenIds((o) => ({ ...o, [target]: true }));
    window.setTimeout(() => document.getElementById("knowledge-" + target)?.scrollIntoView({ block: "start" }), 50);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route.filter]);
  return (
    <div className="ax-sections">
      <Questionnaires />
      <div id="knowledge-policies">
        <ProfileSettings sections={["policies"]} />
      </div>
      {full && (
        <Fold {...fold("summary")} title="Fact summary">
          {probe.data ? (
            <div className="ax-sections">
              <Attestations />
              <Facts summary={probe.data.categories} />
            </div>
          ) : probe.error ? (
            <ErrorLine error={probe.error} retry={probe.refresh} />
          ) : (
            <Loading />
          )}
        </Fold>
      )}
      {full && (
        <Fold {...fold("employers")} title="Employer memory" note={probe.data ? String(probe.data.employers) : undefined}>
          <EmployerMemory />
        </Fold>
      )}
      <Fold {...fold("answers")} title="Saved answers">
        <LearnedAnswers />
      </Fold>
      <Fold {...fold("facts-json")} title="Edit facts as JSON">
        <ProfileSettings sections={["facts"]} />
      </Fold>
      <Fold {...fold("rules")} title="When options don't match">
        <ProfileSettings sections={["rules"]} />
      </Fold>
    </div>
  );
}
