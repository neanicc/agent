import type { Metadata } from "next";

export const metadata: Metadata = { title: "Sign in" };

type SignInPageProps = {
  searchParams: Promise<{ error?: string; return_to?: string }>;
};

export default async function SignInPage({ searchParams }: SignInPageProps) {
  const query = await searchParams;
  const returnTo = safeReturnTo(query.return_to);
  const href = `/auth/start?return_to=${encodeURIComponent(returnTo)}`;
  return (
    <main className="auth-layout">
      <section aria-labelledby="sign-in-title" className="auth-panel">
        <div>
          <p className="eyebrow">Developer control plane</p>
          <h1 id="sign-in-title">See what your agents are doing. Intervene safely.</h1>
          <p className="auth-panel__lede">
            LoopGuard turns run events, verification proof, and explicit approvals into one quiet operational view.
          </p>
        </div>
        <div className="auth-panel__action">
          {query.error ? (
            <p className="auth-error" role="alert">
              Sign-in could not be completed. Start a fresh, secure session.
            </p>
          ) : null}
          <a className="button" href={href}>Continue with your organization</a>
          <p>Authorization uses your organization’s identity provider. LoopGuard never receives your password.</p>
        </div>
      </section>
    </main>
  );
}

function safeReturnTo(value: string | undefined): string {
  if (!value?.startsWith("/") || value.startsWith("//") || value.includes("\\")) return "/inbox";
  return value;
}
