const $ = (selector, root = document) => root.querySelector(selector);
const pageRoot = $("#page-content");
const routeInfo = {
  "/": { title: "Overview", description: "Cold-chain operations at a glance." },
  "/overview": { title: "Overview", description: "Cold-chain operations at a glance." },
  "/prediction": { title: "New Temperature Prediction", description: "Enter the latest available container operating conditions." },
  "/analytics": { title: "Model Analytics", description: "Review held-out model performance and dataset relationships." },
  "/history": { title: "Prediction History", description: "Search, filter, and review locally saved predictions." },
  "/dataset": { title: "Dataset", description: "Explore the data used to train and evaluate the model." },
  "/users": { title: "User Management", description: "Create accounts and manage access to the application." },
  "/activity": { title: "Activity Log", description: "Review and export administrator-visible account and system events." },
  "/account": { title: "My Account", description: "Update your profile and password." },
  "/admin/users": { title: "User Management", description: "Create accounts and manage access to the application." },
  "/admin/user-details": { title: "User Details", description: "Review and update an account." },
};

let currentRoute = "/";
let latestPrediction = null;
let followupMessages = [];
let analyticsData = null;
let datasetData = null;
let historyRows = [];
let historyNewestFirst = true;
let currentUser = null;
let csrfToken = null;
let authRouteCurrent = "/login";
let userSearchTimer = null;
let userSearchSequence = 0;
let userSearchTerm = "";

function format(value, digits = 2) {
  if (value === null || value === undefined || value === "") return "—";
  return Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "—";
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (["POST", "PATCH", "DELETE"].includes(method) && csrfToken && !headers["X-CSRF-Token"]) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  const response = await fetch(path, { ...options, method, headers, credentials: "same-origin" });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = Array.isArray(data?.detail)
      ? data.detail.map((item) => item.msg).join("; ")
      : data?.detail;
    const error = new Error(detail || `Request failed (${response.status})`);
    error.status = response.status;
    if (response.status === 401 && currentUser) showLogin();
    throw error;
  }
  return data;
}

function showLogin(message = "") {
  currentUser = null;
  csrfToken = null;
  document.querySelector("#app-shell").hidden = true;
  const path = location.pathname;
  const route = ["/signup", "/forgot-password", "/reset-password"].includes(path) ? path : "/login";
  if (route === "/login" && path !== "/login") history.replaceState({}, "", "/login");
  renderAuthPage(route, message);
  api("/auth/csrf").then((token) => {
    csrfToken = token.csrf_token;
    bindAuthForm(authRouteCurrent);
  }).catch((error) => {
    const errorBox = $("#auth-error");
    if (errorBox) {
      errorBox.textContent = `Unable to prepare secure authentication: ${error.message}`;
      errorBox.hidden = false;
    }
  });
}

function authPageLink(path, text) {
  return `<a href="${path}" data-auth-route="${path}">${text}</a>`;
}

function renderAuthPage(route, message = "", notice = "") {
  authRouteCurrent = route;
  const titles = {
    "/login": ["Sign in", "Welcome back. Sign in to your Cold Chain workspace."],
    "/signup": ["Create your account", "Register for a personal cold-chain workspace."],
    "/forgot-password": ["Reset your password", "We’ll send a secure reset link if email delivery is available."],
    "/reset-password": ["Choose a new password", "Reset links expire after 30 minutes and can only be used once."],
  };
  const screen = $("#login-screen");
  screen.hidden = false;
  const [title, description] = titles[route] || titles["/login"];
  let form;
  if (route === "/signup") {
    form = `<form id="auth-form">
      <label>Full name<input name="full_name" autocomplete="name" required maxlength="120" /></label>
      <label>Username<input name="username" autocomplete="username" required minlength="3" maxlength="32" pattern="[A-Za-z0-9_.\\x2d]+" /></label>
      <label>Email address<input name="email" type="email" autocomplete="email" required maxlength="254" /></label>
      <label>Password<span class="password-input-wrap"><input name="password" type="password" autocomplete="new-password" required minlength="6" maxlength="128" /><button class="password-toggle" type="button" aria-label="Show password" aria-pressed="false">Show</button></span></label>
      <label>Confirm password<input name="confirm_password" type="password" autocomplete="new-password" required minlength="6" maxlength="128" /></label>
      <button class="button button-primary" type="submit">Create account</button>
      <p class="auth-switch">Already registered? ${authPageLink("/login", "Sign in")}</p>
    </form>`;
  } else if (route === "/forgot-password") {
    form = `<form id="auth-form">
      <label>Email address<input name="email" type="email" autocomplete="email" required maxlength="254" /></label>
      <button class="button button-primary" type="submit">Send reset link</button>
      <p class="auth-switch">${authPageLink("/login", "Return to sign in")}</p>
    </form>`;
  } else if (route === "/reset-password") {
    const resetToken = new URLSearchParams(location.hash.slice(1)).get("token");
    form = resetToken
      ? `<form id="auth-form">
        <label>New password<span class="password-input-wrap"><input name="password" type="password" autocomplete="new-password" required minlength="6" maxlength="128" /><button class="password-toggle" type="button" aria-label="Show password" aria-pressed="false">Show</button></span></label>
        <label>Confirm new password<input name="confirm_password" type="password" autocomplete="new-password" required minlength="6" maxlength="128" /></label>
        <button class="button button-primary" type="submit">Reset password</button>
        <p class="auth-switch">${authPageLink("/login", "Return to sign in")}</p>
      </form>`
      : `<p class="message error-message" role="alert">This reset link is missing or invalid. ${authPageLink("/forgot-password", "Request a new link")}</p>`;
  } else {
    form = `<form id="auth-form">
      <label>Username or email<input name="username" autocomplete="username" required maxlength="254" /></label>
      <label>Password<span class="password-input-wrap"><input name="password" type="password" autocomplete="current-password" required maxlength="256" /><button class="password-toggle" type="button" aria-label="Show password" aria-pressed="false">Show</button></span></label>
      <label class="remember-control"><input name="remember_me" type="checkbox" /> Remember me <span>Keep this device signed in for 30 days</span></label>
      <div class="auth-actions">${authPageLink("/forgot-password", "Forgot password?")}</div>
      <button class="button button-primary" type="submit">Sign in</button>
      <p class="auth-switch">New to Cold Chain? ${authPageLink("/signup", "Create an account")}</p>
    </form>`;
  }
  screen.innerHTML = `<section class="login-card">
    <a class="brand login-brand" href="/" aria-label="Cold Chain home"><span class="brand-mark">CC</span><span><strong>Cold Chain</strong><small>Temperature intelligence</small></span></a>
    <p class="eyebrow">SECURE WORKSPACE</p>
    <h1>${title}</h1>
    <p class="login-description">${description}</p>
    ${notice ? `<p class="message success-message" role="status">${escapeHtml(notice)}</p>` : ""}
    ${form}
    <p class="message error-message" id="auth-error" role="alert" ${message ? "" : "hidden"}>${escapeHtml(message)}</p>
  </section>`;
  document.title = `${title} | Cold Chain`;
  screen.querySelector('button[type="submit"]').disabled = true;
}

