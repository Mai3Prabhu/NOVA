const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

/** FastAPI's HTTPException gives {detail: string}; its own 422 validation
 * errors give {detail: [{msg, loc, ...}, ...]}. Normalize both to one string. */
function readDetail(body) {
  if (!body || body.detail == null) return null;
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) {
    return body.detail.map((item) => item.msg || JSON.stringify(item)).join(" ");
  }
  return JSON.stringify(body.detail);
}

export async function runAgent(request, { signal } = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/agent`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request }),
      signal,
    });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError("Could not reach the agent API. Is the backend running?", 0);
  }

  let body = null;
  try {
    body = await response.json();
  } catch {
    // non-JSON body; fall through with body === null
  }

  if (!response.ok) {
    throw new ApiError(readDetail(body) || `Request failed (${response.status}).`, response.status);
  }
  return body;
}

export async function checkHealth({ signal } = {}) {
  try {
    const response = await fetch(`${API_BASE_URL}/health`, { signal });
    if (!response.ok) return false;
    const body = await response.json();
    return body?.status === "ok";
  } catch {
    return false;
  }
}

export function downloadUrlFor(path) {
  return `${API_BASE_URL}${path}`;
}
