/* JOS shared UI helpers.
 *
 * Loaded before app.js (no build step, no modules) so these are plain globals.
 * This file grows over the UX overhaul; Phase 1 seeds it with the one canonical
 * "today" helper. Later phases add the toast queue, empty/error states, dateField,
 * and entityPicker here.
 */

/* localDateStr(d): a Date's *local* calendar day as YYYY-MM-DD (no UTC shift). */
function localDateStr(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/* localToday(): the browser's *local* calendar day as YYYY-MM-DD.
 * Replaces `new Date().toISOString().slice(0,10)`, which is UTC and rolls over a
 * day early every evening in Mountain time (audit C4). */
function localToday() {
  return localDateStr(new Date());
}

function _uiEscape(s) {
  if (s == null) return "";
  return String(s)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}

/* ---- Toast queue (audit M5/L4) --------------------------------------------
 * One canonical feedback channel. Toasts stack instead of clobbering each other,
 * so a second success never destroys the first's Undo. Replaces alert() and the
 * old single-slot container.
 *   toast(msg, { type, undo, linkLabel, onLink, timeout })
 *     type: "info" | "success" | "error"
 *     undo: async callback -> renders an Undo button
 *     linkLabel/onLink: renders a labelled action button ("View note")
 * Returns a dismiss() function. */
function toast(msg, opts = {}) {
  const { type = "info", undo = null, linkLabel = null, onLink = null, timeout } = opts;
  const container = document.getElementById("toast-container");
  if (!container) return () => {};
  container.classList.add("visible");

  const el = document.createElement("div");
  el.className = `toast toast-${type}`;
  const span = document.createElement("span");
  span.className = "toast-msg";
  span.textContent = msg;
  el.appendChild(span);

  let timer = null;
  const dismiss = () => {
    if (timer) { clearTimeout(timer); timer = null; }
    el.classList.add("leaving");
    setTimeout(() => {
      el.remove();
      if (!container.children.length) container.classList.remove("visible");
    }, 180);
  };

  if (typeof undo === "function") {
    const b = document.createElement("button");
    b.className = "toast-undo";
    b.textContent = "Undo";
    b.addEventListener("click", async () => {
      dismiss();
      try { await undo(); } catch (e) { toast(e.message || "Undo failed", { type: "error" }); }
    });
    el.appendChild(b);
  }
  if (linkLabel && typeof onLink === "function") {
    const b = document.createElement("button");
    b.className = "toast-link";
    b.textContent = linkLabel;
    b.addEventListener("click", () => { dismiss(); onLink(); });
    el.appendChild(b);
  }
  const x = document.createElement("button");
  x.className = "toast-dismiss";
  x.setAttribute("aria-label", "Dismiss");
  x.textContent = "×";
  x.addEventListener("click", dismiss);
  el.appendChild(x);

  container.appendChild(el);
  const ms = timeout != null ? timeout : (type === "error" ? 9000 : 6000);
  if (ms > 0) timer = setTimeout(dismiss, ms);
  return dismiss;
}

function toastError(msg) { return toast(msg, { type: "error" }); }
function toastSuccess(msg, opts) { return toast(msg, Object.assign({}, opts, { type: "success" })); }

/* ---- Empty & error states (audit M8, §8) ---------------------------------- */
/* emptyState returns an HTML string for table/list bodies. */
function emptyState(text, opts = {}) {
  const { icon = "", actionLabel = "", actionId = "" } = opts;
  return `<div class="empty-state">` +
    (icon ? `<div class="empty-state-icon">${icon}</div>` : "") +
    `<div class="empty-state-text">${_uiEscape(text)}</div>` +
    (actionLabel
      ? `<button class="empty-state-action"${actionId ? ` id="${actionId}"` : ""}>${_uiEscape(actionLabel)}</button>`
      : "") +
    `</div>`;
}

/* ---- Canonical date field (audit M3) -------------------------------------
 * One way to pick a date everywhere: a native <input type=date> plus quick-pick
 * chips. Renders an input with id `${prefix}-dl-date`, which getDeadlineValue()/
 * setDeadlineSelects() in app.js read and write. */
function dateField(prefix, value = "", opts = {}) {
  const quickPicks = opts.quickPicks !== false;
  const v = value && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : "";
  return `<div class="date-field">` +
    `<input type="date" id="${prefix}-dl-date" class="date-field-input" value="${v}">` +
    (quickPicks
      ? `<div class="dl-quick-btns">` +
        `<button type="button" class="dl-quick-btn" data-quick="today" data-prefix="${prefix}">Today</button>` +
        `<button type="button" class="dl-quick-btn" data-quick="this-week" data-prefix="${prefix}">This week</button>` +
        `<button type="button" class="dl-quick-btn" data-quick="next-week" data-prefix="${prefix}">Next week</button>` +
        `</div>`
      : "") +
    `</div>`;
}

/* errorState returns a DOM node with a working Retry button (never a hidden card). */
function errorState(message, retryFn) {
  const el = document.createElement("div");
  el.className = "error-state";
  const msg = document.createElement("div");
  msg.className = "error-state-msg";
  msg.textContent = message || "Something went wrong.";
  el.appendChild(msg);
  if (typeof retryFn === "function") {
    const b = document.createElement("button");
    b.className = "error-state-retry";
    b.textContent = "Retry";
    b.addEventListener("click", () => retryFn());
    el.appendChild(b);
  }
  return el;
}
