import { useTheme } from "./prefs";

/** The theme control: a small arched window whose moon sets into dawn. Night and Dawn are the two themes' names. */
export function HourToggle() {
  const [theme, toggle] = useTheme();
  const night = theme === "dark";
  return (
    <button type="button" className="ax-hour" aria-label={night ? "Switch to dawn theme" : "Switch to night theme"} title={night ? "Switch to dawn" : "Switch to night"} onClick={toggle}>
      <span className="ax-hour-window" aria-hidden="true">
        <i />
      </span>
      <span className="ax-hour-name">{night ? "Night" : "Dawn"}</span>
    </button>
  );
}
