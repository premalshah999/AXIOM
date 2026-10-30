import type { Awake } from "./awake";
import "./identity.css";

const WORD = { awake: "AWAKE", asleep: "ASLEEP", unknown: "?" };

/** The blade sign on the corner of the building. It says what the machine is doing, not the brand. */
export function Sign({ awake }: { awake: Awake }) {
  const word = WORD[awake.state];
  const said = awake.state === "unknown" ? awake.reason : `${word[0]}${word.slice(1).toLowerCase()}. ${awake.reason}`;
  return (
    <div className={"ax-sign is-" + awake.state} role="img" aria-label={said} title={awake.reason}>
      {[...word].map((c, i) => (
        <span key={i} aria-hidden="true">
          {c}
        </span>
      ))}
    </div>
  );
}
