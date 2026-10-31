import { Component, type ReactNode } from "react";
import { Button, Empty } from "../ui/index";

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
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert">
        <Empty
          title="This page hit an error."
          hint={this.state.error.message || undefined}
          action={<Button onClick={() => location.reload()}>Reload the page</Button>}
        />
      </div>
    );
  }
}
