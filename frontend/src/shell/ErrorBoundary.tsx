import { Component, type ReactNode } from "react";

/**
 * A page that throws must not blank the whole app: the sidebar, the palette and every other page keep working. The
 * shell keys this by page, so choosing another page clears the error.
 */
export class PageBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error) {
    console.error("page crashed:", error);
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="ax-empty" role="alert">
        <p className="ax-empty-title">This page hit an error</p>
        <p className="ax-empty-hint">{this.state.error.message || "Something went wrong while drawing it."} The rest of AXIOM is unaffected.</p>
        <div className="ax-empty-action">
          <button type="button" className="ax-btn is-sm" onClick={() => location.reload()}>
            Reload the page
          </button>
        </div>
      </div>
    );
  }
}
