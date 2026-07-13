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

/* ---- Entity picker (person / org) — audit M4 -----------------------------
 * One search-dropdown with an explicit "+ Create" row for choosing (or creating)
 * a person or organization. Creation is NEVER implicit on a typo: typing a name
 * without picking a row leaves the selection empty (getId() === "").
 *
 *   entityPicker(hostEl, { type, value, valueId, placeholder, allowCreate,
 *                          orgList, onSelect }) -> handle
 *   handle.getId()   -> selected entity id ("" if nothing committed)
 *   handle.getName() -> current input text
 *   handle.clear()   -> reset
 * For type 'org', creation is deferred (orgs are created by name on save via
 * _org_for_name), so getName() carries the label and getId() may stay "". */
function entityPicker(host, opts = {}) {
  const {
    type = "person",
    value = "",
    valueId = "",
    placeholder = (type === "org" ? "Search organizations" : "Search people"),
    allowCreate = true,
    orgList = null,
    onSelect = null,
  } = opts;

  host.classList.add("entity-picker");
  host.innerHTML =
    `<input class="entity-picker-input" type="text" autocomplete="off" ` +
      `placeholder="${_uiEscape(placeholder)}" value="${_uiEscape(value)}">` +
    `<div class="entity-picker-menu hidden"></div>`;
  const input = host.querySelector(".entity-picker-input");
  const menu = host.querySelector(".entity-picker-menu");
  const sel = { id: valueId || "", name: value || "" };

  const close = () => { menu.classList.add("hidden"); menu.innerHTML = ""; };
  const commit = (id, name) => {
    sel.id = id; sel.name = name; input.value = name; close();
    if (typeof onSelect === "function") onSelect(id, name);
  };

  async function fetchMatches(q) {
    if (type === "org") {
      const ql = q.toLowerCase();
      return (orgList || []).filter((o) => (o.name || "").toLowerCase().includes(ql))
        .slice(0, 8).map((o) => ({ id: o.id, name: o.name }));
    }
    try {
      const r = await fetch(`/api/contacts?q=${encodeURIComponent(q)}`);
      const data = await r.json();
      const list = Array.isArray(data) ? data : (data.contacts || []);
      return list.slice(0, 8).map((c) => ({
        id: c.id, name: c.name, sub: [c.title, c.company].filter(Boolean).join(" · "),
      }));
    } catch { return []; }
  }

  async function createEntity(name) {
    if (type === "org") { commit("", name); return; }  // org id resolved on save
    try {
      const r = await fetch("/api/contacts", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const data = await r.json();
      if (!data.ok || !data.id) { toastError(data.error || "Could not create contact"); return; }
      commit(data.id, name);
      if (typeof window.loadPeopleCache === "function") { try { await window.loadPeopleCache(); } catch (_) {} }
    } catch (e) { toastError(e.message || "Could not create contact"); }
  }

  const render = async () => {
    const q = input.value.trim();
    if (sel.name && q !== sel.name) sel.id = "";  // typing invalidates a prior pick
    if (!q) { close(); return; }
    const matches = await fetchMatches(q);
    const rows = matches.map((m) =>
      `<div class="entity-picker-row" data-id="${_uiEscape(m.id)}" data-name="${_uiEscape(m.name)}">` +
      `<span class="entity-picker-name">${_uiEscape(m.name)}</span>` +
      (m.sub ? `<span class="entity-picker-sub">${_uiEscape(m.sub)}</span>` : "") +
      `</div>`);
    if (allowCreate && !matches.some((m) => m.name.toLowerCase() === q.toLowerCase())) {
      rows.push(`<div class="entity-picker-row entity-picker-create" data-create="1">` +
        `+ Create &ldquo;${_uiEscape(q)}&rdquo;</div>`);
    }
    menu.innerHTML = rows.join("") || `<div class="entity-picker-empty">No matches</div>`;
    menu.classList.remove("hidden");
    menu.querySelectorAll(".entity-picker-row").forEach((row) => {
      row.addEventListener("mousedown", (e) => {
        e.preventDefault();
        if (row.dataset.create) createEntity(q);
        else commit(row.dataset.id, row.dataset.name);
      });
    });
  };

  let timer = null;
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(render, 160); });
  input.addEventListener("focus", () => { if (input.value.trim()) render(); });
  input.addEventListener("blur", () => setTimeout(close, 150));

  return {
    getId: () => (input.value.trim() === sel.name ? sel.id : ""),
    getName: () => input.value.trim(),
    clear: () => { sel.id = ""; sel.name = ""; input.value = ""; },
    input,
  };
}

