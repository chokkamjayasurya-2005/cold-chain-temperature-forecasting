const $ = (selector, root = document) => root.querySelector(selector);
const pageRoot = $("#page-content");
const routeInfo = {
  "/": { title: "Overview", description: "Cold-chain operations at a glance." },
  "/overview": { title: "Overview", description: "Cold-chain operations at a glance." },
  "/prediction": { title: "New Temperature Prediction", description: "Enter the latest available container operating conditions." },
  "/analytics": { title: "Model Analytics", description: "Review held-out model performance and dataset relationships." },
  "/history": { title: "Prediction History", description: "Search, filter, and review locally saved predictions." },
  "/dataset": { title: "Dataset", description: "Explore the data used to train and evaluate the model." },
};

let currentRoute = "/";
let latestPrediction = null;
let followupMessages = [];
let analyticsData = null;
let datasetData = null;
let historyRows = [];
let historyNewestFirst = true;

function format(value, digits = 2) {
  return Number(value).toFixed(digits);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((item) => item.msg).join("; ")
      : data.detail;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return data;
}

function pageHeader(title, description, meta = "") {
  return `<header class="page-header">
    <div><p class="eyebrow">COLD-CHAIN OPERATIONS</p><h1>${title}</h1><p>${description}</p></div>
    ${meta ? `<div class="page-header-meta">${meta}</div>` : ""}
  </header>`;
}

function setPage(route) {
  currentRoute = route;
  document.title = `${routeInfo[route].title} | Cold Chain`;
  document.querySelectorAll(".nav-link").forEach((link) => {
    const active = link.dataset.route === route;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

function navigate(route) {
  if (!routeInfo[route]) return;
  if (location.pathname !== route) history.pushState({}, "", route);
  renderPage(route);
  window.scrollTo(0, 0);
}

function routeFromLocation() {
  const path = location.pathname.replace(/\/+$/, "") || "/";
  if (path === "/overview") return "/";
  return routeInfo[path] ? path : "/";
}

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[data-route]");
  if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigate(link.dataset.route);
});

window.addEventListener("popstate", () => renderPage(routeFromLocation()));

function canvasContext(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const bounds = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, bounds.width * ratio);
  canvas.height = Math.max(1, bounds.height * ratio);
  const context = canvas.getContext("2d");
  context.scale(ratio, ratio);
  return { context, width: bounds.width, height: bounds.height };
}