function bindAuthForm(route) {
  const form = $("#auth-form");
  if (!form) return;
  const submitButton = form.querySelector('button[type="submit"]');
  submitButton.disabled = !csrfToken;
  form.querySelectorAll(".password-toggle").forEach((toggle) => {
    toggle.addEventListener("click", () => {
      const input = toggle.closest(".password-input-wrap").querySelector("input");
      const visible = input.type === "password";
      input.type = visible ? "text" : "password";
      toggle.textContent = visible ? "Hide" : "Show";
      toggle.setAttribute("aria-label", visible ? "Hide password" : "Show password");
      toggle.setAttribute("aria-pressed", String(visible));
    });
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const errorBox = $("#auth-error");
    const values = new FormData(form);
    errorBox.hidden = true;
    button.disabled = true;
    const label = button.textContent;
    button.textContent = "Please wait…";
    try {
      if (route === "/signup") {
        if (values.get("password") !== values.get("confirm_password")) {
          throw new Error("Passwords do not match.");
        }
        const result = await api("/auth/register", {
          method: "POST",
          body: JSON.stringify({
            full_name: values.get("full_name"),
            username: values.get("username"),
            email: values.get("email"),
            password: values.get("password"),
          }),
        });
        history.replaceState({}, "", "/login");
        renderAuthPage("/login", "", result.message);
        bindAuthForm("/login");
      } else if (route === "/forgot-password") {
        const result = await api("/auth/password-reset/request", {
          method: "POST",
          body: JSON.stringify({ email: values.get("email") }),
        });
        renderAuthPage(route, "", result.message);
        bindAuthForm(route);
      } else if (route === "/reset-password") {
        if (values.get("password") !== values.get("confirm_password")) {
          throw new Error("Passwords do not match.");
        }
        const token = new URLSearchParams(location.hash.slice(1)).get("token");
        if (!token) throw new Error("This reset link is invalid or expired.");
        history.replaceState({}, "", location.pathname);
        const result = await api("/auth/password-reset/complete", {
          method: "POST",
          body: JSON.stringify({ token, new_password: values.get("password") }),
        });
        history.replaceState({}, "", "/login");
        renderAuthPage("/login", "", result.message);
        bindAuthForm("/login");
      } else {
        const result = await api("/auth/login", {
          method: "POST",
          body: JSON.stringify({
            username: values.get("username"),
            password: values.get("password"),
            remember_me: values.has("remember_me"),
          }),
        });
        form.reset();
        currentUser = result.user;
        csrfToken = result.csrf_token;
        enterWorkspace();
      }
    } catch (error) {
      errorBox.textContent = error.message;
      errorBox.hidden = false;
    } finally {
      if (button.isConnected) {
        button.disabled = false;
        button.textContent = label;
      }
    }
  });
}

