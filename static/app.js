// Notes Dashboard — client-side app

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const state = {
  tab: "home",
  meetings: [],
  tasksByStatus: { active: [], backburner: [], done: [] },
  tasksGroupsInScope: [],
  paperOrder: ["active", "backburner", "done"],
  drawerTask: null,
  selectedMeetingId: null,
  selectedTaskIdx: -1,
  facets: { groups: [], purposes: [], attendees: [], unaliased_raw_groups: [] },
  stats: null,
  meetingFilters: { group: "", purpose: "", attendee: "", dateFrom: "", dateTo: "", hasOpenTasks: false },
  smartView: "today",
  smartViewTasks: [],
  dailyPlanTasks: [],
  dailyPlanOrder: [],
};

// ---------- Utilities ----------
function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

// ---------- Filter state ----------
function meetingsFilters() {
  const p = new URLSearchParams();
  const q = $("#q").value.trim(); if (q) p.set("q", q);
  const mf = state.meetingFilters;
  if (mf.group)        p.set("group", mf.group);
  if (mf.purpose)      p.set("purpose", mf.purpose);
  if (mf.attendee)     p.set("attendee", mf.attendee);
  if (mf.dateFrom)     p.set("date_from", mf.dateFrom);
  if (mf.dateTo)       p.set("date_to", mf.dateTo);
  if (mf.hasOpenTasks) p.set("has_open_tasks", "1");
  return p.toString();
}
function tasksFilters() {
  const p = new URLSearchParams();
  const q = $("#q").value.trim(); if (q) p.set("q", q);
  const ty = $("#t-type").value; if (ty) p.set("type", ty);
  const g = $("#t-group").value; if (g) p.set("group", g);
  if ($("#t-overdue").checked) p.set("overdue", "1");
  if ($("#t-snoozed")?.checked) p.set("snoozed", "1");
  const pr = $("#t-priority")?.value; if (pr) p.set("priority", pr);
  return p.toString();
}

// ---------- Render: HOME dashboard ----------
function greetingFor(d) {
  const h = d.getHours();
  if (h < 5)  return "Working late, Will";
  if (h < 12) return "Good morning, Will";
  if (h < 17) return "Good afternoon, Will";
  return "Good evening, Will";
}
function formatTodayLabel(d) {
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

async function renderHome() {
  let s;
  try { s = await api("/api/stats"); } catch (e) { console.error("stats load failed", e); return; }
  state.stats = s;

  const now = new Date();
  $("#hero-greeting").textContent = greetingFor(now);
  $("#hero-date").textContent = formatTodayLabel(now);
  $("#hero-open").textContent = s.open_count;
  $("#hero-overdue").textContent = s.overdue_count;
  $("#hero-due-today").textContent = s.due_today_count;

  const focusEl = $("#hero-focus");
  if (focusEl) {
    const items = s.due_today_top || [];
    if (items.length > 0) {
      focusEl.innerHTML =
        `<div class="focus-label">Due today</div>` +
        `<ul class="focus-list">` +
        items.map((t) => {
          const group = t.group
            ? `<span class="focus-group">${escapeHtml(t.group)}</span>`
            : "";
          return `<li class="focus-item">${escapeHtml(t.text)}${group}</li>`;
        }).join("") +
        `</ul>`;
    } else {
      const pct = s.pct_complete || 0;
      focusEl.innerHTML = `<div class="focus-fallback">${pct}% done this week</div>`;
    }
  }

  const nt = $("#nav-tasks-count");
  if (nt) nt.textContent = s.open_count > 0 ? String(s.open_count) : "";

  const strip = $("#deadlines-strip");
  const maxCount = Math.max(0, ...s.deadlines.map((d) => d.count));
  const totalNext = s.deadlines.reduce((sum, d) => sum + d.count, 0);
  $("#deadlines-subtitle").textContent =
    totalNext === 0 ? "No deadlines in the next 7 days."
    : `${totalNext} deadline${totalNext === 1 ? "" : "s"} in the next 7 days`;
  strip.innerHTML = s.deadlines.map((d) => {
    const cls = ["deadline-day"];
    if (d.is_today) cls.push("today");
    if (d.count > 0) cls.push("has-tasks");
    if (d.count > 0 && d.count === maxCount && maxCount > 0) cls.push("peak");
    return `
      <div class="${cls.join(" ")}" data-date="${d.date}" title="${d.date}${d.count ? ` — ${d.count} task${d.count === 1 ? "" : "s"}` : ""}">
        ${d.count ? `<span class="d-count">${d.count}</span>` : ""}
        <span class="d-num">${d.day}</span>
        <span class="d-dow">${d.dow}</span>
      </div>`;
  }).join("");

  const pct = s.pct_complete || 0;
  const CIRC = 2 * Math.PI * 62;
  const ring = $("#ring-progress");
  if (ring) {
    ring.setAttribute("stroke-dasharray", CIRC.toFixed(2));
    requestAnimationFrame(() => {
      ring.setAttribute("stroke-dashoffset", String(CIRC * (1 - pct / 100)));
    });
  }
  $("#ring-percent").textContent = `${pct}%`;
  const weekDone = s.completions_30d || 0;
  const weekOpen = s.open_count || 0;
  $("#ring-caption").textContent = weekDone + weekOpen === 0
    ? "No tasks this week."
    : `${weekDone} done of ${weekDone + weekOpen} this week`;

  drawSparkline(s.completions_per_day || []);
  $("#spark-total").textContent = s.completions_30d || 0;

  const odCard = $("#card-overdue");
  const odList = $("#overdue-list");
  const odPill = $("#overdue-count-pill");
  if (!s.overdue_top || s.overdue_top.length === 0) {
    if (odCard) odCard.style.display = "none";
  } else {
    if (odCard) odCard.style.display = "";
    odPill.style.display = "";
    odPill.textContent = `${s.overdue_count} total`;
    odList.innerHTML = s.overdue_top.map((t) => {
      const days = t.days_overdue;
      const label = days === 0 ? "today" : `${days}d`;
      const groupLine = t.group ? `<span style="color:var(--muted); font-size:11px;">${escapeHtml(t.group)}</span>` : "";
      return `
        <li data-overdue-id="${escapeHtml(t.id)}">
          <div>
            <div class="od-text">${escapeHtml(t.text)}</div>
            ${groupLine}
          </div>
          <span class="od-days">${label}</span>
        </li>`;
    }).join("");
  }

  const bgEl = $("#group-bars");
  const maxGroup = Math.max(0, ...((s.by_group || []).map((g) => g.count)));
  if (!s.by_group || !s.by_group.length) {
    bgEl.innerHTML = `<div style="color:var(--muted); font-size:13px; text-align:center; padding:18px 0;">No open tasks with a group yet.</div>`;
  } else {
    bgEl.innerHTML = s.by_group.map((g) => {
      const pctW = maxGroup ? Math.round((g.count / maxGroup) * 100) : 0;
      return `
        <div class="group-bar" data-group="${escapeHtml(g.group)}">
          <span class="group-bar-name">${escapeHtml(g.group)}</span>
          <span class="group-bar-num">${g.count}</span>
          <div class="group-bar-fill" style="--pct: ${pctW}%;"></div>
        </div>`;
    }).join("");
  }

  if (s.recent_meeting) {
    const rm = s.recent_meeting;
    $("#recent-title").textContent = rm.group + (rm.topic ? ` — ${rm.topic}` : "");
    $("#recent-meta").textContent = rm.date || "";
    $("#recent-actions").textContent = rm.open_actions || 0;
    $("#recent-reminders").textContent = rm.open_reminders || 0;
    $("#recent-meeting-card").dataset.meetingId = rm.id;
  } else {
    $("#recent-title").textContent = "—";
    $("#recent-meta").textContent = "No meetings yet.";
    $("#recent-actions").textContent = "0";
    $("#recent-reminders").textContent = "0";
    delete $("#recent-meeting-card").dataset.meetingId;
  }

  _renderFocusPanel(s.top_urgency || []);

  // Daily planning: auto-open on first visit each day
  const today = new Date().toISOString().slice(0, 10);
  if (localStorage.getItem("last_plan_date") !== today && s.open_count > 0) {
    openDailyPlan();
  }
}

function drawSparkline(data) {
  const svg = $("#spark-svg");
  if (!svg) return;
  svg.innerHTML = "";
  if (!data.length) return;
  const W = 300, H = 100, PAD = 4;
  const values = data.map((d) => d.count);
  const max = Math.max(1, ...values);
  const step = (W - PAD * 2) / Math.max(1, data.length - 1);
  const pts = values.map((v, i) => {
    const x = PAD + i * step;
    const y = H - PAD - (v / max) * (H - PAD * 2);
    return [x, y];
  });
  let area = `M ${pts[0][0].toFixed(2)} ${H - PAD}`;
  for (const [x, y] of pts) area += ` L ${x.toFixed(2)} ${y.toFixed(2)}`;
  area += ` L ${pts[pts.length - 1][0].toFixed(2)} ${H - PAD} Z`;
  const line = pts.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`).join(" ");
  const lastPt = pts[pts.length - 1];
  svg.innerHTML = `
    <defs>
      <linearGradient id="spark-grad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#2563eb" stop-opacity="0.4"/>
        <stop offset="100%" stop-color="#2563eb" stop-opacity="0"/>
      </linearGradient>
    </defs>
    <path d="${area}" fill="url(#spark-grad)"/>
    <path d="${line}" fill="none" stroke="#2563eb" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${lastPt[0].toFixed(2)}" cy="${lastPt[1].toFixed(2)}" r="3" fill="#2563eb"/>
  `;
}

// ---------- Render: Tasks ----------
const _URGENCY_KW_RE = /\b(?:urgent|urgently|asap|a\.s\.a\.p|critical|blocker|blocking|immediately|right away|p[01]|high priority|top priority|must do|must complete|mandatory|required|eod|end of day)\b/i;

function _hasUrgencySignals(t) {
  if (t.priority === "high") return false;
  if (t.overdue) return true;
  if (_URGENCY_KW_RE.test(t.text)) return true;
  if (typeof t.urgency_score === "number" && t.urgency_score >= 150) return true;
  return false;
}

function _taskRow(t, i, paper) {
  const chips = [];
  chips.push(`<span class="chip type-${t.type}">${escapeHtml(t.type)}</span>`);
  if (t.group) chips.push(`<span class="chip group">${escapeHtml(t.group)}</span>`);
  if (t.contact) chips.push(`<span class="chip contact">${escapeHtml(t.contact)}</span>`);
  if (t.deadline) {
    const cls = t.overdue ? "chip deadline overdue" : "chip deadline";
    chips.push(`<span class="${cls}">${escapeHtml(t.deadline)}</span>`);
  }
  if (t.priority === "high") {
    chips.push(`<span class="chip priority-high">▲ high</span>`);
  } else if (t.priority === "low") {
    chips.push(`<span class="chip priority-low">▽ low</span>`);
  } else if (_hasUrgencySignals(t)) {
    chips.push(`<span class="chip urgency-auto">~ urgent</span>`);
  }
  if (t.backburner) chips.push(`<span class="chip bb">💤 backburner</span>`);
  if (t.estimate_minutes) {
    const est = t.estimate_minutes < 60
      ? `${t.estimate_minutes}m`
      : `${(t.estimate_minutes / 60).toFixed(1).replace(/\.0$/, "")}h`;
    chips.push(`<span class="chip estimate">⏱ ${est}</span>`);
  }
  if (t.recurrence_rule) {
    const rtype = t.recurrence_rule.type || "";
    const label = { daily: "↻ daily", weekly: "↻ weekly", monthly: "↻ monthly", after_completion: "↻ on done" }[rtype] || "↻ recurring";
    chips.push(`<span class="chip recur">${label}</span>`);
  }
  if (t.subtask_count > 0) {
    chips.push(`<span class="chip subtasks" data-subtask-toggle="${t.id}" style="cursor:pointer;">▸ ${t.subtask_count} subtask${t.subtask_count > 1 ? "s" : ""}</span>`);
  }
  if (t.has_blockers) chips.push(`<span class="chip blocked">🔗 blocked</span>`);
  if (t.snoozed_until) chips.push(`<span class="chip snoozed">💤 until ${escapeHtml(t.snoozed_until)}</span>`);

  const source = t.type === "free"
    ? `<span>Free-form</span>`
    : (t.source_date || "") + " · " + escapeHtml(t.source_filename.replace(/\.md$/, "").replace(/^\d{4}-\d{2}-\d{2} - /, ""));

  const classes = ["task"];
  if (t.done) classes.push("done");
  if (t.overdue && !t.done) classes.push("overdue");
  if (t.backburner) classes.push("backburner");
  if (paper === state.paperOrder[0] && i === state.selectedTaskIdx) classes.push("selected");

  const bbIcon = t.backburner ? "☀️" : "💤";
  const bbTitle = t.backburner ? "Bring back to main list" : "Send to backburner";
  const bbActive = t.backburner ? " active" : "";

  return `
    <li class="${classes.join(" ")}" data-idx="${i}" data-paper="${paper}" data-task-id="${t.id}">
      <span class="checkbox action-toggle" title="${t.done ? "Mark open" : "Mark done"}"></span>
      <div class="main">
        <div class="text">${escapeHtml(t.text)}</div>
        <div class="meta">${chips.join("")}</div>
        <ul class="subtask-list hidden" id="subtasks-${t.id}"></ul>
      </div>
      <div class="source">${source}</div>
      <div class="actions">
        <button class="icon-btn action-bb${bbActive}" title="${bbTitle}">${bbIcon}</button>
      </div>
    </li>`;
}

function renderTasks() {
  $$(".paper").forEach((el) => {
    el.classList.remove("pos-0", "pos-1", "pos-2");
    const pos = state.paperOrder.indexOf(el.dataset.paper);
    if (pos >= 0) el.classList.add(`pos-${pos}`);
  });

  ["active", "backburner", "done"].forEach((paper) => {
    const tasks = state.tasksByStatus[paper];
    const ul = $(`ul[data-paper-list="${paper}"]`);
    const summaryEl = $(`#tasks-summary-${paper}`);
    const peekCount = $(`[data-peek-count="${paper}"]`);

    if (peekCount) peekCount.textContent = tasks.length;

    if (!tasks.length) {
      ul.innerHTML = `<li class="empty">No tasks.</li>`;
      if (summaryEl) summaryEl.innerHTML = `<span><strong>0</strong> tasks</span>`;
      return;
    }

    const overdue = tasks.filter((t) => t.overdue && !t.done).length;
    const labels = { active: "open", backburner: "backburner", done: "done" };
    if (summaryEl) summaryEl.innerHTML = `
      <span><strong>${tasks.length}</strong> ${labels[paper]}</span>
      ${overdue ? `<span class="pill overdue">${overdue} overdue</span>` : ""}
    `;
    ul.innerHTML = tasks.map((t, i) => _taskRow(t, i, paper)).join("");
  });

  const gSel = $("#t-group");
  const current = gSel.value;
  const opts = [`<option value="">All groups</option>`]
    .concat(state.tasksGroupsInScope.map((g) => `<option value="${escapeHtml(g)}">${escapeHtml(g)}</option>`));
  gSel.innerHTML = opts.join("");
  if (current && state.tasksGroupsInScope.includes(current)) gSel.value = current;
  else gSel.value = "";
}

