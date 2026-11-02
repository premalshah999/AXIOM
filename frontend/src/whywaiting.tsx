// Why nothing is starting, in the words every screen uses (jobpilot/waiting.py), and the one thing to press about it.
import { useWorkspace } from "./context";
import type { WaitAsk, WaitReason } from "./types";

export const whyLine = (why: WaitReason) => [why.says, why.next].filter(Boolean).join(" ");

export function WhyAction({ reason, className = "ax-link" }: { reason: WaitReason; className?: string }) {
  const { go } = useWorkspace();
  const action = reason.action;
  if (!action) return null;
  return (
    <button type="button" className={className} onClick={() => go({ view: action.view, tab: action.tab })}>
      {action.label}
    </button>
  );
}

/** "41 are over the employer limit and 45 are in Reserved: approve them to give workers more to do", with a way to each. */
export function WhyAsks({ ask, className = "ax-link" }: { ask: WaitAsk; className?: string }) {
  const { go } = useWorkspace();
  return (
    <>
      {ask.links.map((link) => (
        <button key={link.filter} type="button" className={className} onClick={() => go({ view: link.view, filter: link.filter })}>
          {link.label}
        </button>
      ))}
    </>
  );
}
