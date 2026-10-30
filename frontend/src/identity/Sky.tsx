import type { ReactNode } from "react";
import "./identity.css";

/** The graded sky behind Overview's sentence, with the moon or sun low on its right. Overview only. */
export function Sky({ children }: { children: ReactNode }) {
  return (
    <div className="ax-sky">
      <div className="ax-orb" aria-hidden="true" />
      {children}
    </div>
  );
}