/* ---- formModal: promise-based edit form (replaces prompt(), audit M5) ------
 * const vals = await formModal({ title, fields: [{key,label,type,value,options}] });
 *   type: 'text' | 'select' | 'date'   -> resolves to {key: value} or null on cancel. */
function formModal({ title = "Edit", fields = [], submitLabel = "Save" } = {}) {
  return new Promise((resolve) => {
    const back = document.createElement("div");
    back.className = "form-modal-backdrop";
    const rows = fields.map((f) => {
      const id = `fm-${f.key}`;
      let control;
      if (f.type === "select") {
        control = `<select id="${id}">` + (f.options || []).map((o) => {
          const val = typeof o === "string" ? o : o.value;
          const lbl = typeof o === "string" ? o : o.label;
          return `<option value="${_uiEscape(val)}"${String(val) === String(f.value) ? " selected" : ""}>${_uiEscape(lbl)}</option>`;
        }).join("") + `</select>`;
      } else if (f.type === "date") {
        control = `<input id="${id}" type="date" value="${_uiEscape(f.value || "")}">`;
      } else {
        control = `<input id="${id}" type="text" value="${_uiEscape(f.value || "")}">`;
      }
      return `<label class="form-modal-field"><span>${_uiEscape(f.label)}</span>${control}</label>`;
    }).join("");
    back.innerHTML =
      `<div class="form-modal" role="dialog" aria-modal="true">` +
      `<div class="form-modal-title">${_uiEscape(title)}</div>${rows}` +
      `<div class="form-modal-actions">` +
      `<button class="form-modal-cancel" type="button">Cancel</button>` +
      `<button class="form-modal-save" type="button">${_uiEscape(submitLabel)}</button>` +
      `</div></div>`;
    document.body.appendChild(back);
    const cleanup = (result) => { back.remove(); resolve(result); };
    const save = () => {
      const out = {};
      fields.forEach((f) => { out[f.key] = (document.getElementById(`fm-${f.key}`).value || "").trim(); });
      cleanup(out);
    };
    back.querySelector(".form-modal-cancel").addEventListener("click", () => cleanup(null));
    back.querySelector(".form-modal-save").addEventListener("click", save);
    back.addEventListener("mousedown", (e) => { if (e.target === back) cleanup(null); });
    back.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.stopPropagation(); cleanup(null); }
      if (e.key === "Enter" && e.target.tagName !== "SELECT") { e.preventDefault(); save(); }
    });
    const first = back.querySelector("input, select");
    if (first) setTimeout(() => { first.focus(); if (first.select) first.select(); }, 20);
  });
}

/* pickEntityModal: choose a person/org via entityPicker in a modal. Resolves {id,name} or null. */
function pickEntityModal({ title = "Choose", type = "person", allowCreate = false, orgList = null, submitLabel = "Select" } = {}) {
  return new Promise((resolve) => {
    const back = document.createElement("div");
    back.className = "form-modal-backdrop";
    back.innerHTML =
      `<div class="form-modal" role="dialog" aria-modal="true">` +
      `<div class="form-modal-title">${_uiEscape(title)}</div>` +
      `<div class="form-modal-field"><div id="pick-entity-host"></div></div>` +
      `<div class="form-modal-actions">` +
      `<button class="form-modal-cancel" type="button">Cancel</button>` +
      `<button class="form-modal-save" type="button">${_uiEscape(submitLabel)}</button>` +
      `</div></div>`;
    document.body.appendChild(back);
    const picker = entityPicker(back.querySelector("#pick-entity-host"), { type, allowCreate, orgList });
    const cleanup = (r) => { back.remove(); resolve(r); };
    back.querySelector(".form-modal-cancel").addEventListener("click", () => cleanup(null));
    back.addEventListener("mousedown", (e) => { if (e.target === back) cleanup(null); });
    back.addEventListener("keydown", (e) => { if (e.key === "Escape") cleanup(null); });
    back.querySelector(".form-modal-save").addEventListener("click", () => {
      const id = picker.getId();
      if (!id) { toastError("Pick a match from the list."); return; }
      cleanup({ id, name: picker.getName() });
    });
    setTimeout(() => picker.input.focus(), 20);
  });
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
