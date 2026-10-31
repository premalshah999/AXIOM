// Saved answers (Profile › Knowledge). Employer accounts (Settings) are in accounts.tsx.
import { useEffect, useState } from "react";
import { api } from "./api";
import { useAction, useDraft, usePoll } from "./hooks";
import { Pagination, usePagination } from "./pagination";
import type { ReviewedAnswer } from "./types";
import { Actions, FormGrid } from "./setup-kit";
import { Button, ConfirmButton, Empty, ErrorLine, Input, Panel, Select, Table, Textarea } from "./ui/index";
import { SearchBox } from "./worktools";

// ---------- Saved answers ----------
export function LearnedAnswers() {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  useEffect(() => {
    const t = window.setTimeout(() => setTerm(q.trim()), 250);
    return () => window.clearTimeout(t);
  }, [q]);
  const entries = usePoll<{ entries: ReviewedAnswer[] }>(
    (signal) => api("/knowledge?q=" + encodeURIComponent(term), "GET", undefined, signal),
    120000,
    [term],
  );
  const { pending, run } = useAction();
  const [question, setQuestion] = useDraft("kb-question", "");
  const [answer, setAnswer] = useDraft("kb-answer", "");
  const [scope, setScope] = useDraft<ReviewedAnswer["scope"]>("kb-scope", "personal");
  const [employer, setEmployer] = useDraft("kb-employer", "");
  const rows = entries.data?.entries || [];
  const paging = usePagination(rows, term, 25);
  return (
    <div className="ax-sections">
      <Panel title="Add an answer">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            run(
              "add",
              async () => {
                await api("/knowledge", "POST", {
                  question: question.trim(),
                  answer: answer.trim(),
                  options: [],
                  scope,
                  employer: scope === "employer" ? employer.trim() : "",
                });
                setQuestion("");
                setAnswer("");
                await entries.refresh();
              },
              "Answer saved.",
            );
          }}
        >
          <FormGrid>
            <Input
              label="Question"
              required
              minLength={2}
              maxLength={2000}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Do you have experience with Kafka?"
            />
            <Textarea label="Answer" required rows={2} maxLength={8000} value={answer} onChange={(e) => setAnswer(e.target.value)} />
            <Select label="Applies to" value={scope} onChange={(e) => setScope(e.target.value as ReviewedAnswer["scope"])}>
              <option value="personal">Every employer</option>
              <option value="employer">One employer</option>
            </Select>
            {scope === "employer" && (
              <Input label="Employer" required value={employer} onChange={(e) => setEmployer(e.target.value)} />
            )}
          </FormGrid>
          <Actions>
            <Button type="submit" loading={pending === "add"} disabled={pending !== null && pending !== "add"}>
              Save answer
            </Button>
          </Actions>
        </form>
      </Panel>
      <Panel title="Saved answers" actions={<SearchBox label="Search saved answers" placeholder="Search" value={q} onChange={setQ} />} flush>
        <ErrorLine error={entries.error} retry={entries.refresh} />
        <Table
          label="Saved answers"
          rows={paging.items}
          rowKey={(e) => e.id}
          loading={!entries.data && !entries.error}
          minWidth={640}
          empty={<Empty title={term ? "No saved answers match" : "No saved answers yet"} hint={term ? undefined : "Answers you give to genuine questions are saved here and reused."} />}
          columns={[
            { key: "q", header: "Question", wrap: true, width: 260, cell: (e) => e.question },
            { key: "a", header: "Answer", wrap: true, width: 260, cell: (e) => e.answer },
            {
              key: "scope",
              header: "Applies to",
              cell: (e) => (
                <>
                  {e.scope === "personal" ? "Every employer" : e.employer || "One employer"}
                  <span className="ax-hint"> · {e.layer === "narrative" ? "narrative" : "fact"}</span>
                </>
              ),
            },
            {
              key: "act",
              header: <span className="sr-only">Actions</span>,
              pin: true,
              cell: (e) => (
                <ConfirmButton
                  label="Forget"
                  confirm="Forget answer"
                  variant="secondary"
                  pending={pending === e.id}
                  onConfirm={() =>
                    run(
                      e.id,
                      async () => {
                        await api("/knowledge/" + encodeURIComponent(e.id), "DELETE");
                        await entries.refresh();
                      },
                      "Answer forgotten. It won't be reused.",
                    )
                  }
                />
              ),
            },
          ]}
        />
        <Pagination paging={paging} label="Answer pages" />
      </Panel>
    </div>
  );
}