function bringToFront(paperName) {
  const idx = state.paperOrder.indexOf(paperName);
  if (idx <= 0) return;
  state.paperOrder = [paperName, ...state.paperOrder.filter((p) => p !== paperName)];
  state.selectedTaskIdx = -1;
  renderTasks();
}

async function refreshTasks() {
  const qs = tasksFilters();
  const [activeData, bbData, doneData] = await Promise.all([
    api("/api/tasks?status=open&" + qs),
    api("/api/tasks?status=backburner&" + qs),
    api("/api/tasks?status=done&" + qs),
  ]);
  state.tasksByStatus.active = activeData.tasks;
  state.tasksByStatus.backburner = bbData.tasks;
  state.tasksByStatus.done = doneData.tasks;
  state.tasksGroupsInScope = activeData.groups_in_scope || [];

  const frontTasks = state.tasksByStatus[state.paperOrder[0]];
  if (state.selectedTaskIdx >= frontTasks.length) state.selectedTaskIdx = -1;

  renderTasks();
}

const refreshTasksDebounced = debounce(refreshTasks, 100);

async function toggleTaskDone(task) {
  const newDone = !task.done;
  await api("/api/tasks/toggle", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: task.id, done: newDone }),
  });
  closeDrawer();
  if (newDone) {
    showUndoToast(task);
    const li = document.querySelector(`li[data-task-id="${task.id}"]`);
    if (li) {
      li.classList.add("task-completing");
      await new Promise(r => setTimeout(r, 380));
      li.classList.add("task-exit");
      await new Promise(r => setTimeout(r, 230));
    }
  }
  await refreshTasks();
  if (state.tab === "smart") loadSmartView(state.smartView);
  if (state.meetings.length) refreshMeetings();
  if (state.tab === "home") renderHome();
}

async function toggleTaskBackburner(task) {
  await api("/api/tasks/backburner", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: task.id, backburner: !task.backburner }),
  });
  await refreshTasks();
  if (state.tab === "home") renderHome();
}

async function setPriority(task, priority) {
  await api("/api/tasks/priority", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: task.id, priority }),
  });
  await refreshTasks();
}

async function cyclePriority(task) {
  const order = ["high", "normal", "low"];
  const next = order[(order.indexOf(task.priority ?? "normal") + 1) % 3];
  await setPriority(task, next);
}

async function deleteTask(task) {
  await api("/api/tasks/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: task.id }),
  });
  await refreshTasks();
  if (state.meetings.length) refreshMeetings();
  if (state.tab === "home") renderHome();
}

// ---------- Undo toast ----------
let _undoTimer = null;

function showUndoToast(task) {
  clearTimeout(_undoTimer);
  const container = $("#toast-container");
  container.innerHTML = `
    <div class="toast">
      <span class="toast-msg">Marked complete.</span>
      <button class="toast-undo" id="toast-undo-btn">Undo</button>
      <button class="toast-dismiss" id="toast-dismiss-btn">×</button>
    </div>
  `;
  container.classList.add("visible");

  $("#toast-undo-btn").addEventListener("click", async () => {
    clearTimeout(_undoTimer);
    container.classList.remove("visible");
    await api("/api/tasks/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        id: task.id,
        done: false,
      }),
    });
    await refreshTasks();
    if (state.tab === "home") renderHome();
  });

  $("#toast-dismiss-btn").addEventListener("click", () => {
    clearTimeout(_undoTimer);
    container.classList.remove("visible");
  });

  _undoTimer = setTimeout(() => container.classList.remove("visible"), 6000);
}

// ---------- Context menu ----------
let _ctxTask = null;

function openContextMenu(e, task) {
  e.preventDefault();
  _ctxTask = task;
  const menu = $("#ctx-menu");

  const isDone = task.done;
  const hasNote = !!task.meeting_id;
  const isBb = task.backburner;

  menu.innerHTML = `
    <div class="ctx-item" data-action="toggle-done">${isDone ? "✓ Mark as open" : "✓ Mark as complete"}</div>
    <div class="ctx-item" data-action="edit">✏︎ Edit</div>
    <div class="ctx-divider"></div>
    ${hasNote ? `<div class="ctx-item" data-action="view-note">↗ View meeting note</div>` : ""}
    <div class="ctx-item" data-action="backburner">${isBb ? "☀ Move to active" : "☁ Send to backburner"}</div>
    <div class="ctx-item" data-action="snooze">💤 Snooze until…</div>
    ${!task.parent_id ? `<div class="ctx-item" data-action="add-subtask">⊕ Add subtask</div>` : ""}
    <div class="ctx-item" data-action="add-blocker">🔗 Block on…</div>
    <div class="ctx-item ctx-has-sub">
      <span>⬆ Priority</span>
      <span class="ctx-arrow">›</span>
      <div class="ctx-submenu">
        <div class="ctx-item${task.priority === "high" ? " ctx-active" : ""}" data-action="set-priority" data-priority="high">▲ High</div>
        <div class="ctx-item${(!task.priority || task.priority === "normal") ? " ctx-active" : ""}" data-action="set-priority" data-priority="normal">— Normal</div>
        <div class="ctx-item${task.priority === "low" ? " ctx-active" : ""}" data-action="set-priority" data-priority="low">▽ Low</div>
      </div>
    </div>
    <div class="ctx-divider"></div>
    <div class="ctx-item ctx-danger" data-action="delete">Delete task</div>
  `;

  // Position: keep within viewport
  const menuW = 200, menuH = 210;
  let x = e.clientX, y = e.clientY;
  if (x + menuW > window.innerWidth)  x = window.innerWidth - menuW - 8;
  if (y + menuH > window.innerHeight) y = window.innerHeight - menuH - 8;

  menu.style.left = x + "px";
  menu.style.top  = y + "px";
  menu.classList.remove("hidden");
  menu.classList.add("visible");
}

function closeContextMenu() {
  const menu = $("#ctx-menu");
  menu.classList.remove("visible");
  menu.classList.add("hidden");
  _ctxTask = null;
}

document.addEventListener("click", (e) => {
  if (!e.target.closest("#ctx-menu")) closeContextMenu();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeContextMenu();
}, true);

// ---------- Edit modal ----------
let _editTask = null;

function openEditModal(task) {
  _editTask = task;
  $("#edit-m-text").value = task.text;
  if ($("#edit-m-priority")) $("#edit-m-priority").value = task.priority || "normal";

  // Populate group datalist
  const list = $("#edit-m-group-list");
  if (list) {
    list.innerHTML = state.tasksGroupsInScope.concat(state.facets.groups)
      .filter((v, i, a) => a.indexOf(v) === i)
      .map((g) => `<option value="${escapeHtml(g)}"></option>`).join("");
  }
  if ($("#edit-m-group")) $("#edit-m-group").value = task.group || "";
  if ($("#edit-m-contact")) $("#edit-m-contact").value = task.contact || "";
  if ($("#edit-m-estimate")) $("#edit-m-estimate").value = task.estimate_minutes || "";
  if ($("#edit-m-recur")) {
    _updateRecurDetail("edit-m", task.recurrence_rule || null);
  }

  // Populate year select and deadline
  const yearSel = $("#edit-m-dl-year");
  if (yearSel) {
    const thisYear = new Date().getFullYear();
    yearSel.innerHTML = `<option value="">Year</option>` +
      [thisYear - 1, thisYear, thisYear + 1, thisYear + 2]
        .map((y) => `<option value="${y}">${y}</option>`).join("");
  }
  if (task.deadline && task.deadline.match(/^\d{4}-\d{2}-\d{2}$/)) {
    const [yy, mo, dd] = task.deadline.split("-");
    if ($("#edit-m-dl-month")) $("#edit-m-dl-month").value = mo;
    if ($("#edit-m-dl-day"))   $("#edit-m-dl-day").value = dd;
    if ($("#edit-m-dl-year"))  $("#edit-m-dl-year").value = yy;
  } else {
    ["edit-m-dl-month", "edit-m-dl-day", "edit-m-dl-year"].forEach((id) => {
      const el = $("#" + id); if (el) el.value = "";
    });
  }

  $("#edit-modal-backdrop").classList.remove("hidden");
  setTimeout(() => {
    const inp = $("#edit-m-text");
    inp.focus();
    inp.setSelectionRange(inp.value.length, inp.value.length);
  }, 10);
}

function closeEditModal() {
  $("#edit-modal-backdrop").classList.add("hidden");
  _editTask = null;
}

async function submitEditModal() {
  if (!_editTask) return;
  const newText = $("#edit-m-text").value.trim();
  if (!newText) { $("#edit-m-text").focus(); return; }
  const newPriority = $("#edit-m-priority")?.value || "normal";
  const newGroup = $("#edit-m-group")?.value.trim() || null;
  const newDeadline = getDeadlineValue("edit-m") || null;
  const newContact = $("#edit-m-contact")?.value.trim() ?? null;
  const estimateVal = parseInt($("#edit-m-estimate")?.value);
  await api("/api/tasks/edit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source_filename: _editTask.source_filename,
      section: _editTask.section,
      old_text: _editTask.text,
      new_text: newText,
      priority: newPriority,
      group: newGroup,
      deadline_direct: newDeadline,
      contact: newContact,
      estimate_minutes: isNaN(estimateVal) ? null : estimateVal,
      recurrence_rule: getRecurrenceRule("edit-m"),
    }),
  });
  closeEditModal();
  await refreshTasks();
  if (state.meetings.length) refreshMeetings();
}

// ---------- Source-note drawer ----------
function closeDrawer() {
  const bd = $("#drawer-backdrop");
  bd.classList.remove("visible");
  bd.classList.add("hidden");
  state.drawerTask = null;
}