function drawScatter(canvas, points, xName, yName, equalScale = false) {
  if (!canvas || !points.length) return;
  const { context: ctx, width, height } = canvasContext(canvas);
  const pad = { left: 48, right: 12, top: 15, bottom: 36 };
  const valuesX = points.map((point) => point[xName]);
  const valuesY = points.map((point) => point[yName]);
  const minimum = Math.min(...valuesX, ...valuesY);
  const maximum = Math.max(...valuesX, ...valuesY);
  const low = equalScale ? minimum - 0.2 : Math.min(...valuesX);
  const high = equalScale ? maximum + 0.2 : Math.max(...valuesX);
  const lowY = equalScale ? low : Math.min(...valuesY);
  const highY = equalScale ? high : Math.max(...valuesY);
  ctx.strokeStyle = "#e6edef";
  ctx.fillStyle = "#819198";
  ctx.font = "11px system-ui, sans-serif";
  ctx.beginPath();
  ctx.moveTo(pad.left, pad.top);
  ctx.lineTo(pad.left, height - pad.bottom);
  ctx.lineTo(width - pad.right, height - pad.bottom);
  ctx.stroke();
  if (equalScale) {
    ctx.strokeStyle = "#e9a08c";
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(pad.left, height - pad.bottom - (low - lowY) / (highY - lowY || 1) * (height - pad.top - pad.bottom));
    ctx.lineTo(width - pad.right, height - pad.bottom - (high - lowY) / (highY - lowY || 1) * (height - pad.top - pad.bottom));
    ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.fillStyle = "#298b7b66";
  for (const point of points) {
    const x = pad.left + ((point[xName] - low) / (high - low || 1)) * (width - pad.left - pad.right);
    const y = height - pad.bottom - ((point[yName] - lowY) / (highY - lowY || 1)) * (height - pad.top - pad.bottom);
    ctx.beginPath();
    ctx.arc(x, y, 2, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.fillStyle = "#819198";
  ctx.fillText(equalScale ? "Actual (°C)" : "Ambient temperature (°C)", pad.left, height - 11);
  ctx.fillText(equalScale ? "Predicted (°C)" : "Internal temperature (°C)", Math.max(pad.left, width - 190), height - 11);
}

function drawHistogram(canvas, values, bins = 18) {
  if (!canvas || !values.length) return;
  const low = Math.min(...values);
  const high = Math.max(...values);
  const counts = Array(bins).fill(0);
  const span = high - low || 1;
  values.forEach((value) => counts[Math.min(bins - 1, Math.floor(((value - low) / span) * bins))]++);
  drawBars(canvas, counts, Array.from({ length: bins }, (_, index) => {
    const edge = low + (high - low) * index / bins;
    return format(edge, 1);
  }), format(low, 1), format(high, 1));
}

function drawBars(canvas, values, labels, lowLabel = "", highLabel = "") {
  if (!canvas || !values.length) return;
  const { context: ctx, width, height } = canvasContext(canvas);
  const pad = { left: 38, right: 10, top: 12, bottom: 30 };
  const maxCount = Math.max(...values, 1);
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  ctx.strokeStyle = "#e6edef";
  ctx.beginPath();
  ctx.moveTo(pad.left, pad.top);
  ctx.lineTo(pad.left, height - pad.bottom);
  ctx.lineTo(width - pad.right, height - pad.bottom);
  ctx.stroke();
  const barWidth = plotWidth / values.length;
  values.forEach((count, index) => {
    const barHeight = (count / maxCount) * plotHeight;
    ctx.fillStyle = "#288b7c";
    ctx.fillRect(pad.left + index * barWidth + 1, height - pad.bottom - barHeight, Math.max(1, barWidth - 2), barHeight);
  });
  ctx.fillStyle = "#819198";
  ctx.font = "11px system-ui, sans-serif";
  ctx.fillText(lowLabel || labels[0] || "", pad.left, height - 10);
  ctx.fillText(highLabel || labels[labels.length - 1] || "", width - 40, height - 10);
}

function drawImportance(canvas, features) {
  if (!canvas || !features.length) return;
  const { context: ctx, width, height } = canvasContext(canvas);
  const padLeft = Math.min(205, width * 0.46);
  const rowHeight = (height - 20) / features.length;
  const max = Math.max(...features.map((entry) => Math.max(0, entry.importance_mean)), 0.00001);
  ctx.font = "11px system-ui, sans-serif";
  features.forEach((entry, index) => {
    const y = 12 + index * rowHeight;
    ctx.fillStyle = "#6e8188";
    ctx.fillText(entry.feature.replaceAll("_", " "), 2, y + 11, padLeft - 8);
    const bar = Math.max(0, entry.importance_mean) / max * (width - padLeft - 48);
    ctx.fillStyle = "#288b7c";
    ctx.fillRect(padLeft, y + 2, bar, 13);
    ctx.fillStyle = "#86969b";
    ctx.fillText(format(entry.importance_mean, 3), padLeft + bar + 5, y + 12);
  });
}

function drawCargoDistribution(canvas, distribution) {
  if (!canvas) return;
  const { context: ctx, width, height } = canvasContext(canvas);
  const entries = Object.entries(distribution);
  const left = Math.min(190, width * 0.46);
  const rowHeight = (height - 20) / entries.length;
  const max = Math.max(...entries.map(([, count]) => count), 1);
  ctx.font = "11px system-ui, sans-serif";
  entries.forEach(([label, count], index) => {
    const y = 12 + index * rowHeight;
    ctx.fillStyle = "#6e8188";
    ctx.fillText(label.replaceAll("_", " "), 2, y + 11, left - 8);
    const bar = count / max * (width - left - 50);
    ctx.fillStyle = "#288b7c";
    ctx.fillRect(left, y + 2, bar, 14);
    ctx.fillStyle = "#86969b";
    ctx.fillText(Number(count).toLocaleString(), left + bar + 5, y + 13);
  });
}

function chartPanel(title, subtitle, id) {
  return `<article class="panel chart-card"><div class="panel-heading"><div><h2>${title}</h2><p>${subtitle}</p></div></div><canvas id="${id}" aria-label="${title} chart"></canvas></article>`;
}

function metricCard(title, value, note, color = "green") {
  return `<article class="metric-card"><span class="metric-icon ${color}" aria-hidden="true">●</span><div><p>${title}</p><strong>${value}</strong><small>${note}</small></div></article>`;
}

function renderOverview(summary, info, recent, health) {
  const status = health.model_loaded ? "Model active" : "Model unavailable";
  pageRoot.innerHTML = `${pageHeader("Overview", "Cold-chain operations at a glance.", `<span class="status-pill ${health.model_loaded ? "online" : "offline"}">${status}</span>`)}
    <section class="overview-hero">
      <div><p class="eyebrow">TEMPERATURE INTELLIGENCE</p><h2>Monitor shipment conditions with model-backed estimates.</h2><p>Review historical dataset patterns, predict internal temperature from telemetry, and keep an eye on configurable operational risk indicators.</p><a class="button button-primary" href="/prediction" data-route="/prediction">Start a prediction <span aria-hidden="true">→</span></a></div>
      <div class="hero-summary"><span>Selected model</span><strong>${escapeHtml(info.model)}</strong><span>Held-out test RMSE</span><strong>${format(info.test_metrics.rmse, 3)} °C</strong></div>
    </section>
    <section class="metric-grid" aria-label="Dataset overview">
      ${metricCard("Total containers", Number(summary.rows).toLocaleString(), "records in dataset", "blue")}
      ${metricCard("Average temperature", `${format(summary.average_temperature_c)} °C`, "actual internal temperature", "green")}
      ${metricCard("Average spoilage risk", `${format(summary.average_spoilage_risk_pct, 1)}%`, "dataset-provided estimate", "violet")}
      ${metricCard("High risk containers", Number(summary.high_risk_count).toLocaleString(), "at or above HIGH threshold", "amber")}
      ${metricCard("Critical containers", Number(summary.critical_risk_count).toLocaleString(), "at or above CRITICAL threshold", "red")}
    </section>
    <section class="panel overview-summary"><div class="section-title"><div><p class="eyebrow">TEMPERATURE INTELLIGENCE</p><h2>System summary</h2></div></div>
      <p>The current model is <strong>${escapeHtml(info.model)}</strong>, with held-out test MAE ${format(info.test_metrics.mae, 3)} °C and R² ${format(info.test_metrics.r2, 4)}. The risk figures above are based on the CSV’s configured risk bands and are not independently validated spoilage probabilities.</p>
    </section>
    <section class="panel overview-recent"><div class="section-title"><div><p class="eyebrow">LOCAL SQLITE HISTORY</p><h2>Recent predictions</h2></div><a class="text-button" data-route="/history" href="/history">View full history →</a></div>
      ${recent.length ? `<div class="table-wrap"><table><thead><tr><th>Cargo</th><th>Predicted temp.</th><th>Risk</th><th>Risk level</th><th>Time</th></tr></thead><tbody>${recent.map((row) => `<tr><td>${escapeHtml(row.input_features.cargo_type.replaceAll("_", " "))}</td><td>${format(row.predicted_internal_temperature_c)} °C</td><td>${format(row.estimated_risk_index_pct, 1)} / 100</td><td>${riskBadge(row.risk_level)}</td><td>${formatTimestamp(row.timestamp)}</td></tr>`).join("")}</tbody></table></div>` : '<p class="empty-state">No predictions have been saved yet.</p>'}
    </section>`;
}

function predictionMarkup() {
  return `${pageHeader("New Temperature Prediction", routeInfo["/prediction"].description)}
    <div class="prediction-grid">
      <section class="panel form-panel">
        <div class="panel-heading"><div><h2>Container telemetry</h2><p>Provide the observed operating conditions.</p></div><span class="panel-icon" aria-hidden="true">↗</span></div>
        <form id="prediction-form">
          <label>Cargo type<select id="cargo-type" name="cargo_type" required><option value="" disabled selected>Loading cargo types...</option></select></label>
          <div class="form-row">
            <label>Ambient temperature <span class="unit">°C</span><input name="ambient_temperature_c" type="number" step="0.1" value="29.4" min="-80" max="80" required /></label>
            <label>Internal set point <span class="unit">°C</span><input name="set_point_c" type="number" step="0.1" value="-18" min="-80" max="20" required /></label>
          </div>
          <div class="form-row">
            <label>HVAC power <span class="unit">W</span><input name="hvac_power_watts" type="number" step="1" value="2491" min="0" max="20000" required /></label>
            <label>Seal integrity <span class="unit">0–1</span><input name="seal_integrity" type="number" step="0.001" value="0.959" min="0" max="1" required /></label>
          </div>
          <button class="button button-primary" type="submit" id="predict-button">Predict temperature <span aria-hidden="true">→</span></button>
          <p class="form-footnote">Predictions are model estimates, not observed sensor readings.</p>
        </form>
        <div id="form-error" class="message error-message" role="alert" hidden></div>
      </section>
      <section class="panel result-panel" id="result-panel" aria-live="polite">
        <div class="panel-heading"><div><h2>Prediction result</h2><p id="result-subtitle">Submit telemetry to view an estimate.</p></div><span id="risk-badge" class="risk-badge neutral">AWAITING INPUT</span></div>
        <div class="result-temperature"><strong id="predicted-temp">—</strong><span>°C</span><small>MODEL PREDICTION</small></div>
        <div class="result-stats"><div><span>Set point deviation</span><strong id="deviation">—</strong></div><div><span>Estimated risk index</span><strong id="risk-index">—</strong></div></div>
        <div class="recommendation"><span class="recommendation-icon" aria-hidden="true">✳</span><div><small>LOGISTICS RECOMMENDATION</small><strong id="recommendation">Waiting for a prediction</strong></div></div>
        <div class="explanation"><div class="explanation-title"><span aria-hidden="true">◈</span><strong>Model factors</strong></div><ul id="factor-list"><li>Feature effects appear after prediction.</li></ul></div>
        <p class="disclaimer">Risk is a configurable operational index, not a calibrated spoilage probability. Rules are for demonstration and are not operationally validated.</p>
      </section>
    </div>
    <section class="panel prediction-chat" id="prediction-chat">
      <div class="assistant-intro"><span class="assistant-icon" aria-hidden="true">✧</span><div><h2>Ask a follow-up question</h2><p>Ask about this prediction, its risk level, or the model factors.</p></div></div>
      <div id="chat-messages" class="chat-messages" aria-live="polite"></div>
      <form id="chat-form" class="chat-form">
        <label class="sr-only" for="chat-question">Follow-up question</label>
        <textarea id="chat-question" name="question" rows="3" maxlength="1000" placeholder="Run a prediction to ask a follow-up question" required disabled></textarea>
        <button class="button button-primary" type="submit" id="chat-button" disabled>Ask assistant <span aria-hidden="true">→</span></button>
      </form>
      <p class="chat-footnote" id="chat-footnote">Run a prediction first to enable context-aware follow-up questions.</p>
    </section>`;
}

function displayPrediction(result, preserveConversation = false) {
  if (!preserveConversation) {
    followupMessages = [];
    sessionStorage.removeItem("predictionFollowupMessages");
  }
  latestPrediction = result;
  sessionStorage.setItem("latestPrediction", JSON.stringify(result));
  $("#result-subtitle").textContent = result.cargo_type.replaceAll("_", " ");
  $("#predicted-temp").textContent = format(result.predicted_internal_temperature_c);
  $("#deviation").textContent = `${result.temperature_deviation_c >= 0 ? "+" : ""}${format(result.temperature_deviation_c)} °C`;
  $("#risk-index").textContent = `${format(result.estimated_risk_index_pct, 1)} / 100`;
  $("#recommendation").textContent = result.recommendation;
  const badge = $("#risk-badge");
  badge.textContent = result.risk_level;
  badge.className = `risk-badge ${result.risk_level.toLowerCase()}`;
  $("#factor-list").innerHTML = result.explanation.top_factors.map((factor) =>
    `<li><strong>${escapeHtml(factor.feature.replaceAll("_", " "))}</strong>: ${escapeHtml(factor.detail)}</li>`
  ).join("");
  const chat = $("#prediction-chat");
  if (chat) {
    $("#chat-question").disabled = false;
    $("#chat-question").placeholder = "Why is the risk level high?";
    $("#chat-button").disabled = false;
    $("#chat-footnote").textContent = "The assistant uses the configured Groq API and the current prediction as context.";
    renderFollowupMessages();
  }
}

function renderFollowupMessages() {
  const messages = $("#chat-messages");
  if (!messages) return;
  messages.innerHTML = followupMessages.map((message) =>
    `<article class="chat-message ${message.role}"><strong>${message.role === "user" ? "You" : "Assistant"}</strong><p>${escapeHtml(message.content)}</p></article>`
  ).join("");
}

function resizeFollowupField(input) {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 240)}px`;
  input.style.overflowY = input.scrollHeight > 240 ? "auto" : "hidden";
}

async function submitFollowupQuestion(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const input = $("#chat-question");
  const button = $("#chat-button");
  const question = input.value.trim();
  if (!question || !latestPrediction) return;

  followupMessages.push({ role: "user", content: question });
  renderFollowupMessages();
  input.value = "";
  resizeFollowupField(input);
  input.disabled = true;
  button.disabled = true;
  button.textContent = "Thinking…";
  try {
    const response = await api("/chat", {
      method: "POST",
      body: JSON.stringify({
        question,
        conversation: followupMessages.slice(-7, -1).map((message) => ({
          role: message.role,
          content: message.content.slice(-1500),
        })),
        container_context: latestPrediction,
      }),
    });
    followupMessages.push({ role: "assistant", content: response.answer });
    sessionStorage.setItem("predictionFollowupMessages", JSON.stringify(followupMessages));
    renderFollowupMessages();
  } catch (error) {
    followupMessages.pop();
    renderFollowupMessages();
    const message = document.createElement("p");
    message.className = "chat-error";
    message.setAttribute("role", "alert");
    message.textContent = error.message;
    $("#chat-messages").append(message);
    input.value = question;
  } finally {
    input.disabled = false;
    button.disabled = false;
    button.innerHTML = 'Ask assistant <span aria-hidden="true">→</span>';
    input.focus();
  }
}

async function initializePredictionPage() {
  const form = $("#prediction-form");
  const chatInput = $("#chat-question");
  chatInput.addEventListener("input", () => resizeFollowupField(chatInput));
  chatInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      $("#chat-form").requestSubmit();
    }
  });
  const select = $("#cargo-type");
  try {
    const { cargo_types: cargoTypes } = await api("/options");
    select.replaceChildren(...cargoTypes.map((cargoType) => {
      const option = document.createElement("option");
      option.value = cargoType;
      option.textContent = cargoType.replaceAll("_", " ");
      return option;
    }));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = $("#predict-button");
      const error = $("#form-error");
      error.hidden = true;
      button.disabled = true;
      button.textContent = "Calculating…";
      const payload = Object.fromEntries(new FormData(form).entries());
      for (const key of ["ambient_temperature_c", "set_point_c", "hvac_power_watts", "seal_integrity"]) payload[key] = Number(payload[key]);
      try {
        displayPrediction(await api("/predict", { method: "POST", body: JSON.stringify(payload) }));
      } catch (requestError) {
        error.textContent = requestError.message;
        error.hidden = false;
      } finally {
        button.disabled = false;
        button.innerHTML = 'Predict temperature <span aria-hidden="true">→</span>';
      }
    });
    $("#chat-form").addEventListener("submit", submitFollowupQuestion);
  } catch (error) {
    $("#form-error").textContent = `Cargo options could not load: ${error.message}`;
    $("#form-error").hidden = false;
  }
  try {
    const saved = sessionStorage.getItem("latestPrediction");
    if (saved) displayPrediction(JSON.parse(saved), true);
    const savedMessages = sessionStorage.getItem("predictionFollowupMessages");
    if (savedMessages) {
      followupMessages = JSON.parse(savedMessages);
      renderFollowupMessages();
    }
  } catch (error) {
    sessionStorage.removeItem("latestPrediction");
    console.error("Could not restore the latest prediction:", error);
  }
}

function renderComparisonTable(results) {
  return `<div class="table-wrap"><table><thead><tr><th>Model</th><th>MAE (°C)</th><th>RMSE (°C)</th><th>R²</th></tr></thead><tbody>${results.map((row) => `<tr><td>${escapeHtml(row.model)}</td><td>${format(row.mae, 4)}</td><td>${format(row.rmse, 4)}</td><td>${format(row.r2, 5)}</td></tr>`).join("")}</tbody></table></div>`;
}

function renderAnalytics(payload, summary) {
  analyticsData = { payload, summary };
  const info = payload.model_info;
  const comparison = payload.comparison.validation_results;
  pageRoot.innerHTML = `${pageHeader("Model Analytics", routeInfo["/analytics"].description, `<span class="status-pill online">${escapeHtml(info.model)}</span>`)}
    <section class="metric-grid analytics-metrics">
      ${metricCard("Test MAE", `${format(info.test_metrics.mae, 3)} °C`, "held-out test set", "blue")}
      ${metricCard("Test RMSE", `${format(info.test_metrics.rmse, 3)} °C`, "held-out test set", "green")}
      ${metricCard("Test R²", format(info.test_metrics.r2, 5), "held-out test set", "violet")}
      ${metricCard("Best model", escapeHtml(info.model), "selected on validation RMSE", "amber")}
    </section>
    <section class="panel analytics-comparison"><div class="section-title"><div><p class="eyebrow">MODEL PERFORMANCE</p><h2>Validation comparison</h2></div></div>${renderComparisonTable(comparison)}</section>
    <section class="chart-grid analytics-charts">
      ${chartPanel("Actual vs. predicted", "Held-out test observations", "actual-chart")}
      ${chartPanel("Prediction error distribution", "Actual minus predicted (°C)", "residual-chart")}
      ${chartPanel("Feature importance", "Permutation impact on RMSE", "importance-chart")}
      ${chartPanel("Temperature distribution", "Actual internal temperatures in the dataset", "temperature-chart")}
      ${chartPanel("Feature relationship", "Ambient temperature vs. actual internal temperature", "relationship-chart")}
    </section>
    <p class="notice"><strong>Interpretation note</strong><span>The evaluation data is held out from model selection. Permutation importance and feature relationships are descriptive, not causal. Dataset risk estimates are not validated spoilage probabilities.</span></p>`;
  redrawAnalytics();
}

function redrawAnalytics() {
  if (!analyticsData) return;
  const { payload, summary } = analyticsData;
  drawScatter($("#actual-chart"), payload.evaluation_samples, "actual_temperature_c", "predicted_temperature_c", true);
  drawHistogram($("#residual-chart"), payload.evaluation_samples.map((item) => item.residual_c));
  drawImportance($("#importance-chart"), payload.model_info.feature_importance);
  const temperature = summary.histograms.temperature;
  drawBars($("#temperature-chart"), temperature.counts, temperature.edges.map((value) => format(value, 1)), format(temperature.edges[0], 1), format(temperature.edges.at(-1), 1));
  drawScatter($("#relationship-chart"), summary.feature_relationship_sample, "ambient_temperature_c", "actual_temperature_c");
}

function riskBadge(level) {
  return `<span class="table-risk ${String(level).toLowerCase()}">${escapeHtml(level)}</span>`;
}

function formatTimestamp(timestamp) {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? escapeHtml(timestamp) : escapeHtml(date.toLocaleString());
}

function renderHistoryRows(rows) {
  const tbody = $("#history-rows");
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty-state">No predictions match these filters.</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map((row) => {
    const inputs = row.input_features;
    const deviation = row.predicted_internal_temperature_c - inputs.set_point_c;
    return `<tr>
      <td>${formatTimestamp(row.timestamp)}</td>
      <td>${escapeHtml(inputs.cargo_type.replaceAll("_", " "))}</td>
      <td>${format(inputs.ambient_temperature_c)} °C</td>
      <td>${format(inputs.set_point_c)} °C</td>
      <td>${format(inputs.hvac_power_watts, 0)} W</td>
      <td>${format(inputs.seal_integrity, 3)}</td>
      <td>${format(row.predicted_internal_temperature_c)} °C</td>
      <td>${deviation >= 0 ? "+" : ""}${format(deviation)} °C</td>
      <td>${riskBadge(row.risk_level)}</td>
      <td>${escapeHtml(row.recommendation)}</td>
    </tr>`;
  }).join("");
}

function filterHistory() {
  const query = $("#history-search").value.trim().toLowerCase();
  const risk = $("#risk-filter").value;
  const rows = historyRows.filter((row) => {
    const values = [
      ...Object.values(row.input_features),
      row.recommendation,
      row.risk_level,
      row.timestamp,
    ].join(" ").toLowerCase();
    return (!query || values.includes(query)) && (risk === "ALL" || row.risk_level === risk);
  }).sort((left, right) => {
    const order = new Date(left.timestamp) - new Date(right.timestamp);
    return historyNewestFirst ? -order : order;
  });
  renderHistoryRows(rows);
  $("#history-count").textContent = `${rows.length} of ${historyRows.length} predictions`;
}

function historyMarkup() {
  return `${pageHeader("Prediction History", routeInfo["/history"].description)}
    <section class="panel history-panel">
      <div class="history-controls">
        <label class="search-label">Search predictions<input id="history-search" type="search" placeholder="Cargo type, risk, or recommendation" /></label>
        <label>Risk level<select id="risk-filter"><option value="ALL">All risk levels</option><option>LOW</option><option>MEDIUM</option><option>HIGH</option><option>CRITICAL</option></select></label>
        <button class="button button-secondary" id="sort-history" type="button">Newest first ↓</button>
        <button class="button button-danger" id="clear-history" type="button">Clear history</button>
      </div>
      <p id="history-count" class="table-summary">Loading saved predictions...</p>
      <div class="table-wrap"><table><thead><tr><th>Timestamp</th><th>Cargo type</th><th>Ambient temp.</th><th>Set point</th><th>HVAC power</th><th>Seal integrity</th><th>Predicted temp.</th><th>Deviation</th><th>Risk</th><th>Recommendation</th></tr></thead><tbody id="history-rows"><tr><td colspan="10" class="empty-state">Loading history...</td></tr></tbody></table></div>
    </section>`;
}

function bindHistoryControls() {
  $("#history-search").addEventListener("input", filterHistory);
  $("#risk-filter").addEventListener("change", filterHistory);
  $("#sort-history").addEventListener("click", (event) => {
    historyNewestFirst = !historyNewestFirst;
    event.currentTarget.textContent = historyNewestFirst ? "Newest first ↓" : "Oldest first ↑";
    filterHistory();
  });
  $("#clear-history").addEventListener("click", async (event) => {
    if (!historyRows.length || !window.confirm("Permanently clear all locally saved predictions?")) return;
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await api("/history", { method: "DELETE" });
      historyRows = [];
      filterHistory();
    } catch (error) {
      $("#history-count").textContent = `History could not be cleared: ${error.message}`;
    } finally {
      button.disabled = false;
    }
  });
}

function renderDataset(summary) {
  datasetData = summary;
  pageRoot.innerHTML = `${pageHeader("Dataset", routeInfo["/dataset"].description)}
    <section class="metric-grid dataset-metrics">
      ${metricCard("Records", Number(summary.rows).toLocaleString(), "rows", "blue")}
      ${metricCard("Columns", summary.column_count, "features and outcomes", "green")}
      ${metricCard("Missing values", summary.columns.reduce((total, column) => total + column.missing_values, 0).toLocaleString(), "across all columns", "violet")}
      ${metricCard("Duplicate rows", Number(summary.duplicate_rows).toLocaleString(), "retained data checks", "amber")}
    </section>
    <section class="panel dataset-section"><div class="section-title"><div><p class="eyebrow">DATA QUALITY</p><h2>Column information</h2></div></div>
      <div class="table-wrap"><table><thead><tr><th>Column</th><th>Data type</th><th>Missing values</th><th>Unique values</th></tr></thead><tbody>${summary.columns.map((column) => `<tr><td>${escapeHtml(column.name)}</td><td>${escapeHtml(column.data_type)}</td><td>${column.missing_values.toLocaleString()}</td><td>${column.unique_values.toLocaleString()}</td></tr>`).join("")}</tbody></table></div>
    </section>
    <section class="panel dataset-section"><div class="section-title"><div><p class="eyebrow">NUMERIC FIELDS</p><h2>Summary statistics</h2></div></div>
      <div class="table-wrap"><table><thead><tr><th>Column</th><th>Count</th><th>Mean</th><th>Std. dev.</th><th>Min</th><th>25%</th><th>Median</th><th>75%</th><th>Max</th></tr></thead><tbody>${summary.numeric_summary.map((row) => `<tr><td>${escapeHtml(row.column)}</td><td>${Number(row.count).toLocaleString()}</td>${["mean", "std", "min", "25%", "50%", "75%", "max"].map((key) => `<td>${format(row[key], 2)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
    </section>
    <section class="chart-grid dataset-charts">
      ${chartPanel("Cargo type distribution", "Dataset records by cargo category", "cargo-chart")}
      ${chartPanel("Ambient temperature", "External temperature distribution", "ambient-chart")}
      ${chartPanel("Actual temperature", "Internal temperature distribution", "dataset-temperature-chart")}
      ${chartPanel("Spoilage risk estimate", "Dataset-provided estimate distribution", "spoilage-chart")}
    </section>
    <section class="panel dataset-section"><div class="section-title"><div><p class="eyebrow">SAMPLE</p><h2>First records</h2></div></div>
      <div class="table-wrap"><table><thead><tr>${Object.keys(summary.sample_records[0] || {}).map((key) => `<th>${escapeHtml(key.replaceAll("_", " "))}</th>`).join("")}</tr></thead><tbody>${summary.sample_records.map((record) => `<tr>${Object.keys(summary.sample_records[0] || {}).map((key) => `<td>${escapeHtml(record[key])}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
    </section>
    <p class="notice"><strong>Data note</strong><span>Container identifiers are excluded from sample records. Dataset risk estimates are descriptive and not verified spoilage outcomes.</span></p>`;
  drawCargoDistribution($("#cargo-chart"), summary.cargo_distribution);
  drawBars($("#ambient-chart"), summary.histograms.ambient_temperature.counts, summary.histograms.ambient_temperature.edges.map((value) => format(value, 1)), format(summary.histograms.ambient_temperature.edges[0], 1), format(summary.histograms.ambient_temperature.edges.at(-1), 1));
  drawBars($("#dataset-temperature-chart"), summary.histograms.temperature.counts, summary.histograms.temperature.edges.map((value) => format(value, 1)), format(summary.histograms.temperature.edges[0], 1), format(summary.histograms.temperature.edges.at(-1), 1));
  drawBars($("#spoilage-chart"), summary.histograms.spoilage_risk.counts, summary.histograms.spoilage_risk.edges.map((value) => format(value, 1)), format(summary.histograms.spoilage_risk.edges[0], 1), format(summary.histograms.spoilage_risk.edges.at(-1), 1));
}

async function renderOverviewData() {
  const [summary, metrics, rows, health] = await Promise.all([
    api("/dataset-summary"),
    api("/metrics"),
    api("/history?limit=5"),
    api("/health"),
  ]);
  if (currentRoute !== "/" && currentRoute !== "/overview") return;
  renderOverview(summary, metrics.model_info, rows, health);
}

async function renderPage(route) {
  if (!routeInfo[route]) route = "/";
  setPage(route);
  pageRoot.innerHTML = `${pageHeader(routeInfo[route].title, "Loading page data…")}<div class="loading-state">Loading…</div>`;
  analyticsData = null;
  try {
    if (route === "/" || route === "/overview") {
      await renderOverviewData();
    } else if (route === "/prediction") {
      pageRoot.innerHTML = predictionMarkup();
      await initializePredictionPage();
    } else if (route === "/analytics") {
      const [metrics, summary] = await Promise.all([api("/metrics"), api("/dataset-summary")]);
      if (currentRoute === route) renderAnalytics(metrics, summary);
    } else if (route === "/history") {
      pageRoot.innerHTML = historyMarkup();
      bindHistoryControls();
      historyRows = await api("/history?limit=500");
      if (currentRoute === route) filterHistory();
    } else if (route === "/dataset") {
      const summary = await api("/dataset-summary");
      if (currentRoute === route) renderDataset(summary);
    }
  } catch (error) {
    if (currentRoute !== route) return;
    pageRoot.innerHTML = `${pageHeader(routeInfo[route].title, routeInfo[route].description)}<div class="page-error" role="alert">Page data could not be loaded: ${escapeHtml(error.message)}</div>`;
  }
}

window.addEventListener("resize", () => {
  if (analyticsData) redrawAnalytics();
  if (datasetData && currentRoute === "/dataset") {
    drawCargoDistribution($("#cargo-chart"), datasetData.cargo_distribution);
    for (const [id, key] of [
      ["ambient-chart", "ambient_temperature"],
      ["dataset-temperature-chart", "temperature"],
      ["spoilage-chart", "spoilage_risk"],
    ]) {
      const histogram = datasetData.histograms[key];
      drawBars($( `#${id}`), histogram.counts, histogram.edges.map((value) => format(value, 1)), format(histogram.edges[0], 1), format(histogram.edges.at(-1), 1));
    }
  }
});

renderPage(routeFromLocation());