function enterWorkspace() {
  $("#login-screen").hidden = true;
  $("#app-shell").hidden = false;
  $("#app-shell").classList.toggle("password-required", currentUser.must_change_password);
  $("#account-bar").hidden = false;
  $("#account-bar").innerHTML = `<span><strong>${escapeHtml(currentUser.username)}</strong><small>${escapeHtml(currentUser.role)}</small></span>
    <button class="button button-secondary" id="logout-button" type="button">Sign out</button>`;
  $("#logout-button").addEventListener("click", async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } catch (error) {
      if (error.status !== 401) throw error;
    } finally {
      currentUser = null;
      csrfToken = null;
      history.replaceState({}, "", "/");
      showLogin();
    }
  });
  document.querySelectorAll(".admin-nav").forEach((item) => {
    item.hidden = currentUser.role !== "ADMIN";
  });
  $("#local-monitoring-note").hidden = currentUser.role === "USER";
  $("#genai-status-note").hidden = currentUser.role === "USER";
  if (["/login", "/signup", "/forgot-password", "/reset-password"].includes(location.pathname)) {
    history.replaceState({}, "", "/");
  }
  if (currentUser.must_change_password) {
    renderAccountPage(true);
    return;
  }
  renderPage(routeFromLocation());
}

async function initializeApp() {
  try {
    const token = await api("/auth/csrf");
    csrfToken = token.csrf_token;
    const result = await api("/auth/me");
    currentUser = result.user;
    enterWorkspace();
  } catch (error) {
    if (error.status === 401) showLogin();
    else showLogin(`Unable to connect: ${error.message}`);
  }
}

function pageHeader(title, description, meta = "") {
  return `<header class="page-header">
    <div><p class="eyebrow">COLD-CHAIN OPERATIONS</p><h1>${title}</h1><p>${description}</p></div>
    ${meta ? `<div class="page-header-meta">${meta}</div>` : ""}
  </header>`;
}