async function openDrawer(task) {
  state.drawerTask = task;
  const el = $("#drawer-content");
  const bd = $("#drawer-backdrop");
  bd.classList.remove("hidden");
  requestAnimationFrame(() => bd.classList.add("visible"));

  if (task.type === "free") {
    el.innerHTML = `
      <header>
        <h1>Free-form task</h1>
        <div class="meta">
          ${task.group ? `<span>${escapeHtml(task.group)}</span>` : ""}
          ${task.deadline ? `<span>⏰ ${escapeHtml(task.deadline)}</span>` : ""}
        </div>
      </header>
      <div class="free-info">
        <p>This task isn't tied to a meeting note. It lives in <strong>tasks.md</strong>.</p>
        <p style="margin-top:10px; color:var(--muted); font-size:12px;">
          Raw: <code>${escapeHtml(task.text)}</code>
        </p>
      </div>
    `;
    return;
  }
  if (!task.meeting_id) {
    el.innerHTML = `<div class="detail-empty">No source meeting linked.</div>`;
    return;
  }
  el.innerHTML = `<div class="detail-empty">Loading…</div>`;
  try {
    const m = await api(`/api/meetings/${task.meeting_id}`);
    const meta = [];
    if (m.date)          meta.push(`<span>${escapeHtml(m.date)}</span>`);
    if (m.purpose?.length) meta.push(`<span>${escapeHtml(m.purpose.join(" · "))}</span>`);
    if (m.attendees)     meta.push(`<span>👥 ${escapeHtml(m.attendees)}</span>`);
    if (m.outcome)       meta.push(`<span>→ ${escapeHtml(m.outcome)}</span>`);
    el.innerHTML = `
      <header>
        <h1>${escapeHtml(m.group)}${m.topic ? ` — <span style="color:var(--muted); font-weight:400">${escapeHtml(m.topic)}</span>` : ""}</h1>
        <div class="meta">${meta.join("")}</div>
        <a href="#" class="open-full" data-mid="${m.id}">Open full meeting view →</a>
      </header>
      <div class="body">${m.body_html}</div>
    `;
  } catch (e) {
    el.innerHTML = `<div class="detail-empty">Couldn't load source note.</div>`;
  }
}

// ---------- Add-task modal ----------
function getDeadlineValue(prefix = "m") {
  const mo = $(`#${prefix}-dl-month`).value;
  const dd = $(`#${prefix}-dl-day`).value;
  const yy = $(`#${prefix}-dl-year`).value;
  if (!mo || !dd || !yy) return "";
  return `${yy}-${mo}-${dd}`;
}

function setDeadlineSelects(date, prefix = "m") {
  const mo = String(date.getMonth() + 1).padStart(2, "0");
  const dd = String(date.getDate()).padStart(2, "0");
  const yy = String(date.getFullYear());
  $(`#${prefix}-dl-month`).value = mo;
  $(`#${prefix}-dl-day`).value = dd;
  $(`#${prefix}-dl-year`).value = yy;
}

function _nextWeekday(targetDay) {
  const today = new Date();
  const d = new Date(today);
  const curDay = today.getDay(); // 0=Sun, 1=Mon, ..., 5=Fri, 6=Sat
  let diff = targetDay - curDay;
  if (diff <= 0) diff += 7;
  d.setDate(today.getDate() + diff);
  return d;
}

function _thisFriday() {
  const today = new Date();
  const curDay = today.getDay();
  if (curDay === 6) { // Saturday → next Friday
    const d = new Date(today); d.setDate(today.getDate() + 6); return d;
  }
  const diff = curDay === 0 ? 5 : 5 - curDay; // Sunday=5 ahead, else days to Fri
  const d = new Date(today);
  d.setDate(today.getDate() + (diff <= 0 ? 7 + diff : diff));
  return d;
}

// ---------- Recurrence helpers ----------
function _updateRecurDetail(prefix, rule) {
  const sel = $(`#${prefix}-recur`);
  if (sel) sel.value = rule?.type || "";
  const detail = $(`#${prefix}-recur-detail`);
  if (!detail) return;
  const type = rule?.type || "";
  if (!type) { detail.classList.add("hidden"); detail.innerHTML = ""; return; }
  detail.classList.remove("hidden");
  const days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  if (type === "daily") {
    detail.innerHTML = `<label>Every <input type="number" id="${prefix}-recur-interval" value="${rule?.interval || 1}" min="1" max="90" style="width:50px"> days</label>`;
  } else if (type === "weekly") {
    detail.innerHTML = `<label>Every <input type="number" id="${prefix}-recur-interval" value="${rule?.interval || 1}" min="1" max="52" style="width:50px"> week(s) on
      <select id="${prefix}-recur-day">${days.map((d, i) => `<option value="${i}"${rule?.day_of_week === i ? " selected" : ""}>${d}</option>`).join("")}</select></label>`;
  } else if (type === "monthly") {
    detail.innerHTML = `<label>On day <input type="number" id="${prefix}-recur-day" value="${rule?.day_of_month || 1}" min="1" max="31" style="width:50px"> of the month</label>`;
  } else if (type === "after_completion") {
    detail.innerHTML = `<label><input type="number" id="${prefix}-recur-days" value="${rule?.days || 7}" min="1" max="365" style="width:50px"> days after completion</label>`;
  }
}

function getRecurrenceRule(prefix) {
  const sel = $(`#${prefix}-recur`);
  if (!sel || !sel.value) return null;
  const type = sel.value;
  if (type === "daily") {
    return { type, interval: parseInt($(`#${prefix}-recur-interval`)?.value) || 1 };
  } else if (type === "weekly") {
    return { type, interval: parseInt($(`#${prefix}-recur-interval`)?.value) || 1, day_of_week: parseInt($(`#${prefix}-recur-day`)?.value) || 0 };
  } else if (type === "monthly") {
    return { type, day_of_month: parseInt($(`#${prefix}-recur-day`)?.value) || 1 };
  } else if (type === "after_completion") {
    return { type, days: parseInt($(`#${prefix}-recur-days`)?.value) || 7 };
  }
  return null;
}

// ---------- NL Morphing Add-task modal ----------
function _clientExtractDeadline(text) {
  const t = text.toLowerCase();
  const today = new Date();
  const iso = (d) => d.toISOString().slice(0, 10);
  const addDays = (n) => { const d = new Date(today); d.setDate(d.getDate() + n); return d; };

  if (/\btomorrow\b/.test(t)) return iso(addDays(1));
  const inN = t.match(/\bin (\d+) days?\b/);
  if (inN) return iso(addDays(parseInt(inN[1])));
  if (/\bend of (this )?week\b/.test(t)) return iso(_thisFriday());
  if (/\bnext week\b/.test(t)) return iso(_thisFriday());
  const dayNames = ["sunday","monday","tuesday","wednesday","thursday","friday","saturday"];
  const nextDay = t.match(/\bnext (sunday|monday|tuesday|wednesday|thursday|friday|saturday)\b/);
  if (nextDay) return iso(_nextWeekday(dayNames.indexOf(nextDay[1])));
  return null;
}

function parseNLTask(text) {
  const result = { text, priority: "normal", contact: null, phone: null, email: null, deadline: null, group: null, estimate_minutes: null };

  if (/\b(urgent|urgently|asap|a\.s\.a\.p\.?|critical|immediately|must do|p[01])\b/i.test(text)) {
    result.priority = "high";
  }

  const contactM = text.match(/\b(?:call|email|meet|text|ping)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)/);
  if (contactM) result.contact = contactM[1];

  const phoneM = text.match(/\b(\d{3}[-.\s]?\d{3}[-.\s]?\d{4})\b/);
  if (phoneM) result.phone = phoneM[1];

  const emailM = text.match(/\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b/);
  if (emailM) result.email = emailM[0];

  const estM = text.match(/\b(\d+(?:\.\d+)?)\s*(min(?:utes?)?|h(?:ours?)?)\b/i);
  if (estM) {
    const val = parseFloat(estM[1]);
    result.estimate_minutes = /h/i.test(estM[2]) ? Math.round(val * 60) : Math.round(val);
  }

  result.deadline = _clientExtractDeadline(text);

  const allGroups = state.tasksGroupsInScope.concat(state.facets.groups).filter((v, i, a) => a.indexOf(v) === i);
  for (const g of allGroups) {
    if (text.toLowerCase().includes(g.toLowerCase())) {
      result.group = g;
      result.groupUncertain = true;
      break;
    }
  }

  return result;
}

function _populateNLStep2(parsed) {
  const fields = [];
  fields.push({ id: "nl-f-priority", label: "Priority", type: "select", value: parsed.priority, options: [["normal","Normal"],["high","High"],["low","Low"]] });
  fields.push({ id: "nl-f-deadline", label: "Deadline", type: "date", value: parsed.deadline || "" });
  fields.push({ id: "nl-f-group", label: "Group", type: "text", value: parsed.group || "", uncertain: parsed.groupUncertain });
  const contactVal = [parsed.email, parsed.phone, parsed.contact].filter(Boolean).join("  ");
  if (contactVal) {
    fields.push({ id: "nl-f-contact", label: "Contact method", type: "text", value: contactVal });
  }
  if (parsed.estimate_minutes) {
    fields.push({ id: "nl-f-estimate", label: "Estimate (min)", type: "number", value: parsed.estimate_minutes });
  }

  const container = $("#nl-parsed-fields");
  container.innerHTML = fields.map((f, i) => {
    const uncertain = f.uncertain ? " nl-field-uncertain" : "";
    let input;
    if (f.type === "select") {
      input = `<select id="${f.id}">${f.options.map(([v, l]) => `<option value="${v}"${v === f.value ? " selected" : ""}>${l}</option>`).join("")}</select>`;
    } else {
      input = `<input id="${f.id}" type="${f.type}" value="${escapeHtml(String(f.value || ""))}" autocomplete="off">`;
    }
    return `<div class="nl-field nl-field-blur${uncertain}" style="transition-delay:${i * 55}ms">
      <label>${f.label}${f.uncertain ? ' <span class="nl-check-this">check this</span>' : ""}</label>
      ${input}
    </div>`;
  }).join("");

  // Expand container, then unblur fields staggered
  requestAnimationFrame(() => {
    container.classList.add("nl-fields-visible");
    requestAnimationFrame(() => {
      container.querySelectorAll(".nl-field-blur").forEach((el) => el.classList.remove("nl-field-blur"));
    });
  });
}

let _nlParsed = null;

function openNLModal() {
  _nlParsed = null;
  const ta = $("#nl-text");
  ta.value = "";
  ta.readOnly = false;
  ta.classList.remove("nl-text-blurred");
  $("#nl-hint").textContent = "";
  const container = $("#nl-parsed-fields");
  container.innerHTML = "";
  container.classList.remove("nl-fields-visible");
  $("#nl-footer-step1").classList.remove("hidden");
  $("#nl-footer-step2").classList.add("hidden");
  $("#nl-modal-backdrop").classList.remove("hidden");
  setTimeout(() => ta.focus(), 10);
}
function closeNLModal() { $("#nl-modal-backdrop").classList.add("hidden"); }

function _nlGoBack() {
  const ta = $("#nl-text");
  const container = $("#nl-parsed-fields");
  // Blur fields briefly, then collapse
  container.querySelectorAll(".nl-field").forEach((el) => el.classList.add("nl-field-blur"));
  setTimeout(() => {
    container.classList.remove("nl-fields-visible");
    container.innerHTML = "";
    ta.readOnly = false;
    ta.classList.remove("nl-text-blurred");
    $("#nl-footer-step2").classList.add("hidden");
    $("#nl-footer-step1").classList.remove("hidden");
    ta.focus();
  }, 180);
}

function _nlTransitionToStep2() {
  const text = $("#nl-text").value.trim();
  if (!text) { $("#nl-text").focus(); return; }
  _nlParsed = parseNLTask(text);

  const ta = $("#nl-text");
  ta.readOnly = true;
  // Blur the textarea
  ta.classList.add("nl-text-blurred");
  $("#nl-hint").textContent = "";

  setTimeout(() => {
    // Swap footers while still blurred
    $("#nl-footer-step1").classList.add("hidden");
    $("#nl-footer-step2").classList.remove("hidden");
    // Populate + expand extra fields (they start blurred and unblur staggered)
    _populateNLStep2(_nlParsed);
    // Unblur the textarea
    ta.classList.remove("nl-text-blurred");
  }, 210);
}

async function submitNLModal() {
  if (!_nlParsed) return;
  const text = $("#nl-text").value.trim();
  if (!text) return;
  const priority = $("#nl-f-priority")?.value || "normal";
  const deadline = $("#nl-f-deadline")?.value || "";
  const group = $("#nl-f-group")?.value.trim() || "";
  const contact = $("#nl-f-contact")?.value.trim() || null;
  const estimateRaw = parseInt($("#nl-f-estimate")?.value);
  const estimate_minutes = isNaN(estimateRaw) ? null : estimateRaw;

  await api("/api/tasks/add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, group, deadline, priority, contact, estimate_minutes }),
  });
  closeNLModal();
  if (state.tab === "tasks") await refreshTasks();
  if (state.tab === "home")  await renderHome();
  await loadFacets();
}

