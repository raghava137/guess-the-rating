// Pipeline console: run status, history and logs, model versions, alerts, and admin actions.

const STAGES = ["ingest", "validate", "featurize", "train", "evaluate", "promote"];
const GRAFANA_PANELS = [
  { id: 13, title: "Pipeline throughput" },
  { id: 9, title: "Input drift (PSI)" },
  { id: 7, title: "Live error vs test error" },
  { id: 3, title: "Prediction latency" },
];
let selectedRun = null;

function adminToken() {
  return document.getElementById("token").value.trim();
}

function setAdminStatus(text, kind = "") {
  const box = document.getElementById("admin-status");
  box.textContent = text;
  box.className = `status ${kind}`;
}

function duration(seconds) {
  if (seconds === undefined || seconds === null) return "";
  if (seconds < 90) return `${Math.round(seconds)} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

function startedText(run) {
  return run.started ? run.started.replace("T", " ").slice(0, 16) : "";
}

function renderStages(run) {
  const list = document.getElementById("stages");
  list.replaceChildren(...STAGES.map((name) => {
    const info = (run && run.stages && run.stages[name]) || {};
    let state = info.status || (run && run.status === "running" ? "waiting" : "not run");
    if (state === "done" && info.seconds !== undefined) state = `done in ${duration(info.seconds)}`;
    const css = (info.status || "").split(" ")[0];
    return el("li", { class: css }, el("span", { class: "stage-name" }, name), el("span", { class: "stage-state" }, state));
  }));
}

function summaryText(status) {
  if (status.running) {
    return `Run ${status.running.id} is in progress (started by ${status.running.trigger}, month ${status.running.month || "unchanged"}).`;
  }
  const last = status.last;
  if (!last) return "No runs yet. Start one from the Admin section.";
  let text = `Last run ${last.id} ${last.status === "success" ? "succeeded" : "failed"} in ${duration(last.seconds)}.`;
  const t = last.throughput;
  if (t && t.featurize) {
    text += ` Speed: ingest read ${Math.round(t.ingest || 0).toLocaleString()} games/s,`
      + ` validate ${Math.round(t.validate || 0).toLocaleString()} games/s,`
      + ` Stockfish features ${t.featurize} games/s.`;
  }
  if (last.promotion) text += ` Promotion: ${last.promotion.promoted ? "new model promoted" : "kept the current model"} (${last.promotion.reason}).`;
  return text;
}

async function refreshStatus() {
  try {
    const status = await api("/api/v1/pipeline/status", { timeoutMs: 8000 });
    renderStages(status.running || status.last);
    document.getElementById("run-summary").textContent = summaryText(status);
    if (status.running && selectedRun === status.running.id) loadLog(selectedRun);
  } catch (err) {
    document.getElementById("run-summary").textContent = `Pipeline service: ${err.message}`;
  }
}

async function refreshRuns() {
  try {
    const runs = await api("/api/v1/pipeline/runs?limit=20", { timeoutMs: 8000 });
    const body = document.getElementById("runs");
    if (!runs.length) {
      body.replaceChildren(el("tr", {}, el("td", { colspan: "6" }, "No runs yet.")));
      return;
    }
    body.replaceChildren(...runs.map((run) => {
      const promoted = run.promotion ? (run.promotion.promoted ? `v${run.promotion.challenger} promoted` : "kept") : "";
      const row = el("tr", { class: `selectable${run.id === selectedRun ? " selected" : ""}`, tabindex: "0" },
        el("td", {}, startedText(run)), el("td", {}, run.trigger), el("td", {}, run.month || ""),
        el("td", {}, el("span", { class: `badge ${run.status}` }, run.status)),
        el("td", {}, duration(run.seconds)), el("td", {}, promoted));
      const open = () => { selectedRun = run.id; loadLog(run.id); refreshRuns(); };
      row.addEventListener("click", open);
      row.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
      return row;
    }));
  } catch (err) {
    document.getElementById("runs").replaceChildren(el("tr", {}, el("td", { colspan: "6" }, err.message)));
  }
}

async function loadLog(id) {
  const box = document.getElementById("log");
  try {
    const data = await api(`/api/v1/pipeline/runs/${id}/log`, { timeoutMs: 8000 });
    box.textContent = data.lines.join("\n") || "(empty log)";
    box.scrollTop = box.scrollHeight;
  } catch (err) {
    box.textContent = err.message;
  }
}

async function refreshModels() {
  const aliases = document.getElementById("aliases");
  try {
    const info = await api("/api/v1/model", { timeoutMs: 8000 });
    const pairs = [["Serving now", info.served_version ? `version ${info.served_version}` : "nothing (model server down)"],
      ["@champion", info.aliases.champion || "none"], ["@previous", info.aliases.previous || "none"],
      ["@challenger", info.aliases.challenger || "none"]];
    aliases.replaceChildren(...pairs.flatMap(([k, v]) => [el("dt", {}, k), el("dd", {}, v)]));
    document.getElementById("versions").replaceChildren(...info.versions.map((v) => el("tr", {},
      el("td", {}, `${v.version}${v.aliases.length ? ` (${v.aliases.join(", ")})` : ""}`),
      el("td", {}, v.test_mae || ""), el("td", {}, v.promotion || ""))));
  } catch (err) {
    aliases.replaceChildren(el("dt", {}, "Models"), el("dd", {}, err.message));
  }
}

async function refreshAlerts() {
  try {
    const alerts = await api("/api/v1/alerts", { timeoutMs: 8000 });
    const body = document.getElementById("alerts");
    if (!alerts.length) {
      body.replaceChildren(el("tr", {}, el("td", { colspan: "3" }, "No alerts received.")));
      return;
    }
    body.replaceChildren(...alerts.slice(0, 10).map((a) => el("tr", {},
      el("td", {}, a.name), el("td", {}, el("span", { class: `badge ${a.status}` }, a.status)),
      el("td", {}, [a.summary, a.action].filter(Boolean).join(" ")))));
  } catch (err) {
    document.getElementById("alerts").replaceChildren(el("tr", {}, el("td", { colspan: "3" }, err.message)));
  }
}

function needToken() {
  if (adminToken()) return false;
  setAdminStatus("Enter the admin token first.", "error");
  document.getElementById("token").focus();
  return true;
}

document.getElementById("month-mode").addEventListener("change", (e) => {
  document.getElementById("month").hidden = e.target.value !== "pick";
});

document.getElementById("start-run").addEventListener("click", async (e) => {
  if (needToken()) return;
  const mode = document.getElementById("month-mode").value;
  const month = mode === "pick" ? document.getElementById("month").value : mode;
  if (mode === "pick" && !/^\d{4}-\d{2}$/.test(month)) {
    setAdminStatus("Choose a month first.", "error");
    return;
  }
  e.target.disabled = true;
  try {
    const run = await api("/api/v1/pipeline/run", { method: "POST", token: adminToken(), body: { month: month || null } });
    selectedRun = run.id;
    setAdminStatus(`Pipeline run ${run.id} started.`);
    refreshStatus();
    refreshRuns();
  } catch (err) {
    setAdminStatus(err.message, "error");
  } finally {
    e.target.disabled = false;
  }
});

document.getElementById("rollback").addEventListener("click", async (e) => {
  if (needToken()) return;
  if (!window.confirm("Serve the previous model version again?")) return;
  e.target.disabled = true;
  try {
    const answer = await api("/api/v1/model/rollback", { method: "POST", token: adminToken() });
    setAdminStatus(`Rolled back. Version ${answer.champion} will be serving within a minute.`);
    refreshModels();
  } catch (err) {
    setAdminStatus(err.message, "error");
  } finally {
    e.target.disabled = false;
  }
});

function buildTools() {
  const cfg = window.APP_CONFIG;
  const names = { mlflow: "MLflow (experiments and registry)", grafana: "Grafana (dashboards)",
    prometheus: "Prometheus (metrics and alert rules)", alertmanager: "Alertmanager", ray: "Ray dashboard (during featurize)" };
  document.getElementById("tools").replaceChildren(...Object.entries(cfg.tools).map(([key, url]) =>
    el("li", {}, el("a", { href: url, target: "_blank", rel: "noopener" }, names[key] || key))));
  const base = `${cfg.tools.grafana}/d-solo/${cfg.grafanaDashboard}/${cfg.grafanaDashboard}`;
  document.getElementById("grafana").replaceChildren(...GRAFANA_PANELS.map((p) => el("iframe", {
    src: `${base}?orgId=1&panelId=${p.id}&from=now-6h&to=now&refresh=10s`, title: p.title, loading: "lazy" })));
}

buildTools();
refreshStatus();
refreshRuns();
refreshModels();
refreshAlerts();
setInterval(refreshStatus, 5000);
setInterval(() => { refreshRuns(); refreshModels(); refreshAlerts(); }, 15000);
