import type { ApiError } from "../api";
export function ErrorBanner({
  message,
  error,
}: {
  message?: string;
  error?: Error | ApiError;
}) {
  return (
    <div role="alert" className="error-banner">
      {message ?? error?.message ?? "The request failed. Reload and try again."}
      {error && "code" in error && (
        <details>
          <summary>Details</summary>
          <pre>{JSON.stringify(error, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}