// ---------- (legacy alias — kept for any old references) ----------
const openAddModal = openNLModal;
const closeAddModal = closeNLModal;

// ---------- Render: Meetings ----------
function renderMeetingsList() {
  const ul = $("#meetings");
  if (!state.meetings.length) {
    ul.innerHTML = `<li style="cursor:default; color:var(--muted); padding:40px 16px; text-align:center; display:block;">No meetings match your filters.</li>`;
    return;
  }
  ul.innerHTML = state.meetings.map((m) => {
    const date = m.date || "—";
    const badges = [];
    if (m.open_action_items_count) badges.push(`<span class="badge actions" title="Open action items">${m.open_action_items_count}A</span>`);
    if (m.open_reminders_count) badges.push(`<span class="badge reminders" title="Open reminders">${m.open_reminders_count}R</span>`);
    const active = m.id === state.selectedMeetingId ? "active" : "";
    return `
      <li data-id="${m.id}" class="${active}">
        <span class="date">${escapeHtml(date)}</span>
        <span class="group">${escapeHtml(m.group)}</span>
        <span class="badges">${badges.join("")}</span>
      </li>`;
  }).join("");
}

function renderDetail(m) {
  if (!m) {
    $("#detail").innerHTML = `<div class="detail-empty">Select a meeting to see the full note.</div>`;
    return;
  }
  const meta = [];
  if (m.date)          meta.push(`<span>${escapeHtml(m.date)}</span>`);
  if (m.purpose?.length) meta.push(`<span>${escapeHtml(m.purpose.join(" · "))}</span>`);
  if (m.attendees)     meta.push(`<span>👥 ${escapeHtml(m.attendees)}</span>`);
  if (m.deadline)      meta.push(`<span>⏰ ${escapeHtml(m.deadline)}</span>`);
  if (m.outcome)       meta.push(`<span>→ ${escapeHtml(m.outcome)}</span>`);
  if (m.raw_group && m.raw_group !== m.group)
    meta.push(`<span style="color:var(--muted)"><em>raw: ${escapeHtml(m.raw_group)}</em></span>`);

  const listOrNone = (items) =>
    items?.length
      ? `<ul>${items.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul>`
      : `<p style="color:var(--muted); margin:4px 0 8px;">None</p>`;

  $("#detail").innerHTML = `
    <header>
      <h1>${escapeHtml(m.group)}${m.topic ? ` — <span style="color:var(--muted); font-weight:400">${escapeHtml(m.topic)}</span>` : ""}</h1>
      <div class="meta">${meta.join("")}</div>
    </header>
    <div class="tasks-panel">
      <h3>Open Action Items</h3>
      ${listOrNone(m.action_items_open)}
      <h3>Open Reminders</h3>
      ${listOrNone(m.reminders_open)}
    </div>
    <div class="body">${m.body_html}</div>
  `;
}

function renderGroupsTable(groups) {
  const tbody = $("#groups-body");
  if (!groups.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--muted); padding:30px;">No groups yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = groups.map((g) => `
    <tr data-group="${escapeHtml(g.group)}">
      <td><strong>${escapeHtml(g.group)}</strong></td>
      <td class="num">${g.meeting_count}</td>
      <td>${g.last_contact ? escapeHtml(g.last_contact) : "—"}</td>
      <td class="num">${g.open_action_items || ""}</td>
      <td class="num">${g.open_reminders || ""}</td>
      <td class="variants">${g.raw_variants?.length ? escapeHtml(g.raw_variants.join(" · ")) : ""}</td>
    </tr>
  `).join("");
}

// ---------- Radial menu ----------
const TRIG_CX = 35;   // trigger center x in view-meetings coords (14 + 21)
const TRIG_CY = 35;   // trigger center y
const MAIN_RADIUS = 138; // needs ≥138px for 44px circles at 20° steps with 4px gap
const SUB_RADIUS  = 95;

const RADIAL_MAIN = [
  { name: "group",      angle: -10 },
  { name: "purpose",    angle:  10 },
  { name: "attendee",   angle:  30 },
  { name: "date",       angle:  50 },
  { name: "open-tasks", angle:  70 },
  { name: "import",     angle:  90 },
];

function initRadialPositions() {
  RADIAL_MAIN.forEach(({ name, angle }, i) => {
    const rad = angle * Math.PI / 180;
    const tx = TRIG_CX + MAIN_RADIUS * Math.cos(rad);
    const ty = TRIG_CY + MAIN_RADIUS * Math.sin(rad);
    const el = $(`[data-radial="${name}"]`);
    if (!el) return;
    el.style.setProperty("--tx", tx.toFixed(1) + "px");
    el.style.setProperty("--ty", ty.toFixed(1) + "px");
    el.style.setProperty("--ox", TRIG_CX + "px");
    el.style.setProperty("--oy", TRIG_CY + "px");
    el.style.transitionDelay = (i * 32) + "ms";
  });
}

let _subTimer = null;
let _openSubName = null;

function openRadial() {
  const root = $("#radial-root");
  if (!root) return;
  // Restore stagger-in delays
  RADIAL_MAIN.forEach(({ name }, i) => {
    const el = $(`[data-radial="${name}"]`);
    if (el) el.style.transitionDelay = (i * 32) + "ms";
  });
  root.classList.add("open");
}

function closeRadial() {
  const root = $("#radial-root");
  if (!root) return;
  closeRadialSub();
  // Collapse all at once (no stagger on close)
  RADIAL_MAIN.forEach(({ name }) => {
    const el = $(`[data-radial="${name}"]`);
    if (el) el.style.transitionDelay = "0ms";
  });
  root.classList.remove("open");
}

function toggleRadial() {
  const root = $("#radial-root");
  if (!root) return;
  if (root.classList.contains("open")) closeRadial();
  else openRadial();
}

function openRadialSub(name) {
  clearTimeout(_subTimer);
  if (_openSubName === name) return;
  closeRadialSub(false);
  _openSubName = name;

  if (name === "group" || name === "purpose") {
    const values = name === "group" ? state.facets.groups : state.facets.purposes;
    if (!values.length) return;
    buildSubRing(name);
    const sub = $(`#rsub-${name}`);
    if (sub) requestAnimationFrame(() => sub.classList.add("open"));
  } else if (name === "attendee" || name === "date") {
    const pop = $(`#rpop-${name}`);
    if (!pop) return;
    const parentAngle = RADIAL_MAIN.find((m) => m.name === name)?.angle ?? 30;
    positionPopupNear(pop, parentAngle);
    pop.classList.add("open");
    if (name === "attendee") setTimeout(() => $("#rpop-attendee-input")?.focus(), 60);
  }
}

function closeRadialSub(clearState = true) {
  $$(".radial-sub-ring").forEach((el) => el.classList.remove("open"));
  $$(".radial-popup").forEach((el) => el.classList.remove("open"));
  if (clearState) _openSubName = null;
}

function buildSubRing(filterName) {
  const parentConfig = RADIAL_MAIN.find((m) => m.name === filterName);
  if (!parentConfig) return;

  const parentRad = parentConfig.angle * Math.PI / 180;
  const px = TRIG_CX + MAIN_RADIUS * Math.cos(parentRad);
  const py = TRIG_CY + MAIN_RADIUS * Math.sin(parentRad);

  const allValues = filterName === "group" ? state.facets.groups : state.facets.purposes;
  const values = allValues.slice(0, 10); // cap at 10 to avoid overlap
  const activeValue = state.meetingFilters[filterName] || "";

  const maxSpan = Math.min(110, Math.max(0, (values.length - 1) * 24));
  const spanEach = values.length > 1 ? maxSpan / (values.length - 1) : 0;
  const startAngle = parentConfig.angle - maxSpan / 2;

  const container = $(`#rsub-${filterName}`);
  if (!container) return;

  container.innerHTML = values.map((val, i) => {
    const a = (startAngle + i * spanEach) * Math.PI / 180;
    const tx = px + SUB_RADIUS * Math.cos(a);
    const ty = py + SUB_RADIUS * Math.sin(a);
    const isActive = val === activeValue;
    return `<button class="radial-sub-item${isActive ? " active" : ""}"
      data-sub-filter="${filterName}" data-value="${escapeHtml(val)}"
      style="--tx:${tx.toFixed(1)}px;--ty:${ty.toFixed(1)}px;--ox:${px.toFixed(1)}px;--oy:${py.toFixed(1)}px;transition-delay:${i * 22}ms"
    >${escapeHtml(val)}</button>`;
  }).join("");
}

function positionPopupNear(popupEl, parentAngle) {
  const rad = parentAngle * Math.PI / 180;
  const px = TRIG_CX + MAIN_RADIUS * Math.cos(rad);
  const py = TRIG_CY + MAIN_RADIUS * Math.sin(rad);
  popupEl.style.left = (px + 46) + "px";
  popupEl.style.top  = Math.max(4, py - 18) + "px";
}

function updateRadialFilterState() {
  const mf = state.meetingFilters;
  const hasAny = !!(mf.group || mf.purpose || mf.attendee || mf.dateFrom || mf.dateTo || mf.hasOpenTasks);

  // Active dot: purely visual, hides when clear btn is visible (they occupy same spot)
  const dot = $("#radial-active-dot");
  if (dot) dot.classList.toggle("visible", false); // clear btn replaces dot when active

  // Clear-all badge: shows only when filters are active
  const clearBtn = $("#radial-clear-btn");
  if (clearBtn) clearBtn.hidden = !hasAny;

  $('[data-radial="group"]')?.classList.toggle("filtered", !!mf.group);
  $('[data-radial="purpose"]')?.classList.toggle("filtered", !!mf.purpose);
  $('[data-radial="attendee"]')?.classList.toggle("filtered", !!mf.attendee);
  $('[data-radial="date"]')?.classList.toggle("filtered", !!(mf.dateFrom || mf.dateTo));
  $('[data-radial="open-tasks"]')?.classList.toggle("toggled", !!mf.hasOpenTasks);
}

// ---------- Import modal ----------
let _importFiles = [];

function openImportModal() {
  _importFiles = [];
  $("#import-file-list").innerHTML = "";
  $("#import-results").innerHTML = "";
  $("#import-modal-submit").disabled = true;
  $("#import-modal-backdrop").classList.remove("hidden");
}

function closeImportModal() {
  $("#import-modal-backdrop").classList.add("hidden");
  _importFiles = [];
}

function renderImportFileList() {
  const el = $("#import-file-list");
  if (!_importFiles.length) { el.innerHTML = ""; return; }
  el.innerHTML = _importFiles.map((f, i) =>
    `<div class="import-file-row">
      <span class="import-file-name">${escapeHtml(f.name)}</span>
      <button class="import-file-remove" data-idx="${i}" title="Remove">×</button>
    </div>`
  ).join("");
  $("#import-modal-submit").disabled = false;
}

async function submitImport() {
  if (!_importFiles.length) return;
  const btn = $("#import-modal-submit");
  btn.disabled = true;
  btn.textContent = "Importing…";

  const form = new FormData();
  _importFiles.forEach((f) => form.append("files", f));

  try {
    const res = await fetch("/api/import", { method: "POST", body: form });
    const data = await res.json();
    const resultsEl = $("#import-results");
    resultsEl.innerHTML = data.results.map((r) => {
      if (r.ok) {
        const warns = (r.warnings || []).map((w) => `<div class="import-warn">⚠ ${escapeHtml(w)}</div>`).join("");
        return `<div class="import-result ok">✓ ${escapeHtml(r.filename)}${warns}</div>`;
      }
      return `<div class="import-result err">✗ ${escapeHtml(r.filename)} — ${escapeHtml(r.error)}</div>`;
    }).join("");
    if (data.processed > 0) {
      await refreshMeetings();
      await loadFacets();
    }
    _importFiles = [];
    $("#import-file-list").innerHTML = "";
    btn.textContent = `Done (${data.processed}/${data.total} imported)`;
    setTimeout(() => { btn.textContent = "Import"; btn.disabled = true; }, 3000);
  } catch (e) {
    $("#import-results").innerHTML = `<div class="import-result err">Error: ${escapeHtml(e.message)}</div>`;
    btn.disabled = false;
    btn.textContent = "Import";
  }
}

// ===== Handwriting Intake =====

