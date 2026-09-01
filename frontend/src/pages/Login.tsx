import { useEffect } from "react";

export default function Login() {
  useEffect(() => {
    document.title = "Sign in — CoderLens";
  }, []);

  return (
    <div className="lp">
      <main className="lp-hero">
        <h1 className="lp-title">CoderLens</h1>
        <p className="lp-tag">
          Understand unfamiliar code, fast.<br />
          Sign in to start ingesting your own repositories.
        </p>
        <a className="btn primary lp-github-signin" href="/api/auth/github/login">
          Sign in with GitHub
        </a>
      </main>
    </div>
  );
}
