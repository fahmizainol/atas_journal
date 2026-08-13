// Thin fetch wrapper. All endpoints live under /api (Vite proxies to :8000 in
// dev; same-origin in prod).

export async function apiGet<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const qs = params ? "?" + toQuery(params) : "";
  const res = await fetch(`/api${path}${qs}`);
  if (!res.ok) {
    const d = await errDetail(res);
    throw new ApiError(`GET ${path} -> ${res.status}${detail(d)}`, res.status, d);
  }
  return res.json() as Promise<T>;
}

/** A failed request, with the status and the parsed `detail` still attached.
 *
 *  The message stays exactly what it always was, so every existing `catch` that
 *  shows `e.message` is unchanged. The extra fields are for the callers that
 *  need to tell one refusal from another — the replay account's create gate
 *  answers 409 with `{code, message, until}`, and "which rule" is the whole
 *  content of that answer. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** The `detail` FastAPI put in the body, parsed once. */
async function errDetail(res: Response): Promise<unknown> {
  try {
    return (await res.json())?.detail ?? null;
  } catch {
    return null;
  }
}

// FastAPI puts human-readable validation messages in {"detail": ...}; surface
// them so a bad strategy config says *what* was bad, not just "400".
function detail(d: unknown): string {
  if (d == null || d === "") return "";
  return `: ${typeof d === "string" ? d : JSON.stringify(d)}`;
}

export async function apiSend<T>(
  method: "PUT" | "POST" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method,
    headers: body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    body: body instanceof FormData ? body : body != null ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const d = await errDetail(res);
    throw new ApiError(`${method} ${path} -> ${res.status}${detail(d)}`, res.status, d);
  }
  return res.json() as Promise<T>;
}

export function toQuery(params: Record<string, unknown>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v == null || v === "") continue;
    if (Array.isArray(v)) {
      if (v.length) sp.set(k, v.join(","));
    } else {
      sp.set(k, String(v));
    }
  }
  return sp.toString();
}