function openIntakeModal() {
  // Lock body scroll so resting palm doesn't move the page behind the modal
  document.body.style.overflow = "hidden";
  document.body.style.touchAction = "none";
  $("#intake-modal-backdrop").classList.remove("hidden");
  $("#intake-modal-backdrop").classList.add("intake-open");
  const dateInput = $("#intake-date");
  if (!dateInput.value) {
    dateInput.value = new Date().toISOString().split("T")[0];
  }
  $("#intake-result").className = "intake-result hidden";
  $("#intake-result").innerHTML = "";
  $("#intake-scribble").value = "";
  $("#intake-action-items").value = "";
  $("#intake-reminders").value = "";
  $("#intake-modal-submit").disabled = false;
  $("#intake-modal-submit").textContent = "Save notes";
  // Populate group datalist from known facets
  const dl = $("#intake-group-list");
  dl.innerHTML = "";
  const groups = state.facets?.groups || [];
  groups.forEach((g) => {
    const opt = document.createElement("option");
    opt.value = g;
    dl.appendChild(opt);
  });
  requestAnimationFrame(() => $("#intake-scribble").focus());
}

function closeIntakeModal() {
  $("#intake-modal-backdrop").classList.add("hidden");
  $("#intake-modal-backdrop").classList.remove("intake-open");
  // Restore body scroll
  document.body.style.overflow = "";
  document.body.style.touchAction = "";
}


async function _intakeSaveNotes() {
  const body = $("#intake-scribble").value.trim();
  const actionItems = $("#intake-action-items").value.trim();
  const reminders = $("#intake-reminders").value.trim();
  if (!body && !actionItems && !reminders) {
    alert("Nothing to save — add some notes or tasks first.");
    return;
  }
  const btn = $("#intake-modal-submit");
  btn.disabled = true;
  btn.textContent = "Saving…";
  const resultEl = $("#intake-result");
  resultEl.className = "intake-result hidden";
  try {
    const res = await fetch("/api/notes/intake", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        group: $("#intake-group").value.trim(),
        topic: $("#intake-topic").value.trim(),
        date: $("#intake-date").value,
        attendees: $("#intake-attendees").value.trim(),
        body,
        action_items: actionItems,
        reminders,
      }),
    });
    const data = await res.json();
    resultEl.classList.remove("hidden");
    if (data.ok) {
      resultEl.className = "intake-result intake-result-ok";
      const chips = [
        data.topic ? `<span class="intake-chip">${escapeHtml(data.topic)}</span>` : "",
        data.group ? `<span class="intake-chip">${escapeHtml(data.group)}</span>` : "",
        `<span class="intake-chip">${data.task_count} task${data.task_count !== 1 ? "s" : ""}</span>`,
      ].join("");
      resultEl.innerHTML = `<strong>Saved!</strong> ${chips}
        <button class="intake-view-btn" id="intake-view-meeting">View note →</button>`;
      $("#intake-view-meeting").addEventListener("click", () => {
        switchTab("meetings");
        closeIntakeModal();
      });
      await refreshMeetings();
      await loadFacets();
      btn.textContent = "Done";
      setTimeout(() => {
        btn.disabled = false;
        btn.textContent = "Save notes";
      }, 4000);
    } else {
      resultEl.className = "intake-result intake-result-err";
      resultEl.innerHTML = `<strong>Error:</strong> ${escapeHtml(data.error || "Unknown error")}`;
      btn.disabled = false;
      btn.textContent = "Save notes";
    }
  } catch (err) {
    resultEl.classList.remove("hidden");
    resultEl.className = "intake-result intake-result-err";
    resultEl.innerHTML = `<strong>Error:</strong> ${escapeHtml(err.message)}`;
    btn.disabled = false;
    btn.textContent = "Save notes";
  }
}

async function submitIntake() {
  await _intakeSaveNotes();
}

// ---------- Data fetches ----------
const refreshMeetingsDebounced = debounce(async () => {
  const qs = meetingsFilters();
  const data = await api("/api/meetings?" + qs);
  state.meetings = data.meetings;
  if (state.selectedMeetingId && !state.meetings.find((m) => m.id === state.selectedMeetingId)) {
    state.selectedMeetingId = null;
    renderDetail(null);
  }
  renderMeetingsList();
}, 120);

async function refreshMeetings() { return refreshMeetingsDebounced(); }

async function loadFacets() {
  state.facets = await api("/api/facets");
}

async function loadGroups() {
  const groups = await api("/api/groups");
  renderGroupsTable(groups);
}

async function selectMeeting(id) {
  state.selectedMeetingId = id;
  $$("#meetings li").forEach((li) => li.classList.toggle("active", id && li.dataset.id === id));
  if (!id) { renderDetail(null); return; }
  const m = await api(`/api/meetings/${id}`);
  renderDetail(m);
}

function selectTask(idx) {
  state.selectedTaskIdx = idx;
  const frontPaper = state.paperOrder[0];
  const frontList = $(`ul[data-paper-list="${frontPaper}"]`);
  frontList?.querySelectorAll("li[data-idx]").forEach((el) =>
    el.classList.toggle("selected", parseInt(el.dataset.idx, 10) === idx));
  const task = state.tasksByStatus[frontPaper]?.[idx];
  if (task) openDrawer(task);
}

// ---------- Tab switching ----------
function switchTab(tab) {
  state.tab = tab;
  $$(".dock-btn[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $$(".view").forEach((v) => v.classList.remove("active"));
  const view = document.getElementById(`view-${tab}`);
  if (view) view.classList.add("active");

  const input = $("#q");
  if (input) input.placeholder = tab === "tasks" ? "Search tasks…"
    : tab === "meetings" ? "Search notes…"
    : tab === "groups" ? "Search groups…"
    : "Search…";

  if (tab === "home")     renderHome();
  if (tab === "groups")   loadGroups();
  if (tab === "meetings" && !state.meetings.length) refreshMeetings();
  if (tab === "tasks")    refreshTasks();
  if (tab === "smart")    loadSmartView(state.smartView);
}

// ---------- Search overlay ----------
function openSearchOverlay() {
  $("#search-overlay").classList.add("open");
  $("#search-overlay").setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => { const q = $("#q"); if (q) { q.focus(); q.select(); } });
}
function closeSearchOverlay() {
  $("#search-overlay").classList.remove("open");
  $("#search-overlay").setAttribute("aria-hidden", "true");
  $("#q")?.blur();
}

// ---------- Task filter toggle ----------
function updateTaskFilterToggleState() {
  const btn = $("#task-filter-toggle");
  if (!btn) return;
  const hasFilters = !!($("#t-type")?.value || $("#t-group")?.value || $("#t-overdue")?.checked || $("#t-priority")?.value);
  btn.classList.toggle("has-filters", hasFilters);
  btn.setAttribute("aria-expanded", String(!$("#task-filter-bar").hidden));
}

// ---------- Focus panel (home dashboard) ----------
function _renderFocusPanel(tasks) {
  const panel = $("#card-focus-panel");
  if (!panel) return;
  if (!tasks || !tasks.length) { panel.style.display = "none"; return; }
  panel.style.display = "";
  const top = tasks[0];
  const runners = tasks.slice(1, 3);
  const meta = [];
  if (top.group) meta.push(escapeHtml(top.group));
  if (top.deadline) meta.push("⏰ " + escapeHtml(top.deadline));
  if (top.priority === "high") meta.push("▲ High");
  if (top.estimate_minutes) meta.push("⏱ " + top.estimate_minutes + "m");
  $("#focus-panel-top").innerHTML = `
    <div class="focus-task-text">${escapeHtml(top.text)}</div>
    ${meta.length ? `<div class="focus-task-meta">${meta.join(" · ")}</div>` : ""}
    <div class="focus-actions">
      <button class="focus-action-btn focus-complete" data-focus-id="${top.id}" title="Complete">
        <span class="focus-btn-icon"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><polyline points="2.5,8.5 6.5,12.5 13.5,4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
        <span class="focus-btn-label">Complete</span>
      </button>
      <button class="focus-action-btn focus-defer" data-focus-id="${top.id}" title="Defer">
        <span class="focus-btn-icon"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="5.5" stroke="currentColor" stroke-width="1.75"/><path d="M8 5v3l2.2 1.4" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"/></svg></span>
        <span class="focus-btn-label">Defer</span>
      </button>
      <button class="focus-action-btn focus-focus" title="Focus">
        <span class="focus-btn-icon"><svg width="16" height="16" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="5.5" stroke="currentColor" stroke-width="1.75"/><circle cx="8" cy="8" r="2" stroke="currentColor" stroke-width="1.75"/></svg></span>
        <span class="focus-btn-label">Focus</span>
      </button>
    </div>`;
  $("#focus-panel-runners").innerHTML = runners.length
    ? `<div class="focus-runners-label">Also up next</div>` + runners.map((t) =>
        `<div class="focus-runner"><span class="focus-runner-text">${escapeHtml(t.text)}</span>${t.group ? ` <span class="focus-runner-group">${escapeHtml(t.group)}</span>` : ""}</div>`
      ).join("")
    : "";
}

// ---------- Smart Views ----------
async function loadSmartView(viewName) {
  state.smartView = viewName;
  $$(".smart-view-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === viewName));
  const titles = { today: "Today", upcoming: "Upcoming", neglected: "Neglected", quick_wins: "Quick Wins", waiting: "Waiting on…" };
  const descs = {
    today: "Tasks due today or highest urgency.",
    upcoming: "Deadlines in the next 7 days.",
    neglected: "High-priority tasks older than 2 weeks.",
    quick_wins: "Tasks estimating 30 minutes or less.",
    waiting: "Tasks blocked by unfinished dependencies.",
  };
  const vName = viewName;
  if ($("#smart-view-title")) $("#smart-view-title").textContent = titles[vName] || vName;
  if ($("#smart-view-desc"))  $("#smart-view-desc").textContent = descs[vName] || "";

  const ul = $("#smart-view-tasks");
  ul.innerHTML = `<li class="empty">Loading…</li>`;
  try {
    const data = await api("/api/tasks?smart_view=" + encodeURIComponent(viewName) + "&status=open");
    state.smartViewTasks = data.tasks;
    if (!data.tasks.length) {
      ul.innerHTML = `<li class="empty">No tasks in this view.</li>`;
    } else {
      ul.innerHTML = data.tasks.map((t, i) => _taskRow(t, i, "smart-view")).join("");
    }
  } catch (e) {
    ul.innerHTML = `<li class="empty">Failed to load.</li>`;
  }
}

// ---------- Snooze popup ----------
let _snoozeTask = null;

function openSnoozePopup(task) {
  _snoozeTask = task;
  const popup = $("#snooze-popup");
  const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate() + 1);
  const tomorrowISO = tomorrow.toISOString().slice(0, 10);
  const inp = $("#snooze-date-input");
  inp.min = tomorrowISO;
  inp.value = tomorrowISO;
  popup.classList.remove("hidden");
}

function closeSnoozePopup() {
  $("#snooze-popup").classList.add("hidden");
  _snoozeTask = null;
}

async function confirmSnooze() {
  if (!_snoozeTask) return;
  const until = $("#snooze-date-input").value;
  if (!until) return;
  await api("/api/tasks/snooze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: _snoozeTask.id, until }),
  });
  closeSnoozePopup();
  await refreshTasks();
  if (state.tab === "smart") loadSmartView(state.smartView);
}

// ---------- Focus mode ----------
let _focusTasks = [];
let _focusIdx = 0;

function openFocusMode(tasksOrSingle) {
  if (!tasksOrSingle) return;
  const arr = Array.isArray(tasksOrSingle) ? tasksOrSingle : [tasksOrSingle];
  _focusTasks = arr.filter((t) => t && !t.done);
  if (!_focusTasks.length) return;
  _focusIdx = 0;
  _renderFocusModeCurrent();
  document.body.classList.add("focus-mode-active");
  const el = $("#focus-mode");
  el.classList.remove("hidden");
  requestAnimationFrame(() => el.classList.add("focus-mode-in"));
}

function closeFocusMode() {
  const el = $("#focus-mode");
  el.classList.remove("focus-mode-in");
  el.classList.add("hidden");
  document.body.classList.remove("focus-mode-active");
  _focusTasks = [];
  _focusIdx = 0;
}

function _renderFocusModeCurrent() {
  const task = _focusTasks[_focusIdx];
  if (!task) { closeFocusMode(); return; }
  $("#focus-mode-text").textContent = task.text;
  const meta = [];
  if (task.group) meta.push(escapeHtml(task.group));
  if (task.deadline) meta.push("⏰ " + escapeHtml(task.deadline));
  if (task.priority === "high") meta.push("▲ High priority");
  if (task.estimate_minutes) meta.push("⏱ " + task.estimate_minutes + "m");
  $("#focus-mode-meta").innerHTML = meta.join(" · ");
  const total = _focusTasks.length;
  $("#focus-mode-progress").textContent = total > 1 ? `${_focusIdx + 1} of ${total}` : "";
}

