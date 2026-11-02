import { Component, type ReactNode } from "react";
import { TriangleAlert } from "lucide-react";
import { Button } from "../ui/index";
import "../styles/screens.css";

/**
 * A page that throws must not blank the whole app: the navigation, the palette and every other page keep working. The
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
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="ax-fail" role="alert">
        <span className="ax-fail-icon">
          <TriangleAlert size={20} aria-hidden="true" />
        </span>
        <h2>This page hit a problem</h2>
        <p>The rest of AXIOM still works, and applications that are running aren't affected. Try again, or open another page from the sidebar.</p>
        <div className="ax-ask">
          <Button variant="primary" onClick={() => this.setState({ error: null })}>
            Try again
          </Button>
          <Button onClick={() => location.reload()}>Reload AXIOM</Button>
        </div>
      </div>
    );
  }
}
