// Thin fetch wrapper. All endpoints live under /api (Vite proxies to :8000 in
// dev; same-origin in prod).

export async function apiGet<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const qs = params ? "?" + toQuery(params) : "";
  const res = await fetch(`/api${path}${qs}`);
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}${await detail(res)}`);
  return res.json() as Promise<T>;
}

// FastAPI puts human-readable validation messages in {"detail": ...}; surface
// them so a bad strategy config says *what* was bad, not just "400".
async function detail(res: Response): Promise<string> {
  try {
    const j = await res.json();
    return j?.detail ? `: ${typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)}` : "";
  } catch {
    return "";
  }
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
  if (!res.ok) throw new Error(`${method} ${path} -> ${res.status}${await detail(res)}`);
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