// ---------- Command palette ----------
const CMD_STATIC = [
  { label: "Go to Home",        icon: "🏠", action: () => { closeCommandPalette(); switchTab("home"); } },
  { label: "Go to Tasks",       icon: "✓",  action: () => { closeCommandPalette(); switchTab("tasks"); } },
  { label: "Go to Meetings",    icon: "📅", action: () => { closeCommandPalette(); switchTab("meetings"); } },
  { label: "Go to Smart Views", icon: "⚡", action: () => { closeCommandPalette(); switchTab("smart"); } },
  { label: "Add new task",      icon: "+",  action: () => { closeCommandPalette(); openNLModal(); } },
  { label: "Focus Mode",        icon: "🎯", action: () => { closeCommandPalette(); openFocusMode(state.stats?.top_urgency?.[0]); } },
  { label: "Daily Plan",        icon: "📋", action: () => { closeCommandPalette(); openDailyPlan(); } },
];
let _cmdSelectedIdx = 0;
let _cmdCurrentItems = [];

function openCommandPalette() {
  $("#cmd-q").value = "";
  _renderCmdResults(CMD_STATIC);
  $("#cmd-palette").classList.remove("hidden");
  setTimeout(() => $("#cmd-q").focus(), 10);
}

function closeCommandPalette() {
  $("#cmd-palette").classList.add("hidden");
}

const _cmdDebounced = debounce(async (q) => {
  if (!q.trim()) { _renderCmdResults(CMD_STATIC); return; }
  const filtered = CMD_STATIC.filter((c) => c.label.toLowerCase().includes(q.toLowerCase()));
  let taskResults = [];
  try {
    const data = await api("/api/tasks/search?q=" + encodeURIComponent(q) + "&limit=6");
    taskResults = (data.tasks || []).map((t) => ({
      label: t.text,
      meta: t.group || "",
      icon: "✓",
      action: () => { closeCommandPalette(); switchTab("tasks"); },
    }));
  } catch (_) {}
  _renderCmdResults([...filtered, ...taskResults]);
}, 200);

function _renderCmdResults(items) {
  _cmdCurrentItems = items;
  _cmdSelectedIdx = 0;
  const ul = $("#cmd-results");
  if (!items.length) { ul.innerHTML = `<li class="cmd-empty">No results</li>`; return; }
  ul.innerHTML = items.map((item, i) =>
    `<li class="cmd-item${i === 0 ? " selected" : ""}" data-cmd-idx="${i}">
      <span class="cmd-icon">${item.icon || ""}</span>
      <span class="cmd-label">${escapeHtml(item.label)}</span>
      ${item.meta ? `<span class="cmd-meta">${escapeHtml(item.meta)}</span>` : ""}
    </li>`
  ).join("");
}

// ---------- Daily planning ----------
async function openDailyPlan() {
  const greeting = greetingFor(new Date());
  if ($("#daily-plan-title")) $("#daily-plan-title").textContent = `${greeting} — here's your day`;
  try {
    const [todayData, overdueData] = await Promise.all([
      api("/api/tasks?smart_view=today&status=open"),
      api("/api/tasks?overdue=1&status=open"),
    ]);
    const seen = new Set();
    const tasks = [];
    [...(todayData.tasks || []), ...(overdueData.tasks || [])].forEach((t) => {
      if (!seen.has(t.id)) { seen.add(t.id); tasks.push(t); }
    });
    state.dailyPlanTasks = tasks;
    if ($("#daily-plan-desc")) {
      $("#daily-plan-desc").textContent = tasks.length
        ? `${tasks.length} task${tasks.length === 1 ? "" : "s"} to tackle today.`
        : "You're all clear for today!";
    }
    _renderDailyPlanModal();
    $("#daily-plan-backdrop").classList.remove("hidden");
  } catch (e) {
    console.error("daily plan load failed", e);
  }
}

function closeDailyPlanModal() {
  $("#daily-plan-backdrop").classList.add("hidden");
  localStorage.setItem("last_plan_date", new Date().toISOString().slice(0, 10));
}

function _renderDailyPlanModal() {
  const ul = $("#daily-plan-list");
  if (!ul) return;
  if (!state.dailyPlanTasks.length) {
    ul.innerHTML = `<li class="dp-empty">Nothing due today — clear skies!</li>`;
    return;
  }
  ul.innerHTML = state.dailyPlanTasks.map((t, i) => `
    <li class="dp-item" data-dp-idx="${i}">
      <span class="dp-rank">${i + 1}</span>
      <div class="dp-content">
        <div class="dp-text">${escapeHtml(t.text)}</div>
        ${t.group ? `<span class="dp-group">${escapeHtml(t.group)}</span>` : ""}
      </div>
      <div class="dp-actions">
        ${i > 0 ? `<button class="dp-move dp-up" data-dp-up="${i}" title="Move up">↑</button>` : `<span class="dp-move"></span>`}
        ${i < state.dailyPlanTasks.length - 1 ? `<button class="dp-move dp-down" data-dp-down="${i}" title="Move down">↓</button>` : `<span class="dp-move"></span>`}
        <button class="dp-defer" data-dp-defer="${i}">defer</button>
      </div>
    </li>`).join("");
}

// ---------- Subtasks ----------
let _subtaskParentTask = null;

async function toggleSubtasks(taskId) {
  const ul = document.getElementById(`subtasks-${taskId}`);
  if (!ul) return;
  if (!ul.classList.contains("hidden")) { ul.classList.add("hidden"); return; }
  ul.innerHTML = "<li style='color:var(--muted);font-size:12px;padding:4px 0'>Loading…</li>";
  ul.classList.remove("hidden");
  try {
    const data = await api(`/api/tasks?parent_id=${taskId}&show_subtasks=1&status=open`);
    const doneData = await api(`/api/tasks?parent_id=${taskId}&show_subtasks=1&status=done`);
    const all = [...(data.tasks || []), ...(doneData.tasks || [])];
    if (!all.length) {
      ul.innerHTML = `<li class="subtask-item subtask-empty">No subtasks yet.</li>`;
    } else {
      ul.innerHTML = all.map((t) => `
        <li class="subtask-item${t.done ? " done" : ""}" data-task-id="${t.id}">
          <span class="checkbox action-toggle" title="${t.done ? "Mark open" : "Mark done"}"></span>
          <span class="subtask-text">${escapeHtml(t.text)}</span>
          ${t.priority === "high" ? `<span class="chip priority-high" style="font-size:10px;padding:1px 5px;">▲</span>` : ""}
        </li>`).join("");
    }
  } catch (err) {
    ul.innerHTML = `<li class="subtask-item subtask-empty">Failed to load.</li>`;
  }
}

function openAddSubtaskModal(parentTask) {
  _subtaskParentTask = parentTask;
  if ($("#subtask-parent-label")) $("#subtask-parent-label").textContent = `Under: "${parentTask.text}"`;
  if ($("#subtask-text")) $("#subtask-text").value = "";
  if ($("#subtask-priority")) $("#subtask-priority").value = "normal";
  $("#subtask-modal-backdrop").classList.remove("hidden");
  setTimeout(() => $("#subtask-text")?.focus(), 10);
}

function closeAddSubtaskModal() {
  $("#subtask-modal-backdrop").classList.add("hidden");
  _subtaskParentTask = null;
}

async function submitAddSubtaskModal() {
  if (!_subtaskParentTask) return;
  const text = $("#subtask-text")?.value.trim();
  if (!text) { $("#subtask-text")?.focus(); return; }
  const priority = $("#subtask-priority")?.value || "normal";
  await api("/api/tasks/add-subtask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ parent_id: _subtaskParentTask.id, text, priority }),
  });
  closeAddSubtaskModal();
  const parentId = _subtaskParentTask?.id;
  await refreshTasks();
  if (parentId) toggleSubtasks(parentId);
}

// ---------- Blocker/dependency modal ----------
let _blockerTask = null;

function openBlockerModal(task) {
  _blockerTask = task;
  if ($("#blocker-task-label")) $("#blocker-task-label").textContent = `Making blocked: "${task.text}"`;
  if ($("#blocker-q")) { $("#blocker-q").value = ""; }
  if ($("#blocker-results")) $("#blocker-results").innerHTML = "";
  $("#blocker-modal-backdrop").classList.remove("hidden");
  setTimeout(() => $("#blocker-q")?.focus(), 10);
}

function closeBlockerModal() {
  $("#blocker-modal-backdrop").classList.add("hidden");
  _blockerTask = null;
}

const _blockerSearchDebounced = debounce(async (q) => {
  const ul = $("#blocker-results");
  if (!q.trim() || !ul) return;
  try {
    const data = await api("/api/tasks/search?q=" + encodeURIComponent(q) + "&limit=8");
    const tasks = (data.tasks || []).filter((t) => t.id !== _blockerTask?.id);
    if (!tasks.length) { ul.innerHTML = `<li class="blocker-empty">No tasks found.</li>`; return; }
    ul.innerHTML = tasks.map((t) =>
      `<li class="blocker-result-item" data-blocker-id="${t.id}">
        <span>${escapeHtml(t.text)}</span>
        ${t.group ? `<span class="blocker-group">${escapeHtml(t.group)}</span>` : ""}
      </li>`
    ).join("");
  } catch (_) {}
}, 200);

