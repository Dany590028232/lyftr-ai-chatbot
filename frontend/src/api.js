const API_URL = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(/\/+$/, "");

export function newSessionId() {
  const random =
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : Math.random().toString(16).slice(2) + Date.now().toString(16);
  return `s-${random.replace(/-/g, "").slice(0, 12)}`;
}

function describeError(status, body) {
  const detail = body?.detail;
  if (Array.isArray(detail)) {
    // FastAPI 422 shape: [{loc, msg, ...}]
    return detail
      .map((d) => `${(d.loc || []).filter((p) => p !== "body").join(".")}: ${d.msg}`)
      .join("; ");
  }
  if (typeof detail === "string") return detail;
  return `Request failed (HTTP ${status})`;
}

export async function sendChat({ sessionId, message, systemPrompt, simulateFailure }) {
  const headers = { "Content-Type": "application/json" };
  if (simulateFailure) headers["X-Simulate-Failure"] = simulateFailure;

  const payload = { session_id: sessionId, message };
  if (systemPrompt && systemPrompt.trim()) payload.system_prompt = systemPrompt;

  let response;
  try {
    response = await fetch(`${API_URL}/chat`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
  } catch {
    throw new Error("Cannot reach the server. Check your connection and try again.");
  }

  let body = null;
  try {
    body = await response.json();
  } catch {
    /* non-JSON body */
  }

  if (!response.ok) {
    const error = new Error(describeError(response.status, body));
    error.status = response.status;
    throw error;
  }
  return body;
}
