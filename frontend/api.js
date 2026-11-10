// Shared by every page: calls the backend REST API with a timeout and turns errors into plain sentences.

async function api(path, { method = "GET", body, token, timeoutMs } = {}) {
  const cfg = window.APP_CONFIG;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs || cfg.requestTimeoutMs);
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers["X-Admin-Token"] = token;

  let response;
  try {
    response = await fetch(cfg.apiBase + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (err) {
    throw new Error(err.name === "AbortError"
      ? "The server took too long to answer. Please try again."
      : "Cannot reach the server. Check that the app is running.");
  } finally {
    clearTimeout(timer);
  }

  let data = null;
  try { data = await response.json(); } catch (err) { /* the body was not JSON */ }
  if (!response.ok) throw new Error(describeError(response.status, data));
  return data;
}

function describeError(status, data) {
  const detail = data && data.detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg).join(" ");   // FastAPI validation errors
  if (typeof detail === "string") return detail;
  return `The server answered with error ${status}.`;
}

// Tiny element builder so pages never inject untrusted text as HTML.
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined) node.append(child instanceof Node ? child : String(child));
  }
  return node;
}