// ---------- Event wiring ----------
document.addEventListener("DOMContentLoaded", async () => {
  // Dock nav
  $$(".dock-btn[data-tab]").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

  // Safe-triangle dock submenu
  (function () {
    const wrap = document.querySelector(".dock-btn-wrap");
    if (!wrap) return;
    const btn = wrap.querySelector(".dock-btn");
    const sub = wrap.querySelector(".dock-submenu");
    let closeTimer = null;
    let exitPt = null;

    function open() { clearTimeout(closeTimer); sub.classList.add("open"); }
    function close() { sub.classList.remove("open"); exitPt = null; }
    function scheduleClose() { clearTimeout(closeTimer); closeTimer = setTimeout(close, 80); }

    function inTriangle(px, py, ax, ay, bx, by, cx, cy) {
      const s = (x1, y1, x2, y2, x3, y3) => (x1 - x3) * (y2 - y3) - (x2 - x3) * (y1 - y3);
      const d1 = s(px,py,ax,ay,bx,by), d2 = s(px,py,bx,by,cx,cy), d3 = s(px,py,cx,cy,ax,ay);
      return !((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0));
    }

    btn.addEventListener("mouseenter", open);
    btn.addEventListener("mouseleave", (e) => { exitPt = { x: e.clientX, y: e.clientY }; scheduleClose(); });
    sub.addEventListener("mouseenter", () => clearTimeout(closeTimer));
    sub.addEventListener("mouseleave", scheduleClose);

    document.addEventListener("mousemove", (e) => {
      if (!sub.classList.contains("open") || !exitPt) return;
      if (e.target.closest(".dock-btn-wrap")) { clearTimeout(closeTimer); return; }
      const r = sub.getBoundingClientRect();
      if (inTriangle(e.clientX, e.clientY, exitPt.x, exitPt.y, r.left, r.top - 4, r.left, r.bottom + 4)) {
        clearTimeout(closeTimer);
      } else {
        close();
      }
    });
  })();

  // Search overlay
  $("#dock-search-btn").addEventListener("click", openSearchOverlay);
  $("#search-overlay-backdrop").addEventListener("click", closeSearchOverlay);

  // Global search
  $("#q").addEventListener("input", () => {
    if (state.tab === "tasks") refreshTasksDebounced();
    else if (state.tab === "meetings") refreshMeetingsDebounced();
  });

  // Tasks filters
  ["t-type", "t-group", "t-overdue", "t-priority", "t-snoozed"].forEach((id) =>
    $("#" + id)?.addEventListener("change", () => { refreshTasks(); updateTaskFilterToggleState(); }));
  $("#t-clear").addEventListener("click", () => {
    $("#t-type").value = "";
    $("#t-group").value = "";
    $("#t-overdue").checked = false;
    if ($("#t-priority")) $("#t-priority").value = "";
    if ($("#t-snoozed")) $("#t-snoozed").checked = false;
    $("#q").value = "";
    refreshTasks();
    updateTaskFilterToggleState();
  });

  // Task filter toggle button
  $("#task-filter-toggle").addEventListener("click", () => {
    const bar = $("#task-filter-bar");
    bar.hidden = !bar.hidden;
    updateTaskFilterToggleState();
  });

  // Peek-edge clicks
  $("#paper-stack").addEventListener("click", (e) => {
    const peek = e.target.closest(".peek-edge");
    if (!peek) return;
    bringToFront(peek.dataset.peek);
  });

  // Task list clicks (checkbox fix: use .closest())
  $("#paper-stack").addEventListener("click", async (e) => {
    const li = e.target.closest("li[data-task-id]");
    if (!li) return;
    const paper = li.dataset.paper;
    const idx = parseInt(li.dataset.idx, 10);
    const task = state.tasksByStatus[paper]?.[idx];
    if (!task) return;
    if (e.target.closest(".action-toggle")) {
      if (!task.done) li.classList.add("task-completing");
      await toggleTaskDone(task); return;
    }
    if (e.target.closest(".action-bb")) {
      await toggleTaskBackburner(task); return;
    }
    if (paper === state.paperOrder[0]) selectTask(idx);
  });

  // Right-click context menu on tasks (front paper only)
  $("#paper-stack").addEventListener("contextmenu", (e) => {
    const li = e.target.closest("li[data-task-id]");
    if (!li) return;
    const paper = li.dataset.paper;
    if (paper !== state.paperOrder[0]) return;
    const idx = parseInt(li.dataset.idx, 10);
    const task = state.tasksByStatus[paper]?.[idx];
    if (!task) return;
    openContextMenu(e, task);
  });

  // Context menu actions
  $("#ctx-menu").addEventListener("click", async (e) => {
    const item = e.target.closest(".ctx-item");
    if (!item || !_ctxTask) return;
    const action = item.dataset.action;
    const task = _ctxTask;
    closeContextMenu();
    if (action === "toggle-done")  { await toggleTaskDone(task); }
    if (action === "edit")         { openEditModal(task); }
    if (action === "delete")       { await deleteTask(task); }
    if (action === "view-note")    { openDrawer(task); }
    if (action === "backburner")   { await toggleTaskBackburner(task); }
    if (action === "set-priority") { await setPriority(task, item.dataset.priority); }
    if (action === "snooze")       { openSnoozePopup(task); }
    if (action === "add-subtask")  { openAddSubtaskModal(task); }
    if (action === "add-blocker")  { openBlockerModal(task); }
  });

  // Edit modal
  $("#edit-modal-close").addEventListener("click",   closeEditModal);
  $("#edit-modal-cancel").addEventListener("click",  closeEditModal);
  $("#edit-modal-submit").addEventListener("click",  submitEditModal);
  $("#edit-modal-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "edit-modal-backdrop") closeEditModal();
  });
  $("#edit-m-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter")  { e.preventDefault(); submitEditModal(); }
    if (e.key === "Escape") { closeEditModal(); }
  });

  // Drawer
  $("#drawer-content").addEventListener("click", async (e) => {
    const link = e.target.closest(".open-full");
    if (!link) return;
    e.preventDefault();
    const mid = link.dataset.mid;
    if (!mid) return;
    closeDrawer();
    switchTab("meetings");
    await refreshMeetings();
    await selectMeeting(mid);
  });
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#drawer-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "drawer-backdrop") closeDrawer();
  });

  // Quick deadline buttons (add modal)
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".dl-quick-btn");
    if (!btn) return;
    const prefix = btn.dataset.prefix || "m";
    const which = btn.dataset.quick;
    const today = new Date();
    if (which === "today") {
      setDeadlineSelects(today, prefix);
    } else if (which === "this-week") {
      setDeadlineSelects(_thisFriday(), prefix);
    } else if (which === "next-week") {
      const nextMon = _nextWeekday(1);
      const nextFri = new Date(nextMon); nextFri.setDate(nextMon.getDate() + 4);
      setDeadlineSelects(nextFri, prefix);
    }
  });

  // Home cards
  $("#hero-add-task").addEventListener("click", openNLModal);
  $("#hero-view-tasks").addEventListener("click", () => switchTab("tasks"));

  // Deadline strip
  $("#deadlines-strip").addEventListener("click", (e) => {
    const day = e.target.closest(".deadline-day");
    if (!day) return;
    switchTab("tasks");
    $("#q").value = day.dataset.date;
    refreshTasks();
  });

  // Overdue list
  $("#overdue-list").addEventListener("click", async (e) => {
    const li = e.target.closest("li[data-overdue-id]");
    if (!li) return;
    const tid = li.dataset.overdueId;
    switchTab("tasks");
    $("#t-overdue").checked = true;
    if (state.paperOrder[0] !== "active") bringToFront("active");
    await refreshTasks();
    const idx = state.tasksByStatus.active.findIndex((t) => t.id === tid);
    if (idx >= 0) {
      selectTask(idx);
      const el = document.querySelector(`ul[data-paper-list="active"] li[data-idx="${idx}"]`);
      if (el) el.scrollIntoView({ block: "nearest" });
    }
  });

  // By-group
  $("#group-bars").addEventListener("click", (e) => {
    const g = e.target.closest(".group-bar");
    if (!g) return;
    switchTab("tasks");
    const want = g.dataset.group;
    refreshTasks().then(() => {
      const sel = $("#t-group");
      if (Array.from(sel.options).some((o) => o.value === want)) {
        sel.value = want;
        refreshTasks();
      }
    });
  });

  // Recent meeting
  $("#recent-meeting-card").addEventListener("click", async () => {
    const mid = $("#recent-meeting-card").dataset.meetingId;
    if (!mid) return;
    switchTab("meetings");
    await refreshMeetings();
    await selectMeeting(mid);
  });

  // NL modal
  $("#open-add-modal").addEventListener("click", openNLModal);
  $("#nl-close").addEventListener("click", closeNLModal);
  $("#nl-cancel-step1").addEventListener("click", closeNLModal);
  $("#nl-confirm-step1").addEventListener("click", _nlTransitionToStep2);
  $("#nl-back").addEventListener("click", _nlGoBack);
  $("#nl-cancel").addEventListener("click", closeNLModal);
  $("#nl-submit").addEventListener("click", submitNLModal);
  $("#nl-modal-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "nl-modal-backdrop") closeNLModal();
  });
  const _nlHintDebounced = debounce((text) => {
    if (!text.trim()) { $("#nl-hint").textContent = ""; return; }
    const p = parseNLTask(text);
    const hints = [];
    if (p.deadline) hints.push(p.deadline);
    if (p.priority === "high") hints.push("high priority");
    if (p.contact) hints.push(`contact: ${p.contact}`);
    if (p.phone) hints.push(p.phone);
    if (p.email) hints.push(p.email);
    if (p.estimate_minutes) hints.push(`~${p.estimate_minutes}m`);
    if (p.group) hints.push(`group: ${p.group}`);
    $("#nl-hint").textContent = hints.length ? "Detected: " + hints.join(", ") : "";
  }, 400);
  const _nlAutoParseDebounced = debounce(() => {
    const ta = $("#nl-text");
    if (!ta.readOnly && ta.value.trim()) _nlTransitionToStep2();
  }, 700);
  $("#nl-text").addEventListener("input", (e) => {
    _nlHintDebounced(e.target.value);
    _nlAutoParseDebounced();
  });
  $("#nl-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); _nlTransitionToStep2(); }
  });

  // Edit modal recurrence picker
  $("#edit-m-recur")?.addEventListener("change", () => {
    _updateRecurDetail("edit-m", { type: $("#edit-m-recur").value });
  });

  // Snooze popup
  $("#snooze-cancel").addEventListener("click", closeSnoozePopup);
  $("#snooze-confirm").addEventListener("click", confirmSnooze);
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".snooze-quick");
    if (!btn) return;
    const val = btn.dataset.snooze;
    const today = new Date();
    let d;
    if (val === "next-monday") d = _nextWeekday(1);
    else if (val === "next-week") { d = _nextWeekday(1); d.setDate(d.getDate() + 4); }
    else { d = new Date(today); d.setDate(today.getDate() + parseInt(val)); }
    $("#snooze-date-input").value = d.toISOString().slice(0, 10);
  });

  // Focus mode
  $("#focus-btn-complete").addEventListener("click", async () => {
    const task = _focusTasks[_focusIdx];
    if (!task) return;
    const el = $("#focus-mode");
    // Reset and replay the circle animation
    const circle = el.querySelector(".focus-done-circle");
    circle.style.animation = "none";
    void circle.offsetHeight;
    circle.style.animation = "";
    el.classList.add("completing");
    // API call and minimum display time run in parallel
    await Promise.all([
      toggleTaskDone(task),
      new Promise(r => setTimeout(r, 680)),
    ]);
    el.classList.remove("completing");
    _focusIdx++;
    if (_focusIdx >= _focusTasks.length) { closeFocusMode(); return; }
    _renderFocusModeCurrent();
  });
  $("#focus-btn-skip").addEventListener("click", () => {
    _focusIdx++;
    if (_focusIdx >= _focusTasks.length) { closeFocusMode(); return; }
    _renderFocusModeCurrent();
  });
  $("#focus-mode-close").addEventListener("click", closeFocusMode);

  // Focus panel (home dashboard)
  document.addEventListener("click", async (e) => {
    if (e.target.closest(".focus-complete")) {
      const btn = e.target.closest(".focus-complete");
      const id = btn.dataset.focusId;
      const task = state.stats?.top_urgency?.find((t) => t.id === id);
      if (task) await toggleTaskDone(task);
    }
    if (e.target.closest(".focus-defer")) {
      const btn = e.target.closest(".focus-defer");
      const id = btn.dataset.focusId;
      const task = state.stats?.top_urgency?.find((t) => t.id === id);
      if (task) openSnoozePopup(task);
    }
    if (e.target.closest(".focus-focus")) {
      openFocusMode(state.stats?.top_urgency || []);
    }
  });

  // Command palette
  $("#cmd-backdrop").addEventListener("click", closeCommandPalette);
  $("#cmd-q").addEventListener("input", (e) => _cmdDebounced(e.target.value));
  $("#cmd-q").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      _cmdSelectedIdx = Math.min(_cmdSelectedIdx + 1, _cmdCurrentItems.length - 1);
    } else if (e.key === "ArrowUp") {
      _cmdSelectedIdx = Math.max(_cmdSelectedIdx - 1, 0);
    } else if (e.key === "Enter") {
      const item = _cmdCurrentItems[_cmdSelectedIdx];
      if (item) item.action();
      return;
    } else {
      return;
    }
    e.preventDefault();
    $$(".cmd-item").forEach((li, i) => li.classList.toggle("selected", i === _cmdSelectedIdx));
  });
  $("#cmd-results").addEventListener("click", (e) => {
    const li = e.target.closest(".cmd-item");
    if (!li) return;
    const idx = parseInt(li.dataset.cmdIdx, 10);
    const item = _cmdCurrentItems[idx];
    if (item) item.action();
  });

  // Daily planning modal
  $("#daily-plan-close").addEventListener("click", closeDailyPlanModal);
  $("#daily-plan-skip").addEventListener("click", closeDailyPlanModal);
  $("#daily-plan-go").addEventListener("click", () => {
    state.dailyPlanOrder = state.dailyPlanTasks.map((t) => t.id);
    closeDailyPlanModal();
    openFocusMode(state.dailyPlanTasks);
  });
  $("#daily-plan-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "daily-plan-backdrop") closeDailyPlanModal();
  });
  $("#daily-plan-list").addEventListener("click", (e) => {
    const upBtn = e.target.closest("[data-dp-up]");
    const downBtn = e.target.closest("[data-dp-down]");
    const deferBtn = e.target.closest("[data-dp-defer]");
    if (upBtn) {
      const i = parseInt(upBtn.dataset.dpUp, 10);
      [state.dailyPlanTasks[i - 1], state.dailyPlanTasks[i]] = [state.dailyPlanTasks[i], state.dailyPlanTasks[i - 1]];
      _renderDailyPlanModal();
    } else if (downBtn) {
      const i = parseInt(downBtn.dataset.dpDown, 10);
      [state.dailyPlanTasks[i], state.dailyPlanTasks[i + 1]] = [state.dailyPlanTasks[i + 1], state.dailyPlanTasks[i]];
      _renderDailyPlanModal();
    } else if (deferBtn) {
      const i = parseInt(deferBtn.dataset.dpDefer, 10);
      openSnoozePopup(state.dailyPlanTasks[i]);
    }
  });

  // Subtask modal
  $("#subtask-modal-close").addEventListener("click", closeAddSubtaskModal);
  $("#subtask-modal-cancel").addEventListener("click", closeAddSubtaskModal);
  $("#subtask-modal-submit").addEventListener("click", submitAddSubtaskModal);
  $("#subtask-modal-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "subtask-modal-backdrop") closeAddSubtaskModal();
  });
  $("#subtask-text")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); submitAddSubtaskModal(); }
  });

  // Subtask toggle chips (event delegation on paper-stack + smart-view)
  document.addEventListener("click", (e) => {
    const chip = e.target.closest("[data-subtask-toggle]");
    if (!chip) return;
    toggleSubtasks(chip.dataset.subtaskToggle);
  });

  // Subtask checkboxes (inside subtask-list)
  document.addEventListener("click", async (e) => {
    if (!e.target.closest(".subtask-list")) return;
    const li = e.target.closest("li[data-task-id]");
    if (!li || !e.target.closest(".action-toggle")) return;
    const taskId = li.dataset.taskId;
    // Find the task in any state list or smart view
    let task = state.smartViewTasks.find((t) => t.id === taskId);
    if (!task) {
      for (const paper of ["active", "backburner", "done"]) {
        task = state.tasksByStatus[paper].find((t) => t.id === taskId);
        if (task) break;
      }
    }
    // Fallback: create minimal task object for toggle
    if (!task) task = { id: taskId, done: li.classList.contains("done") };
    await api("/api/tasks/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: taskId, done: !task.done }),
    });
    await refreshTasks();
  });

  // Blocker modal
  $("#blocker-modal-close").addEventListener("click", closeBlockerModal);
  $("#blocker-modal-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "blocker-modal-backdrop") closeBlockerModal();
  });
  $("#blocker-q")?.addEventListener("input", (e) => _blockerSearchDebounced(e.target.value));
  $("#blocker-results")?.addEventListener("click", async (e) => {
    const li = e.target.closest(".blocker-result-item");
    if (!li || !_blockerTask) return;
    const dependsOnId = li.dataset.blockerId;
    try {
      await api("/api/tasks/dependency/add", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: _blockerTask.id, depends_on_id: dependsOnId }),
      });
      closeBlockerModal();
      await refreshTasks();
    } catch (err) {
      alert("Could not add dependency: " + err.message);
    }
  });

  // Smart view sidebar buttons
  $("#view-smart")?.addEventListener("click", (e) => {
    const btn = e.target.closest(".smart-view-btn");
    if (btn) loadSmartView(btn.dataset.view);
  });

  // Smart view task interactions (toggle, context menu, etc.)
  $("#smart-view-tasks")?.addEventListener("click", async (e) => {
    const li = e.target.closest("li[data-task-id]");
    if (!li) return;
    const idx = parseInt(li.dataset.idx, 10);
    const task = state.smartViewTasks[idx];
    if (!task) return;
    if (e.target.closest(".action-toggle")) {
      await toggleTaskDone(task);
    } else if (e.target.closest(".action-bb")) {
      await toggleTaskBackburner(task);
    }
  });
  $("#smart-view-tasks")?.addEventListener("contextmenu", (e) => {
    const li = e.target.closest("li[data-task-id]");
    if (!li) return;
    const idx = parseInt(li.dataset.idx, 10);
    const task = state.smartViewTasks[idx];
    if (task) openContextMenu(e, task);
  });

  // Snoozed filter
  $("#t-snoozed")?.addEventListener("change", () => { refreshTasks(); updateTaskFilterToggleState(); });

  // ---- Radial menu setup ----
  initRadialPositions();

  // Trigger: click to toggle ring
  $("#radial-trigger")?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleRadial();
  });

  // Clear-all button
  $("#radial-clear-btn")?.addEventListener("click", (e) => {
    e.stopPropagation();
    state.meetingFilters = { group: "", purpose: "", attendee: "", dateFrom: "", dateTo: "", hasOpenTasks: false };
    const ai = $("#rpop-attendee-input"); if (ai) ai.value = "";
    const df = $("#rpop-date-from");      if (df) df.value = "";
    const dt = $("#rpop-date-to");        if (dt) dt.value = "";
    closeRadial();
    updateRadialFilterState();
    refreshMeetings();
  });

  // Main ring items: hover → open sub-ring; click → immediate action or toggle sub
  $$("[data-radial]").forEach((el) => {
    const name = el.dataset.radial;

    el.addEventListener("mouseenter", () => {
      clearTimeout(_subTimer);
      if (name !== "import" && name !== "open-tasks") {
        _subTimer = setTimeout(() => openRadialSub(name), 100);
      }
    });
    el.addEventListener("mouseleave", () => {
      clearTimeout(_subTimer);
      _subTimer = setTimeout(closeRadialSub, 420);
    });

    el.addEventListener("click", (e) => {
      e.stopPropagation();
      if (name === "import") {
        closeRadial();
        openImportModal();
      } else if (name === "open-tasks") {
        state.meetingFilters.hasOpenTasks = !state.meetingFilters.hasOpenTasks;
        updateRadialFilterState();
        refreshMeetings();
      } else {
        // Toggle sub-ring open/closed
        if (_openSubName === name) closeRadialSub();
        else openRadialSub(name);
      }
    });
  });

  // Cancel sub-close when hovering sub-items or popups (mouseover bubbles)
  document.addEventListener("mouseover", (e) => {
    if (e.target.closest(".radial-sub-item, .radial-popup")) {
      clearTimeout(_subTimer);
    }
  });
  document.addEventListener("mouseout", (e) => {
    const leaving   = e.target.closest(".radial-sub-item, .radial-popup");
    const goingTo   = e.relatedTarget?.closest(".radial-sub-item, .radial-popup, [data-radial]");
    if (leaving && !goingTo) {
      _subTimer = setTimeout(closeRadialSub, 420);
    }
  });

  // Sub-ring item clicks (group / purpose values)
  document.addEventListener("click", (e) => {
    const si = e.target.closest(".radial-sub-item");
    if (!si) return;
    const filter = si.dataset.subFilter;
    const value  = si.dataset.value;
    if (!filter) return;
    // Toggle: click active value to deselect
    state.meetingFilters[filter] = state.meetingFilters[filter] === value ? "" : value;
    closeRadial();
    updateRadialFilterState();
    refreshMeetings();
  });

  // Attendee popup input
  $("#rpop-attendee-input")?.addEventListener("input", () => {
    state.meetingFilters.attendee = $("#rpop-attendee-input").value.trim();
    updateRadialFilterState();
    refreshMeetingsDebounced();
  });

  // Date popup inputs
  $("#rpop-date-from")?.addEventListener("change", () => {
    state.meetingFilters.dateFrom = $("#rpop-date-from").value;
    updateRadialFilterState();
    refreshMeetings();
  });
  $("#rpop-date-to")?.addEventListener("change", () => {
    state.meetingFilters.dateTo = $("#rpop-date-to").value;
    updateRadialFilterState();
    refreshMeetings();
  });

  // Close ring when clicking outside radial root
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#radial-root") && $("#radial-root")?.classList.contains("open")) {
      closeRadial();
    }
  });

  // Meeting list click
  $("#meetings").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-id]");
    if (li) selectMeeting(li.dataset.id);
  });

  // Groups table click
  $("#groups-body").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-group]");
    if (!tr) return;
    state.meetingFilters.group = tr.dataset.group;
    switchTab("meetings");
    updateRadialFilterState();
    refreshMeetings();
  });

  // Intake modal
  $("#intake-modal-close").addEventListener("click", closeIntakeModal);
  $("#intake-modal-cancel").addEventListener("click", closeIntakeModal);
  // No backdrop-tap-to-close: too easy to accidentally dismiss with a resting palm on iPad
  $("#intake-modal-submit").addEventListener("click", submitIntake);
  // Import modal
  $("#import-modal-close").addEventListener("click",  closeImportModal);
  $("#import-modal-cancel").addEventListener("click", closeImportModal);
  $("#import-modal-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "import-modal-backdrop") closeImportModal();
  });
  $("#import-modal-submit").addEventListener("click", submitImport);

  // Import: click drop zone → file picker
  $("#import-drop-zone").addEventListener("click", () => $("#import-file-input").click());
  $("#import-file-input").addEventListener("change", (e) => {
    _importFiles = Array.from(e.target.files);
    renderImportFileList();
    e.target.value = "";
  });

  // Import: drag and drop
  const dropZone = $("#import-drop-zone");
  dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("drag-over"); });
  dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drag-over"));
  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("drag-over");
    const dropped = Array.from(e.dataTransfer.files).filter((f) => f.name.endsWith(".md"));
    _importFiles = [..._importFiles, ...dropped];
    renderImportFileList();
  });

  // Import: remove file from list
  $("#import-file-list").addEventListener("click", (e) => {
    const btn = e.target.closest(".import-file-remove");
    if (!btn) return;
    const idx = parseInt(btn.dataset.idx, 10);
    _importFiles.splice(idx, 1);
    renderImportFileList();
    if (!_importFiles.length) $("#import-modal-submit").disabled = true;
  });

  // Keyboard shortcuts
  document.addEventListener("keydown", async (e) => {
    const tag = (document.activeElement?.tagName || "").toLowerCase();
    const typing = ["input", "textarea", "select"].includes(tag);
    // Cmd+K / Ctrl+K: command palette (before typing guard)
    if ((e.metaKey || e.ctrlKey) && e.key === "k") {
      e.preventDefault();
      if (!$("#cmd-palette").classList.contains("hidden")) closeCommandPalette();
      else openCommandPalette();
      return;
    }

    if (e.key === "Escape") {
      if (!$("#cmd-palette").classList.contains("hidden")) { closeCommandPalette(); return; }
      if (!$("#focus-mode").classList.contains("hidden"))  { closeFocusMode(); return; }
      if (!$("#daily-plan-backdrop").classList.contains("hidden")) { closeDailyPlanModal(); return; }
      if (!$("#snooze-popup").classList.contains("hidden")) { closeSnoozePopup(); return; }
      if (!$("#subtask-modal-backdrop").classList.contains("hidden")) { closeAddSubtaskModal(); return; }
      if (!$("#blocker-modal-backdrop").classList.contains("hidden")) { closeBlockerModal(); return; }
      if ($("#search-overlay").classList.contains("open")) { closeSearchOverlay(); return; }
      if (!$("#intake-modal-backdrop").classList.contains("hidden")) { closeIntakeModal(); return; }
      if (!$("#import-modal-backdrop").classList.contains("hidden"))  { closeImportModal(); return; }
      if (!$("#edit-modal-backdrop").classList.contains("hidden"))    { closeEditModal(); return; }
      if (!$("#nl-modal-backdrop").classList.contains("hidden"))       { closeNLModal(); return; }
      if (state.drawerTask) { closeDrawer(); return; }
      if ($("#radial-root")?.classList.contains("open")) { closeRadial(); return; }
      if (typing) { document.activeElement.blur(); return; }
    }
    if (typing) return;
    if (e.key === "/") { e.preventDefault(); openSearchOverlay(); return; }
    if (e.key === "1") { e.preventDefault(); switchTab("home"); return; }
    if (e.key === "2") { e.preventDefault(); switchTab("tasks"); return; }
    if (e.key === "3") { e.preventDefault(); switchTab("meetings"); return; }
    if (e.key === "4") { e.preventDefault(); switchTab("groups"); return; }
    if (e.key === "5") { e.preventDefault(); switchTab("smart"); return; }
    if (e.key === "f") { e.preventDefault(); openFocusMode(state.stats?.top_urgency?.[0]); return; }
    if (e.key === "w") { e.preventDefault(); openIntakeModal(); return; }

    if (state.tab === "tasks") {
      const frontPaper = state.paperOrder[0];
      const frontTasks = state.tasksByStatus[frontPaper];
      if ((e.key === "j" || e.key === "k") && frontTasks.length) {
        e.preventDefault();
        const cur = state.selectedTaskIdx;
        const next = e.key === "j"
          ? Math.min(frontTasks.length - 1, cur < 0 ? 0 : cur + 1)
          : Math.max(0, cur < 0 ? 0 : cur - 1);
        selectTask(next);
        const el = document.querySelector(`ul[data-paper-list="${frontPaper}"] li[data-idx="${next}"]`);
        if (el) el.scrollIntoView({ block: "nearest" });
      } else if (e.key === "x" && state.selectedTaskIdx >= 0) {
        e.preventDefault();
        await toggleTaskDone(frontTasks[state.selectedTaskIdx]);
      } else if (e.key === "b" && state.selectedTaskIdx >= 0) {
        e.preventDefault();
        await toggleTaskBackburner(frontTasks[state.selectedTaskIdx]);
      } else if (e.key === "p" && state.selectedTaskIdx >= 0) {
        e.preventDefault();
        await cyclePriority(frontTasks[state.selectedTaskIdx]);
      } else if (e.key === "a") {
        e.preventDefault();
        openNLModal();
      }
    } else if (state.tab === "meetings") {
      if ((e.key === "j" || e.key === "k") && state.meetings.length) {
        e.preventDefault();
        const ids = state.meetings.map((m) => m.id);
        const cur = ids.indexOf(state.selectedMeetingId);
        const next = e.key === "j"
          ? Math.min(ids.length - 1, cur < 0 ? 0 : cur + 1)
          : Math.max(0, cur < 0 ? 0 : cur - 1);
        selectMeeting(ids[next]);
        const el = document.querySelector(`#meetings li[data-id="${ids[next]}"]`);
        if (el) el.scrollIntoView({ block: "nearest" });
      }
    }
  });

  await loadFacets();
  switchTab("home");
});