function setPage(route) {
  currentRoute = route;
  const title = routeInfo[route]?.title || "User Details";
  document.title = `${title} | Cold Chain`;
  document.querySelectorAll(".nav-link").forEach((link) => {
    const active = link.dataset.route === route;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

function navigate(route) {
  const isUserDetails = /^\/user-details\/\d+$/.test(route);
  if (!routeInfo[route] && !isUserDetails) return;
  if (!currentUser) return;
  if (currentUser.role !== "ADMIN" && (["/analytics", "/dataset", "/users", "/activity", "/admin/users"].includes(route) || isUserDetails)) {
    route = "/";
  }
  if (location.pathname !== route) history.pushState({}, "", route);
  renderPage(route);
  window.scrollTo(0, 0);
}

function routeFromLocation() {
  const path = location.pathname.replace(/\/+$/, "") || "/";
  if (path === "/overview") return "/";
  if (/^\/user-details\/\d+$/.test(path)) return path;
  return routeInfo[path] ? path : "/";
}

document.addEventListener("click", (event) => {
  const authLink = event.target.closest("a[data-auth-route]");
  if (authLink && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
    event.preventDefault();
    const route = authLink.dataset.authRoute;
    history.pushState({}, "", route);
    renderAuthPage(route);
    bindAuthForm(route);
    return;
  }
  const link = event.target.closest("a[data-route]");
  if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  navigate(link.dataset.route);
});

window.addEventListener("popstate", () => {
  const path = location.pathname;
  if (!currentUser && ["/login", "/signup", "/forgot-password", "/reset-password"].includes(path)) {
    renderAuthPage(path);
    bindAuthForm(path);
  } else {
    renderPage(routeFromLocation());
  }
});

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

function renderAdminOverview(dashboard, health) {
  const info = dashboard.model_info;
  const users = dashboard.user_counts;
  const recent = dashboard.recent_predictions;
  pageRoot.innerHTML = `${pageHeader("Administrator Overview", "System-wide prediction activity and account health.", `<span class="status-pill ${health.model_loaded ? "online" : "offline"}">${health.model_loaded ? "Model active" : "Model unavailable"}</span>`)}
    <section class="overview-hero">
      <div><p class="eyebrow">ADMINISTRATOR DASHBOARD</p><h2>Monitor system activity and account access.</h2><p>Review prediction volume, risk indicators, user activity, and the current model status.</p><a class="button button-primary" href="/users" data-route="/users">Manage accounts <span aria-hidden="true">→</span></a></div>
      <div class="hero-summary"><span>Selected model</span><strong>${escapeHtml(info.model)}</strong><span>Held-out test RMSE</span><strong>${format(info.test_metrics.rmse, 3)} °C</strong></div>
    </section>
    <section class="metric-grid">
      ${metricCard("Total predictions", Number(dashboard.total_predictions).toLocaleString(), "all accounts", "blue")}
      ${metricCard("Created today", Number(dashboard.predictions_today).toLocaleString(), "UTC", "green")}
      ${metricCard("Active accounts", Number(users.active).toLocaleString(), `${users.total} total accounts`, "violet")}
      ${metricCard("Critical risk", Number(dashboard.critical_risk_predictions).toLocaleString(), "saved predictions", "amber")}
    </section>
    <section class="panel overview-summary"><div class="section-title"><div><p class="eyebrow">SYSTEM STATUS</p><h2>Operational summary</h2></div></div>
      <p>Average predicted temperature: <strong>${format(dashboard.average_predicted_temperature_c)} °C</strong>. Average operational risk index: <strong>${format(dashboard.average_risk_index_pct, 1)} / 100</strong>. These risk values are configurable indicators, not independently validated spoilage probabilities.</p>
    </section>
    <section class="panel overview-recent"><div class="section-title"><div><p class="eyebrow">ALL ACCOUNTS</p><h2>Recent predictions</h2></div><a class="text-button" data-route="/history" href="/history">View full history →</a></div>
      ${recent.length ? `<div class="table-wrap"><table><thead><tr><th>Cargo</th><th>Predicted temp.</th><th>Risk</th><th>Risk level</th><th>Time</th></tr></thead><tbody>${recent.map((row) => `<tr><td>${escapeHtml(row.input_features.cargo_type.replaceAll("_", " "))}</td><td>${format(row.predicted_internal_temperature_c)} °C</td><td>${format(row.estimated_risk_index_pct, 1)} / 100</td><td>${riskBadge(row.risk_level)}</td><td>${formatTimestamp(row.timestamp)}</td></tr>`).join("")}</tbody></table></div>` : '<p class="empty-state">No predictions have been saved yet.</p>'}
    </section>`;
}

function renderUserOverview(dashboard) {
  const recent = dashboard.recent_predictions;
  pageRoot.innerHTML = `${pageHeader(`Welcome, ${escapeHtml(dashboard.account.username)}`, "Your cold-chain prediction workspace.")}
    <section class="overview-hero">
      <div><p class="eyebrow">YOUR WORKSPACE</p><h2>Review your shipment estimates and saved predictions.</h2><p>Your prediction history is private to your account. Administrators can manage system-wide settings and access.</p><a class="button button-primary" href="/prediction" data-route="/prediction">Start a prediction <span aria-hidden="true">→</span></a></div>
      <div class="hero-summary"><span>Average predicted temperature</span><strong>${format(dashboard.average_predicted_temperature_c)} °C</strong><span>Average operational risk</span><strong>${format(dashboard.average_risk_index_pct, 1)} / 100</strong></div>
    </section>
    <section class="metric-grid">
      ${metricCard("Your predictions", Number(dashboard.total_predictions).toLocaleString(), "saved to your account", "blue")}
      ${metricCard("Created today", Number(dashboard.predictions_today).toLocaleString(), "UTC", "green")}
      ${metricCard("High risk", Number(dashboard.high_risk_predictions).toLocaleString(), "saved predictions", "amber")}
      ${metricCard("Critical risk", Number(dashboard.critical_risk_predictions).toLocaleString(), "saved predictions", "violet")}
    </section>
    <section class="panel overview-recent"><div class="section-title"><div><p class="eyebrow">PRIVATE HISTORY</p><h2>Your recent predictions</h2></div><a class="text-button" data-route="/history" href="/history">View full history →</a></div>
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
          <div class="prediction-form-actions">
            <button class="button button-primary" type="submit" id="predict-button">Predict temperature <span aria-hidden="true">→</span></button>
            <button class="button button-secondary" type="button" id="clear-prediction-form">Clear</button>
          </div>
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
    `<article class="chat-message ${message.role}"><strong>${message.role === "user" ? "You" : "Assistant"}</strong>${message.role === "assistant" ? `<div class="chat-markdown">${renderMarkdown(message.content)}</div>` : `<p>${escapeHtml(message.content)}</p>`}</article>`
  ).join("");
}

function decodeCommonEntities(value) {
  const named = { amp: "&", lt: "<", gt: ">", quot: "\"", apos: "'", nbsp: " " };
  return value.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);/gi, (entity, code) => {
    if (code[0] !== "#") return named[code.toLowerCase()] ?? entity;
    const number = code[1].toLowerCase() === "x"
      ? Number.parseInt(code.slice(2), 16)
      : Number.parseInt(code.slice(1), 10);
    return Number.isFinite(number) && number <= 0x10ffff ? String.fromCodePoint(number) : entity;
  });
}

function renderMarkdownInline(value) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/__(.+?)__/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>")
    .replace(/(^|[^_])_([^_\n]+)_(?!_)/g, "$1<em>$2</em>");
}

function renderMarkdown(source) {
  const normalized = decodeCommonEntities(String(source))
    .replace(/\\([*|_#`~>+\-.!])/g, "$1")
    .replace(/\r\n?/g, "\n");
  const lines = normalized.split("\n");
  const blocks = [];
  for (let index = 0; index < lines.length;) {
    const line = lines[index].trim();
    if (!line) {
      index += 1;
      continue;
    }
    if (line.startsWith("|") && index + 1 < lines.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[index + 1])) {
      const headers = line.replace(/^\||\|$/g, "").split("|").map((item) => item.trim());
      index += 2;
      const rows = [];
      while (index < lines.length && lines[index].trim().startsWith("|")) {
        rows.push(lines[index].trim().replace(/^\||\|$/g, "").split("|").map((item) => item.trim()));
        index += 1;
      }
      blocks.push(`<div class="table-wrap chat-table"><table><thead><tr>${headers.map((cell) => `<th>${renderMarkdownInline(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${headers.map((_, cell) => `<td>${renderMarkdownInline(row[cell] || "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      blocks.push(`<h${level}>${renderMarkdownInline(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }
    const unordered = line.match(/^[-*+]\s+(.+)$/);
    const ordered = line.match(/^\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const orderedList = Boolean(ordered);
      const tag = orderedList ? "ol" : "ul";
      const items = [];
      while (index < lines.length) {
        const match = lines[index].trim().match(orderedList ? /^\d+[.)]\s+(.+)$/ : /^[-*+]\s+(.+)$/);
        if (!match) break;
        items.push(`<li>${renderMarkdownInline(match[1])}</li>`);
        index += 1;
      }
      blocks.push(`<${tag}>${items.join("")}</${tag}>`);
      continue;
    }
    const paragraph = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !/^(#{1,3})\s+/.test(lines[index].trim()) && !/^[-*+]\s+/.test(lines[index].trim()) && !/^\d+[.)]\s+/.test(lines[index].trim())) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(`<p>${renderMarkdownInline(paragraph.join(" "))}</p>`);
  }
  return blocks.join("");
}

function resizeFollowupField(input) {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 240)}px`;
  input.style.overflowY = input.scrollHeight > 240 ? "auto" : "hidden";
}

function clearPredictionForm() {
  const form = $("#prediction-form");
  form.querySelectorAll('input[type="number"]').forEach((input) => {
    input.value = "";
  });
  $("#cargo-type").value = "";
  $("#form-error").hidden = true;

  latestPrediction = null;
  followupMessages = [];
  sessionStorage.removeItem("latestPrediction");
  sessionStorage.removeItem("predictionFollowupMessages");

  $("#result-subtitle").textContent = "Submit telemetry to view an estimate.";
  $("#predicted-temp").textContent = "—";
  $("#deviation").textContent = "—";
  $("#risk-index").textContent = "—";
  $("#recommendation").textContent = "Waiting for a prediction";
  $("#risk-badge").textContent = "AWAITING INPUT";
  $("#risk-badge").className = "risk-badge neutral";
  $("#factor-list").innerHTML = "<li>Feature effects appear after prediction.</li>";

  $("#chat-messages").replaceChildren();
  $("#chat-question").disabled = true;
  $("#chat-question").value = "";
  $("#chat-question").placeholder = "Run a prediction to ask a follow-up question";
  $("#chat-button").disabled = true;
  $("#chat-footnote").textContent = "Run a prediction first to enable context-aware follow-up questions.";
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
  $("#clear-prediction-form").addEventListener("click", clearPredictionForm);
  try {
    const { cargo_types: cargoTypes } = await api("/options");
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.disabled = true;
    placeholder.selected = true;
    placeholder.textContent = "Select cargo type";
    select.replaceChildren(...cargoTypes.map((cargoType) => {
      const option = document.createElement("option");
      option.value = cargoType;
      option.textContent = cargoType.replaceAll("_", " ");
      return option;
    }));
    select.prepend(placeholder);
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

function renderAccountPage(passwordRequired = false, message = "") {
  currentRoute = "/account";
  $("#account-bar").hidden = passwordRequired;
  document.title = `My Account | Cold Chain`;
  pageRoot.innerHTML = `${pageHeader(passwordRequired ? "Set a new password" : "My Account", passwordRequired ? `Account ${escapeHtml(currentUser.username)} must change its temporary password before continuing.` : routeInfo["/account"].description)}
    ${message ? `<p class="message success-message" role="status">${escapeHtml(message)}</p>` : ""}
    ${passwordRequired ? "" : `<section class="panel account-panel">
      <div class="panel-heading"><div><h2>Profile</h2><p>Update your contact email.</p></div></div>
      <form id="profile-form" class="account-form">
        <label>Username<input value="${escapeHtml(currentUser.username)}" disabled /></label>
        <label>Email address<input name="email" type="email" maxlength="254" value="${escapeHtml(currentUser.email || "")}" /></label>
        <button class="button button-primary" type="submit">Save profile</button>
        <p id="profile-message" class="message" role="status" hidden></p>
      </form>
    </section>`}
    <section class="panel account-panel">
      <div class="panel-heading"><div><h2>${passwordRequired ? "Choose a new password" : "Change password"}</h2><p>Use at least 6 characters with three character types.</p></div></div>
      <form id="password-form" class="account-form">
        <label>${passwordRequired ? "Temporary password" : "Current password"}<input name="current_password" type="password" autocomplete="current-password" required maxlength="256" /></label>
        <label>New password<input name="new_password" type="password" autocomplete="new-password" required minlength="6" maxlength="128" /></label>
        <button class="button button-primary" type="submit">${passwordRequired ? "Save password" : "Change password"}</button>
        <p id="password-message" class="message" role="status" hidden></p>
      </form>
    </section>`;
  const profileForm = $("#profile-form");
  if (profileForm) {
    profileForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const email = new FormData(profileForm).get("email").trim();
      const feedback = $("#profile-message");
      try {
        currentUser = await api("/profile", {
          method: "PATCH",
          body: JSON.stringify({ email: email || null }),
        });
        feedback.textContent = "Profile updated.";
        feedback.className = "message success-message";
      } catch (error) {
        feedback.textContent = error.message;
        feedback.className = "message error-message";
      }
      feedback.hidden = false;
    });
  }
  $("#password-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const payload = {
      current_password: values.get("current_password"),
      new_password: values.get("new_password"),
    };
    const feedback = $("#password-message");
    try {
      const result = await api("/auth/change-password", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      csrfToken = result.csrf_token;
      const refreshed = await api("/auth/me");
      currentUser = refreshed.user;
      if (passwordRequired) {
        enterWorkspace();
      } else {
        form.reset();
        feedback.textContent = "Password changed successfully.";
        feedback.className = "message success-message";
        feedback.hidden = false;
      }
    } catch (error) {
      feedback.textContent = error.message;
      feedback.className = "message error-message";
      feedback.hidden = false;
    }
  });
}

function renderUserRows(users) {
  const body = $("#admin-user-rows");
  if (!body) return;
  if (!users.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty-state">No users match your search.</td></tr>';
    $("#admin-user-count").textContent = "0 users found";
    return;
  }
  body.innerHTML = users.map((user) => `<tr>
    <td>${escapeHtml(user.full_name || "—")}</td>
    <td>${escapeHtml(user.username)}</td>
    <td>${escapeHtml(user.email || "—")}</td>
    <td><span class="role-pill">${escapeHtml(user.role)}</span></td>
    <td><span class="account-status ${user.is_active ? "active" : "inactive"}">${user.is_active ? "Active" : "Inactive"}</span></td>
    <td><a class="button button-secondary" data-route="/user-details/${user.id}" href="/user-details/${user.id}">View Details</a></td>
  </tr>`).join("");
  $("#admin-user-count").textContent = `${users.length} user${users.length === 1 ? "" : "s"} found`;
}

async function searchAdminUsers(search) {
  const status = $("#admin-user-loading");
  const error = $("#admin-user-error");
  const sequence = ++userSearchSequence;
  status.hidden = false;
  status.textContent = "Searching users…";
  error.hidden = true;
  try {
    const params = new URLSearchParams();
    if (search.trim()) params.set("search", search.trim());
    const users = await api(`/admin/users${params.size ? `?${params}` : ""}`);
    if (sequence !== userSearchSequence) return;
    renderUserRows(users);
    status.hidden = true;
  } catch (requestError) {
    if (sequence !== userSearchSequence) return;
    status.hidden = true;
    error.textContent = `Users could not be loaded: ${requestError.message}`;
    error.hidden = false;
  }
}

function renderUserManagement(users, message = "") {
  pageRoot.innerHTML = `${pageHeader(routeInfo["/users"].title, routeInfo["/users"].description)}
    ${message ? `<p class="message success-message" role="status">${escapeHtml(message)}</p>` : ""}
    <section class="panel account-panel">
      <div class="panel-heading"><div><h2>Create account</h2><p>New users must change their password at first sign-in.</p></div></div>
      <form id="create-user-form" class="admin-create-form">
        <label>Username<input name="username" minlength="3" maxlength="32" pattern="[A-Za-z0-9_.\\x2d]+" required /></label>
        <label>Email (optional)<input name="email" type="email" maxlength="254" /></label>
        <label>Temporary password<input name="password" type="password" minlength="6" maxlength="128" autocomplete="new-password" required /></label>
        <label>Role<select name="role"><option value="USER">USER</option><option value="ADMIN">ADMIN</option></select></label>
        <button class="button button-primary" type="submit">Create account</button>
        <p id="create-user-message" class="message" role="status" hidden></p>
      </form>
    </section>
    <section class="panel account-panel">
      <div class="panel-heading"><div><h2>Accounts</h2><p>Search all user accounts by name, username, email, or ID.</p></div></div>
      <label class="user-search-label">Search users<input id="admin-user-search" type="search" maxlength="120" placeholder="Name, username, email, or user ID" autocomplete="off" /></label>
      <p id="admin-user-loading" class="loading-state" role="status" hidden></p>
      <p id="admin-user-error" class="page-error" role="alert" hidden></p>
      <p id="admin-user-count" class="table-summary">${users.length} users</p>
      <div class="table-wrap"><table><thead><tr><th>Full name</th><th>Username</th><th>Email</th><th>Role</th><th>Status</th><th>Details</th></tr></thead><tbody id="admin-user-rows"></tbody></table></div>
    </section>`;
  renderUserRows(users);
  const searchInput = $("#admin-user-search");
  searchInput.value = userSearchTerm;
  searchInput.addEventListener("input", () => {
    userSearchTerm = searchInput.value;
    clearTimeout(userSearchTimer);
    userSearchTimer = setTimeout(() => searchAdminUsers(searchInput.value.trim()), 180);
  });
  $("#create-user-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const feedback = $("#create-user-message");
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await api("/admin/users", {
        method: "POST",
        body: JSON.stringify({
          username: values.get("username"),
          email: values.get("email") || null,
          password: values.get("password"),
          role: values.get("role"),
        }),
      });
      form.reset();
      await searchAdminUsers(userSearchTerm);
      feedback.textContent = "Account created.";
      feedback.className = "message success-message";
      feedback.hidden = false;
    } catch (error) {
      feedback.textContent = error.message;
      feedback.className = "message error-message";
      feedback.hidden = false;
    } finally {
      button.disabled = false;
    }
  });
}

function renderAdminUserDetails(user, notice = "") {
  const roleOptions = ["USER", "ADMIN"].map((role) => `<option value="${role}" ${role === user.role ? "selected" : ""}>${role}</option>`).join("");
  pageRoot.innerHTML = `${pageHeader("User Details", `Account profile for ${escapeHtml(user.username)}.`)}
    <p><a class="text-button" data-route="/users" href="/users">← Back to User Management</a></p>
    ${notice ? `<p class="message success-message" role="status">${escapeHtml(notice)}</p>` : ""}
    <section class="panel account-panel">
      <div class="panel-heading"><div><h2>Account information</h2><p>Identity and account activity. Credentials are never displayed.</p></div></div>
      <dl class="user-detail-grid">
        <div><dt>Full name</dt><dd>${escapeHtml(user.full_name || "—")}</dd></div>
        <div><dt>Username</dt><dd>${escapeHtml(user.username)}</dd></div>
        <div><dt>Email</dt><dd>${escapeHtml(user.email || "—")}</dd></div>
        <div><dt>User ID</dt><dd>${user.id}</dd></div>
        <div><dt>Role</dt><dd>${escapeHtml(user.role)}</dd></div>
        <div><dt>Account status</dt><dd>${user.is_active ? "Active" : "Inactive"}</dd></div>
        <div><dt>Created</dt><dd>${formatTimestamp(user.created_at)}</dd></div>
        <div><dt>Last login</dt><dd>${user.last_login_at ? formatTimestamp(user.last_login_at) : "Never"}</dd></div>
      </dl>
    </section>
    <section class="panel account-panel">
      <div class="panel-heading"><div><h2>Profile editing</h2><p>Role and deactivation changes require confirmation.</p></div></div>
      <form id="admin-profile-form" class="account-form" data-user-id="${user.id}" data-original-role="${escapeHtml(user.role)}" data-original-active="${user.is_active}">
        <label>Full name<input name="full_name" value="${escapeHtml(user.full_name || "")}" maxlength="120" required /></label>
        <label>Email<input name="email" type="email" value="${escapeHtml(user.email || "")}" maxlength="254" /></label>
        <label>Role<select name="role">${roleOptions}</select></label>
        <label class="inline-check"><input name="is_active" type="checkbox" ${user.is_active ? "checked" : ""} /> Active account</label>
        <div class="user-detail-actions"><button class="button button-primary" type="submit">Save Changes</button><button class="button button-secondary" id="cancel-user-edit" type="button">Cancel</button></div>
        <p id="admin-profile-message" class="message" role="status" hidden></p>
      </form>
    </section>
    <section class="panel account-panel">
      <div class="panel-heading"><div><h2>Password Management</h2><p>Set a temporary password. The user must change it at next sign-in.</p></div></div>
      <form id="admin-password-form" class="account-form" data-user-id="${user.id}">
        <label>Temporary password<input name="new_password" type="password" autocomplete="new-password" minlength="6" maxlength="128" required /></label>
        <label>Confirm temporary password<input name="confirm_password" type="password" autocomplete="new-password" minlength="6" maxlength="128" required /></label>
        <button class="button button-secondary" type="submit">Reset password and require change</button>
        <p id="admin-password-message" class="message" role="status" hidden></p>
      </form>
    </section>`;
  const profileForm = $("#admin-profile-form");
  profileForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const values = new FormData(profileForm);
    const nextRole = values.get("role");
    const nextActive = values.has("is_active");
    const roleChanged = nextRole !== profileForm.dataset.originalRole;
    const willDeactivate = !nextActive && profileForm.dataset.originalActive === "true";
    if (roleChanged && !window.confirm(`Confirm changing this account's role from ${profileForm.dataset.originalRole} to ${nextRole}?`)) return;
    if (willDeactivate && !window.confirm("Confirm deactivating this account? The user will be signed out.")) return;
    const feedback = $("#admin-profile-message");
    const button = profileForm.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const updated = await api(`/admin/users/${user.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          full_name: values.get("full_name"),
          email: values.get("email") || null,
          role: nextRole,
          is_active: nextActive,
        }),
      });
      feedback.textContent = "Account changes saved.";
      feedback.className = "message success-message";
      feedback.hidden = false;
      const fresh = await api(`/admin/users/${updated.id}`);
      renderAdminUserDetails(fresh, "Account changes saved.");
    } catch (error) {
      feedback.textContent = error.message;
      feedback.className = "message error-message";
      feedback.hidden = false;
    } finally {
      if (button.isConnected) button.disabled = false;
    }
  });
  $("#cancel-user-edit").addEventListener("click", () => renderAdminUserDetails(user));
  $("#admin-password-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    const feedback = $("#admin-password-message");
    if (values.get("new_password") !== values.get("confirm_password")) {
      feedback.textContent = "Passwords do not match.";
      feedback.className = "message error-message";
      feedback.hidden = false;
      return;
    }
    if (!window.confirm(`Reset the password for ${user.username} and require a change at next sign-in?`)) return;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      await api(`/admin/users/${user.id}/reset-password`, {
        method: "POST",
        body: JSON.stringify({ new_password: values.get("new_password") }),
      });
      form.reset();
      feedback.textContent = "Temporary password saved as a hash; the user must change it at next sign-in.";
      feedback.className = "message success-message";
      feedback.hidden = false;
    } catch (error) {
      feedback.textContent = error.message;
      feedback.className = "message error-message";
      feedback.hidden = false;
    } finally {
      button.disabled = false;
    }
  });
}

let activityOffset = 0;
const activityLimit = 50;

function activityQuery() {
  const params = new URLSearchParams();
  const search = $("#activity-search").value.trim();
  const action = $("#activity-action").value;
  const outcome = $("#activity-outcome").value;
  const start = $("#activity-start").value;
  const end = $("#activity-end").value;
  if (search) params.set("search", search);
  if (action) params.set("action", action);
  if (outcome) params.set("outcome", outcome);
  if (start) params.set("start", `${start}T00:00:00+00:00`);
  if (end) params.set("end", `${end}T23:59:59+00:00`);
  return params;
}

async function loadActivity() {
  const params = activityQuery();
  params.set("offset", String(activityOffset));
  params.set("limit", String(activityLimit));
  const payload = await api(`/admin/activity?${params}`);
  $("#activity-count").textContent = `${payload.total.toLocaleString()} events`;
  $("#activity-rows").innerHTML = payload.items.length ? payload.items.map((row) => `<tr>
    <td>${formatTimestamp(row.timestamp)}</td><td>${escapeHtml(row.username || "System")}</td>
    <td>${escapeHtml(row.action)}</td><td>${escapeHtml(row.target_type || "—")}</td>
    <td>${escapeHtml(row.target_id || "—")}</td><td>${escapeHtml(row.outcome)}</td>
    <td><code>${escapeHtml(JSON.stringify(row.safe_metadata))}</code></td>
  </tr>`).join("") : '<tr><td colspan="7" class="empty-state">No events match these filters.</td></tr>';
  $("#activity-previous").disabled = activityOffset === 0;
  $("#activity-next").disabled = activityOffset + activityLimit >= payload.total;
  const exportParams = activityQuery();
  $("#activity-export").href = `/admin/activity.csv?${exportParams}`;
}

function renderActivityPage() {
  activityOffset = 0;
  pageRoot.innerHTML = `${pageHeader(routeInfo["/activity"].title, routeInfo["/activity"].description)}
    <section class="panel account-panel">
      <div class="activity-controls">
        <label>Search user or ID<input id="activity-search" type="search" maxlength="100" /></label>
        <label>Action<select id="activity-action"><option value="">All actions</option><option>login_success</option><option>login_failed</option><option>login_throttled</option><option>logout</option><option>prediction_created</option><option>account_created</option><option>account_updated</option><option>account_activated</option><option>account_deactivated</option><option>role_changed</option><option>password_reset</option><option>password_changed</option><option>access_denied</option></select></label>
        <label>Outcome<select id="activity-outcome"><option value="">All outcomes</option><option value="success">Success</option><option value="failure">Failure</option></select></label>
        <label>From (UTC)<input id="activity-start" type="date" /></label>
        <label>To (UTC)<input id="activity-end" type="date" /></label>
        <button id="activity-filter" class="button button-primary" type="button">Apply filters</button>
        <a id="activity-export" class="button button-secondary" href="/admin/activity.csv">Export CSV</a>
      </div>
      <p class="table-summary" id="activity-count">Loading activity...</p>
      <div class="table-wrap"><table><thead><tr><th>Time (UTC)</th><th>Actor</th><th>Action</th><th>Target</th><th>Target ID</th><th>Outcome</th><th>Safe metadata</th></tr></thead><tbody id="activity-rows"></tbody></table></div>
      <div class="pagination-controls"><button id="activity-previous" class="button button-secondary" type="button">Previous</button><button id="activity-next" class="button button-secondary" type="button">Next</button></div>
    </section>`;
  $("#activity-filter").addEventListener("click", () => {
    activityOffset = 0;
    loadActivity().catch((error) => {
      $("#activity-count").textContent = `Activity could not be loaded: ${error.message}`;
    });
  });
  $("#activity-previous").addEventListener("click", () => {
    activityOffset = Math.max(0, activityOffset - activityLimit);
    loadActivity();
  });
  $("#activity-next").addEventListener("click", () => {
    activityOffset += activityLimit;
    loadActivity();
  });
  loadActivity().catch((error) => {
    $("#activity-count").textContent = `Activity could not be loaded: ${error.message}`;
  });
}

async function renderOverviewData() {
  if (currentUser.role === "USER") {
    const dashboard = await api("/user/dashboard");
    if (currentRoute === "/" || currentRoute === "/overview") renderUserOverview(dashboard);
    return;
  }
  const [dashboard, health] = await Promise.all([api("/admin/dashboard"), api("/health")]);
  if (currentRoute !== "/" && currentRoute !== "/overview") return;
  renderAdminOverview(dashboard, health);
}

async function renderPage(route) {
  if (!currentUser) {
    showLogin();
    return;
  }
  const isUserDetails = /^\/user-details\/\d+$/.test(route);
  if (!routeInfo[route] && !isUserDetails) route = "/";
  if (isUserDetails) routeInfo[route] = routeInfo["/admin/user-details"];
  if (currentUser.role !== "ADMIN" && (["/analytics", "/dataset", "/users", "/activity"].includes(route) || isUserDetails)) {
    route = "/";
    history.replaceState({}, "", route);
  }
  setPage(route);
  pageRoot.innerHTML = `${pageHeader(routeInfo[route].title, "Loading page data…")}<div class="loading-state">Loading…</div>`;
  analyticsData = null;
  datasetData = null;
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
    } else if (route === "/users") {
      const users = await api("/admin/users");
      if (currentRoute === route) renderUserManagement(users);
    } else if (isUserDetails) {
      const user = await api(`/admin/users/${route.split("/").at(-1)}`);
      if (currentRoute === route) renderAdminUserDetails(user);
    } else if (route === "/activity") {
      renderActivityPage();
    } else if (route === "/account") {
      renderAccountPage();
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

initializeApp();
