/** Last-resort guard around the whole app.
 *
 *  React unmounts the entire tree when a render throws and nothing catches it,
 *  which leaves a blank white page — no message, no way back, and the real
 *  error visible only in the devtools console. Any uncaught render error now
 *  lands here instead, showing what broke and a reload button. */

import React from "react";
import { ErrorState } from "./PageState";

type Props = { children: React.ReactNode };
type State = { error: Error | null };

export default class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("Unhandled render error:", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <ErrorState
        title="The page hit an unexpected error."
        message={this.state.error.message}
        onRetry={() => window.location.reload()}
      />
    );
  }
}
