// The place for how a job is applied to: "Apply" or "Apply with tailored resume". It sits in a Job board row and in a review batch row,
// and renders nothing until that choice exists. The choice belongs to the job, so whatever it saves is read when the job's turn comes.
import { TailorChoice } from "./tailoring";

export function ApplyChoice({ jobId }: { jobId: string }) {
  return <TailorChoice jobId={jobId} compact />;
}
