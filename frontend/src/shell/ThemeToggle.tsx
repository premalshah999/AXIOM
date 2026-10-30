import { useTheme } from "./prefs";

/** The theme control: a two-position slide switch, with the theme's name beside it. */
export function ThemeToggle() {
  const [theme, toggle] = useTheme();
  return (
    <div className="ax-theme">
      <button type="button" className="ax-theme-switch" aria-label={theme === "dark" ? "Switch to the light theme" : "Switch to the dark theme"} onClick={toggle} />
      <span className="ax-theme-name" aria-hidden="true">
        {theme === "dark" ? "dark" : "light"}
      </span>
    </div>
  );
}
