// Analyse page: send a game to the backend and show each player's estimate, range and reasons.

const SCALE = { min: 400, max: 3000 };

// Plain-language names for the model inputs, and how to show their values.
const pct = (v) => `${Math.round(v * 100)}%`;
const cp = (v) => `${Math.round(v)} centipawns`;
const LABELS = {
  acpl: ["Average loss per move", cp],
  acpl_opening: ["Loss per move in the opening", cp],
  acpl_middlegame: ["Loss per move in the middlegame", cp],
  acpl_endgame: ["Loss per move in the endgame", cp],
  blunder_rate: ["Moves that were blunders", pct],
  mistake_rate: ["Moves that were mistakes", pct],
  inaccuracy_rate: ["Moves that were inaccuracies", pct],
  best_move_rate: ["Moves matching Stockfish's first choice", pct],
  time_used_frac: ["Share of the clock used", pct],
  mean_move_time: ["Average time per move", (v) => `${v.toFixed(1)} seconds`],
  move_time_cv: ["How uneven the move times were", (v) => v.toFixed(2)],
  low_clock_moves: ["Moves made with under 10 seconds left", (v) => String(Math.round(v))],
  lost_on_time: ["Lost on time", (v) => (v ? "yes" : "no")],
  color: ["Colour", (v) => (v ? "white" : "black")],
  plies: ["Game length", (v) => `${Math.round(v / 2)} moves`],
  result: ["Result for this player", (v) => ({ 1: "win", 0.5: "draw", 0: "loss" }[v] || "unknown")],
  eco_group: ["Opening family", (v) => (v >= 0 ? "ABCDE"[v] : "unknown")],
};

const form = document.getElementById("game-form");
const gameInput = document.getElementById("game");
const submitButton = document.getElementById("submit");
const statusBox = document.getElementById("status");
const resultBox = document.getElementById("result");

function setStatus(text, kind = "") {
  statusBox.textContent = text;
  statusBox.className = `status ${kind}`;
}

function looksLikePgn(text) {
  return /^\s*\[/.test(text) || /\b1\.\s*[a-hKQRBNO]/.test(text);
}

async function loadExamples() {
  try {
    const games = await api("/api/v1/examples?limit=4", { timeoutMs: 8000 });
    if (!games.length) return;
    const list = document.getElementById("example-list");
    games.forEach((game, i) => {
      list.append(el("li", {}, el("button", {
        type: "button",
        class: "quiet",
        onclick: () => {
          gameInput.value = game.pgn;
          setStatus(`Example ${i + 1} loaded. Press Estimate ratings.`);
        },
      }, `Example ${i + 1} (${game.time_control})`)));
    });
    document.getElementById("examples").hidden = false;
  } catch (err) {
    // Examples are a convenience; the page works without them.
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = gameInput.value.trim();
  if (!text) {
    setStatus("Paste a Lichess game link or a PGN first.", "error");
    gameInput.focus();
    return;
  }
  const body = looksLikePgn(text) ? { pgn: text } : { url: text };
  submitButton.disabled = true;
  setStatus("Stockfish is checking every move. This takes a few seconds.", "working");
  try {
    renderResult(await api("/api/v1/predict", { method: "POST", body }));
    setStatus("");
  } catch (err) {
    setStatus(err.message, "error");
  } finally {
    submitButton.disabled = false;
  }
});

function position(value) {
  const share = (value - SCALE.min) / (SCALE.max - SCALE.min);
  return Math.min(100, Math.max(0, share * 100));
}

function reasonItem(reason) {
  const [label, format] = LABELS[reason.feature] || [reason.feature, String];
  const value = reason.value === null ? "not reached" : format(reason.value);
  const size = Math.abs(Math.round(reason.effect));
  const direction = reason.effect >= 0
    ? el("span", { class: "up" }, `raised the estimate by ${size}`)
    : el("span", { class: "down" }, `lowered the estimate by ${size}`);
  return el("li", {}, `${label}: ${value}. This `, direction, ".");
}

function renderResult(result) {
  resultBox.replaceChildren();
  for (const player of result.players) {
    const side = player.color === "white" ? "White" : "Black";
    const name = player.name && player.name !== "?" ? ` (${player.name})` : "";
    // The bar: the range as a band on a 400-3000 scale, with a marker at the estimate.
    const bar = el("div", { class: "bar", "aria-hidden": "true" },
      el("div", { class: "band", style: `left:${position(player.low)}%;width:${position(player.high) - position(player.low)}%` }),
      el("div", { class: "marker", style: `left:${position(player.mid)}%` }));
    const truth = el("p", { class: "truth" });
    if (player.actual !== null) {
      const inside = player.low <= player.actual && player.actual <= player.high;
      truth.append(el("button", {
        type: "button",
        class: "quiet",
        onclick: () => {
          truth.textContent = `Real rating ${player.actual}: the estimate was off by ${Math.abs(player.mid - player.actual)}`
            + ` and the real rating is ${inside ? "inside" : "outside"} the range.`;
        },
      }, "Show real rating"));
    } else {
      truth.textContent = "This PGN has no rating to compare with.";
    }
    resultBox.append(el("article", { class: "player" },
      el("h2", {}, `${side}${name}`),
      el("p", { class: "estimate" }, player.mid),
      el("p", {}, `Most likely between ${player.low} and ${player.high} (the real rating is in this range about 8 times in 10).`),
      bar,
      el("div", { class: "scale", "aria-hidden": "true" }, el("span", {}, SCALE.min), el("span", {}, SCALE.max)),
      el("h3", {}, "Why this estimate"),
      el("ul", {}, player.reasons.map(reasonItem)),
      truth));
  }
  resultBox.append(el("p", { class: "hint" },
    `Model version ${result.model_version}. Stockfish analysis took ${(result.analysis_ms / 1000).toFixed(1)} s.`));
}

loadExamples();
