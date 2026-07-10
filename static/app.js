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
  selectedOrgId: null,
  selectedTaskIdx: -1,
  facets: { groups: [], purposes: [], attendees: [], unaliased_raw_groups: [] },
  people: [],
  stats: null,
  meetingFilters: { group: "", purpose: "", attendee: "", dateFrom: "", dateTo: "", hasOpenTasks: false },
  billsFilter: { relationship: "all", q: "", congress: "current" },
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

  _renderFocusPanel(s.top_urgency || []);

  // Today's callouts summary card
  _refreshTodayCalloutsSummary();

  // Daily planning: auto-open on first visit each day
  const today = new Date().toISOString().slice(0, 10);
  if (localStorage.getItem("last_plan_date") !== today && s.open_count > 0) {
    openDailyPlan();
  }

  loadUpcomingMeetings();
}

async function loadUpcomingMeetings() {
  const card = $("#card-upcoming-meetings");
  const list = $("#upcoming-meetings-list");
  if (!card || !list) return;
  try {
    const data = await api("/api/meetings/upcoming");
    const meetings = data.meetings || [];
    if (!meetings.length) { card.style.display = "none"; return; }
    card.style.display = "";
    list.innerHTML = meetings.map((m) => {
      const dt = m.dtstart ? new Date(m.dtstart) : null;
      const dateStr = dt
        ? dt.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })
          + " · " + dt.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
        : "—";
      const organizer = m.organizer ? `<span class="upcoming-organizer">${escapeHtml(m.organizer)}</span>` : "";
      const attendees = Array.isArray(m.cal_attendees)
        ? m.cal_attendees.length
        : (typeof m.cal_attendees === "string" ? JSON.parse(m.cal_attendees || "[]").length : 0);
      const linkBtn = m.meeting_link
        ? `<a href="${escapeHtml(m.meeting_link)}" target="_blank" rel="noopener" class="upcoming-join-btn">Join</a>`
        : "";
      const statusBadge = m.status === "in_progress"
        ? `<span class="upcoming-badge in-progress">In progress</span>`
        : "";
      const dtLocal = dt
        ? new Date(dt.getTime() - dt.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
        : "";
      const editBtn = `<button class="upcoming-edit-btn" title="Edit meeting details"
              data-id="${escapeHtml(m.id)}"
              data-topic="${escapeHtml(m.topic || "")}"
              data-attendees="${escapeHtml(m.attendees || "")}"
              data-link="${escapeHtml(m.meeting_link || "")}"
              data-dtstart="${dtLocal}">✎</button>`;
      return `<div class="upcoming-item" data-id="${escapeHtml(m.id)}">
        <div class="upcoming-meta">${dateStr}${organizer ? " · " + organizer : ""}${attendees > 0 ? ` · ${attendees} attendees` : ""}</div>
        <div class="upcoming-title">${escapeHtml(m.topic || "Untitled Meeting")} ${statusBadge}</div>
        <div class="upcoming-actions">
          ${editBtn}
          ${linkBtn}
          <button class="cta-pill ghost upcoming-start-btn" data-id="${escapeHtml(m.id)}"
                  data-topic="${escapeHtml(m.topic || "")}"
                  data-attendees="${escapeHtml(m.attendees || "")}"
                  data-date="${m.dtstart ? new Date(m.dtstart).toISOString().slice(0,10) : ""}">
            ${m.status === "in_progress" ? "Continue Notes" : "Start Notes"}
          </button>
        </div>
      </div>`;
    }).join("");

    list.querySelectorAll(".upcoming-edit-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        openMeetingEditModal({
          id: btn.dataset.id,
          topic: btn.dataset.topic,
          attendees: btn.dataset.attendees,
          meeting_link: btn.dataset.link,
          dtstart: btn.dataset.dtstart,
        });
      });
    });

    list.querySelectorAll(".upcoming-start-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const mid = btn.dataset.id;
        await fetch(`/api/meetings/${mid}/status`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "in_progress" }),
        });
        openIntakeModalForMeeting({
          topic: btn.dataset.topic,
          attendees: btn.dataset.attendees,
          date: btn.dataset.date,
          preparedMeetingId: mid,
        });
      });
    });
  } catch (e) {
    card.style.display = "none";
  }
}

let _meetingEditId = null;
function openMeetingEditModal({ id, topic, attendees, meeting_link, dtstart } = {}) {
  _meetingEditId = id;
  $("#me-topic").value = topic || "";
  $("#me-dtstart").value = dtstart || "";
  $("#me-attendees").value = attendees || "";
  $("#me-link").value = meeting_link || "";
  $("#ics-meeting-edit-backdrop").classList.remove("hidden");
  setTimeout(() => $("#me-topic").focus(), 10);
}
function closeMeetingEditModal() {
  $("#ics-meeting-edit-backdrop").classList.add("hidden");
  _meetingEditId = null;
}
async function submitMeetingEditModal() {
  if (!_meetingEditId) return;
  // The datetime-local field holds a local wall-clock value with no timezone.
  // Convert it to a UTC ISO string so the TIMESTAMPTZ column stores the correct
  // instant (otherwise Postgres reads the naive value as UTC and shifts the time).
  const dtRaw = $("#me-dtstart").value;
  const dtstart = dtRaw ? new Date(dtRaw).toISOString() : null;
  const body = {
    topic: $("#me-topic").value.trim(),
    dtstart,
    attendees: $("#me-attendees").value.trim(),
    meeting_link: $("#me-link").value.trim(),
  };
  await api(`/api/meetings/${_meetingEditId}/metadata`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  closeMeetingEditModal();
  await loadUpcomingMeetings();
}

async function uploadICS(file) {
  const form = new FormData();
  form.append("file", file);
  const btn = $("#hero-upload-ics");
  const orig = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "Adding…"; }
  try {
    const res = await fetch("/api/calendar/upload", { method: "POST", body: form });
    const data = await res.json();
    if (data.ok && data.action !== "skipped") {
      loadUpcomingMeetings();
      if (btn) {
        btn.textContent = "Added!";
        setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, 2000);
      }
    } else if (data.action === "skipped") {
      if (btn) { btn.disabled = false; btn.textContent = orig; }
    } else {
      alert(data.error || "Could not parse ICS file.");
      if (btn) { btn.disabled = false; btn.textContent = orig; }
    }
  } catch (e) {
    alert("Upload failed: " + e.message);
    if (btn) { btn.disabled = false; btn.textContent = orig; }
  }
  // Reset file input so the same file can be re-uploaded if needed
  const inp = $("#ics-upload-input");
  if (inp) inp.value = "";
}

function openIntakeModalForMeeting({ topic, attendees, date, preparedMeetingId } = {}) {
  openIntakeModal();
  if (topic) $("#intake-topic").value = topic;
  if (attendees) $("#intake-attendees").value = attendees;
  if (date) $("#intake-date").value = date;
  if (preparedMeetingId) {
    let inp = $("#intake-prepared-meeting-id");
    if (!inp) {
      inp = document.createElement("input");
      inp.type = "hidden";
      inp.id = "intake-prepared-meeting-id";
      inp.name = "prepared_meeting_id";
      $(".modal-intake form, .modal-intake")?.appendChild(inp);
    }
    inp.value = preparedMeetingId;
  }
  // Skip type picker + pre-meeting form — metadata is already known from ICS
  _intakeMeetingType = "other";
  _intakeStartMeeting();
}

async function _refreshTodayCalloutsSummary() {
  const summaryEl = $("#today-callouts-summary");
  const breakdownEl = $("#today-callouts-breakdown");
  if (!summaryEl || !breakdownEl) return;
  try {
    const data = await api("/api/scan-items");
    const meetings = data.meetings || [];
    const allItems = meetings.flatMap((m) => m.items || []);
    if (!allItems.length) {
      summaryEl.textContent = "Nothing yet";
      breakdownEl.innerHTML = `<span class="chip-mini">No meetings recorded today</span>`;
      return;
    }
    const counts = {};
    for (const it of allItems) {
      counts[it.type] = (counts[it.type] || 0) + 1;
    }
    summaryEl.textContent = `${allItems.length} callout${allItems.length === 1 ? "" : "s"} · ${meetings.length} meeting${meetings.length === 1 ? "" : "s"}`;
    const order = ["task", "important", "followup", "ask", "commitment", "trigger", "deadline", "person", "bill"];
    breakdownEl.innerHTML = order
      .filter((k) => counts[k])
      .map((k) => `<span class="chip-mini"><strong>${counts[k]}</strong> ${_SCAN_LABELS[k] || k}</span>`)
      .join("");
  } catch (e) {
    summaryEl.textContent = "—";
    breakdownEl.innerHTML = "";
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
  if (t.callout_source && _SCAN_LABELS[t.callout_source]) {
    chips.push(
      `<span class="chip callout-chip callout-chip--${t.callout_source}" title="From handwritten ${_SCAN_LABELS[t.callout_source]}">${_SCAN_ICONS[t.callout_source] || ""}${_SCAN_LABELS[t.callout_source]}</span>`
    );
  }
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

  // Keep the phone segmented control in sync with the front paper
  const front = state.paperOrder[0];
  document.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.seg === front));
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
  // Populate person datalist + current value
  fillPersonDatalist("edit-m-person-list");
  if ($("#edit-m-person")) {
    const cur = state.people.find((p) => p.id === task.contact_id);
    $("#edit-m-person").value = cur ? cur.name : "";
  }
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
  // Person: "" clears; a typed name resolves to an existing contact or creates a new one.
  const personRaw = $("#edit-m-person")?.value.trim() ?? "";
  const personField = personRaw === "" ? "" : await ensurePersonId(personRaw);
  const payload = {
    id: _editTask.id,
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
  };
  payload.contact_id = personField;
  await api("/api/tasks/edit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  closeEditModal();
  await refreshTasks();
  if (state.meetings.length) refreshMeetings();
  if (state.selectedOrgId) selectOrg(state.selectedOrgId, { skipToggle: true });
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

    const cs = task.callout_source;
    const sourceBadge = cs
      ? `<span class="callout-badge callout-badge--${cs}" title="From handwritten ${_SCAN_LABELS[cs] || cs}">
           ${_SCAN_ICONS[cs] || ""}<span>${escapeHtml(_SCAN_LABELS[cs] || cs)}</span>
         </span>`
      : "";

    // Commitment badge
    let commitmentBadge = "";
    if (task.commitment_id) {
      commitmentBadge = `<span class="callout-badge callout-badge--commitment" title="Created from a commitment">${_SCAN_ICONS.commitment || ""}${escapeHtml(_SCAN_LABELS.commitment)}</span>`;
    }
    // Ask badge
    let askBadge = "";
    if (task.ask_id) {
      askBadge = `<span class="callout-badge callout-badge--ask" title="Linked to an ask">${_SCAN_ICONS.ask || ""}${escapeHtml(_SCAN_LABELS.ask)}</span>`;
    }

    const bills = (m.bill_references || []).map((b) =>
      `<span class="bill-pill">${escapeHtml(b.bill_type)} ${escapeHtml(b.bill_number)}</span>`
    ).join("");
    const billsBlock = bills
      ? `<div class="drawer-bills"><div class="drawer-section-label">Bills referenced</div>${bills}</div>`
      : "";

    const cards = (m.contacts || []).map((c) => `
      <div class="drawer-card">
        ${c.card_image ? `<img class="drawer-card-thumb" src="${c.card_image}" alt="Business card">` : ""}
        <div class="drawer-card-info">
          <div class="drawer-card-name">${escapeHtml(c.name || "(no name)")}</div>
          ${c.title || c.company ? `<div class="drawer-card-sub">${escapeHtml([c.title, c.company].filter(Boolean).join(" · "))}</div>` : ""}
          ${c.email ? `<div class="drawer-card-sub">${escapeHtml(c.email)}</div>` : ""}
          ${c.phone ? `<div class="drawer-card-sub">${escapeHtml(c.phone)}</div>` : ""}
        </div>
      </div>
    `).join("");
    const cardsBlock = cards
      ? `<div class="drawer-cards"><div class="drawer-section-label">Cards from this meeting</div>${cards}</div>`
      : "";

    el.innerHTML = `
      <header>
        <h1>${escapeHtml(m.group)}${m.topic ? ` — <span style="color:var(--muted); font-weight:400">${escapeHtml(m.topic)}</span>` : ""}</h1>
        <div class="meta">${meta.join("")}</div>
        ${sourceBadge}${commitmentBadge}${askBadge}
        <a href="#" class="open-full" data-mid="${m.id}">Open full meeting view →</a>
      </header>
      ${m.canvas_image ? `<img class="canvas-note-image" src="${m.canvas_image}" alt="Handwritten note">` : ""}
      <div class="body">${m.body_html}</div>
      ${billsBlock}
      ${cardsBlock}
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

function _nlMonthOptions(sel) {
  const months = [["01","Jan"],["02","Feb"],["03","Mar"],["04","Apr"],["05","May"],["06","Jun"],["07","Jul"],["08","Aug"],["09","Sep"],["10","Oct"],["11","Nov"],["12","Dec"]];
  return `<option value="">Month</option>` + months.map(([v, l]) => `<option value="${v}"${v === sel ? " selected" : ""}>${l}</option>`).join("");
}
function _nlDayOptions(sel) {
  let o = `<option value="">Day</option>`;
  for (let d = 1; d <= 31; d++) { const v = String(d).padStart(2, "0"); o += `<option value="${v}"${v === sel ? " selected" : ""}>${d}</option>`; }
  return o;
}
function _nlYearOptions(sel) {
  const ty = new Date().getFullYear();
  return `<option value="">Year</option>` + [ty - 1, ty, ty + 1, ty + 2].map((y) => `<option value="${y}"${String(y) === sel ? " selected" : ""}>${y}</option>`).join("");
}

function _populateNLStep2(parsed) {
  // Deadline parts (matches the edit modal's month/day/year selects)
  let dlMo = "", dlDd = "", dlYy = "";
  if (parsed.deadline && /^\d{4}-\d{2}-\d{2}$/.test(parsed.deadline)) {
    [dlYy, dlMo, dlDd] = parsed.deadline.split("-");
  }
  const allGroups = state.tasksGroupsInScope.concat(state.facets.groups).filter((v, i, a) => a.indexOf(v) === i);
  const groupOpts = allGroups.map((g) => `<option value="${escapeHtml(g)}"></option>`).join("");
  const contactVal = [parsed.email, parsed.phone, parsed.contact].filter(Boolean).join(", ");

  const blocks = [
    { uncertain: false, html: `<label>Priority</label>
      <select id="nl-f-priority">${[["normal","Normal"],["high","High"],["low","Low"]].map(([v, l]) => `<option value="${v}"${v === parsed.priority ? " selected" : ""}>${l}</option>`).join("")}</select>` },
    { uncertain: !!parsed.groupUncertain, html: `<label>Organization${parsed.groupUncertain ? ' <span class="nl-check-this">check this</span>' : ""}</label>
      <input id="nl-f-group" list="nl-f-group-list" value="${escapeHtml(parsed.group || "")}" placeholder="Pick or type new" autocomplete="off">
      <datalist id="nl-f-group-list">${groupOpts}</datalist>` },
    { uncertain: false, html: `<label>Person</label>
      <input id="nl-f-person" list="nl-f-person-list" value="" placeholder="Assign to a person" autocomplete="off">
      <datalist id="nl-f-person-list">${state.people.map((p) => `<option value="${escapeHtml(p.name)}"></option>`).join("")}</datalist>` },
    { uncertain: false, html: `<label>Deadline</label>
      <div class="deadline-selects">
        <select id="nl-f-dl-month">${_nlMonthOptions(dlMo)}</select>
        <select id="nl-f-dl-day">${_nlDayOptions(dlDd)}</select>
        <select id="nl-f-dl-year">${_nlYearOptions(dlYy)}</select>
      </div>
      <div class="dl-quick-btns">
        <button type="button" class="dl-quick-btn" data-quick="today" data-prefix="nl-f">Today</button>
        <button type="button" class="dl-quick-btn" data-quick="this-week" data-prefix="nl-f">This week</button>
        <button type="button" class="dl-quick-btn" data-quick="next-week" data-prefix="nl-f">Next week</button>
      </div>` },
    { uncertain: false, html: `<label>Phone / email</label>
      <input id="nl-f-contact" value="${escapeHtml(contactVal)}" placeholder="Phone number or email" autocomplete="off">` },
    { uncertain: false, html: `<label>Estimate (min)</label>
      <input id="nl-f-estimate" type="number" min="1" max="480" value="${parsed.estimate_minutes || ""}" placeholder="e.g. 30" autocomplete="off">` },
  ];

  const container = $("#nl-parsed-fields");
  container.innerHTML = blocks.map((b, i) => `<div class="nl-field nl-field-blur${b.uncertain ? " nl-field-uncertain" : ""}" style="transition-delay:${i * 55}ms">
      ${b.html}
    </div>`).join("");

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
  const deadline = getDeadlineValue("nl-f") || "";
  const group = $("#nl-f-group")?.value.trim() || "";
  const contact = $("#nl-f-contact")?.value.trim() || null;
  const contact_id = await ensurePersonId($("#nl-f-person")?.value);
  const estimateRaw = parseInt($("#nl-f-estimate")?.value);
  const estimate_minutes = isNaN(estimateRaw) ? null : estimateRaw;

  await api("/api/tasks/add", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, group, deadline, priority, contact, contact_id, estimate_minutes }),
  });
  closeNLModal();
  if (state.tab === "tasks") await refreshTasks();
  if (state.tab === "home")  await renderHome();
  await loadFacets();
}

// ---------- (legacy alias — kept for any old references) ----------
const openAddModal = openNLModal;
const closeAddModal = closeNLModal;

function updateRelDepth() {
  const layout = document.querySelector(".rel-layout");
  if (!layout) return;
  const depth = state.selectedMeetingId !== null ? 2
              : state.selectedOrgId    !== null ? 1
              : 0;
  layout.classList.remove("depth-0", "depth-1", "depth-2");
  layout.classList.add("depth-" + depth);
}

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
  if (m.attendees) {
    const norm = (s) => s.trim().toLowerCase();
    const byName = new Map((m.contacts || []).map((c) => [norm(c.name || ""), c.id]));
    const chips = m.attendees.split(/[;,]/).map((a) => a.trim()).filter(Boolean).map((a) => {
      const cid = byName.get(norm(a));
      return cid
        ? `<button class="attendee-chip attendee-chip--link" data-person-id="${escapeHtml(cid)}">${escapeHtml(a)}</button>`
        : `<span class="attendee-chip">${escapeHtml(a)}</span>`;
    }).join("");
    meta.push(`<span class="attendee-chips">👥 ${chips}</span>`);
  }
  if (m.deadline)      meta.push(`<span>⏰ ${escapeHtml(m.deadline)}</span>`);
  if (m.outcome)       meta.push(`<span>→ ${escapeHtml(m.outcome)}</span>`);
  if (m.raw_group && m.raw_group !== m.group)
    meta.push(`<span style="color:var(--muted)"><em>raw: ${escapeHtml(m.raw_group)}</em></span>`);

  const taskById = (text, type) =>
    (m.tasks_full || []).find((t) => t.text === text && t.type === type);

  const listOrNone = (items, type) =>
    items?.length
      ? `<ul>${items.map((i) => {
          const t = taskById(i, type);
          const tid = t ? `data-task-id="${escapeHtml(t.id)}"` : "";
          return `<li class="callout-item" ${tid}><span class="callout-item-text">${escapeHtml(i)}</span><button class="callout-edit-btn" title="Edit">✎</button></li>`;
        }).join("")}</ul>`
      : `<p style="color:var(--muted); margin:4px 0 8px;">None</p>`;

  $("#detail").innerHTML = `
    <header>
      <button class="detail-back" title="Back">← Back</button>
      <div class="detail-header-row">
        <h1>${escapeHtml(m.group)}${m.topic ? ` — <span style="color:var(--muted); font-weight:400">${escapeHtml(m.topic)}</span>` : ""}</h1>
        <div style="display: flex; gap: 8px;">
          <button class="detail-edit-btn" data-mid="${escapeHtml(m.id)}" title="Edit meeting metadata">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L21 3"/></svg>
            Edit
          </button>
          <button class="detail-delete-btn" data-mid="${escapeHtml(m.id)}" title="Delete note">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/></svg>
            Delete
          </button>
        </div>
      </div>
      <div class="meta">${meta.join("")}</div>
    </header>
    ${m.canvas_image ? `<img class="canvas-note-image" src="${m.canvas_image}" alt="Handwritten note" title="Tap to expand">` : ""}
    <div class="tasks-panel">
      <h3>Open Action Items</h3>
      ${listOrNone(m.action_items_open, "action")}
      <h3>Open Reminders</h3>
      ${listOrNone(m.reminders_open, "reminder")}
    </div>
    ${(m.bill_references || []).length ? `
    <div class="detail-bills">
      <h3>Bills Referenced</h3>
      <div class="detail-bill-list">
        ${(m.bill_references || []).map((b) => `
        <span class="bill-pill bill-pill--editable" data-bill-id="${b.id}" data-bill-type="${escapeHtml(b.bill_type)}" data-bill-number="${escapeHtml(b.bill_number)}">
          <span class="bill-pill-text">${escapeHtml(b.bill_type)} ${escapeHtml(b.bill_number)}</span>
          <button class="bill-edit-btn" title="Edit">✎</button>
        </span>`).join("")}
      </div>
    </div>` : ""}
    <div class="detail-contacts">
      <div class="detail-contacts-head">
        <h3>Contacts</h3>
        <button class="detail-contact-add-btn" type="button">+ Add contact</button>
      </div>
      <div class="detail-contact-list">
        ${(m.contacts || []).map((c) => `
        <div class="detail-contact-card" data-cid="${escapeHtml(c.id)}" data-mid="${escapeHtml(m.id)}">
          <div class="detail-contact-info">
            <div class="detail-contact-name">${escapeHtml(c.name)}</div>
            ${c.title || c.company ? `<div class="detail-contact-sub">${escapeHtml([c.title, c.company].filter(Boolean).join(" · "))}</div>` : ""}
            ${c.email ? `<div class="detail-contact-meta">${escapeHtml(c.email)}</div>` : ""}
            ${c.phone ? `<div class="detail-contact-meta">${escapeHtml(c.phone)}</div>` : ""}
          </div>
          <button class="detail-contact-unlink" title="Unlink contact">✕</button>
        </div>`).join("")}
      </div>
      <div class="contact-picker hidden">
        <input class="contact-picker-input" type="text"
               placeholder="Search a contact, or type a new name…" autocomplete="off">
        <div class="contact-picker-results"></div>
      </div>
    </div>
    <div class="body">${m.body_html}</div>
  `;
  const ci = $("#detail .canvas-note-image");
  if (ci) ci.addEventListener("click", () => _openCanvasFullscreen(ci.src));

  $("#detail").querySelectorAll(".attendee-chip--link").forEach((chip) => {
    chip.addEventListener("click", () => toggleDetailPersonCard(chip, chip.dataset.personId));
  });

  $("#detail .detail-back")?.addEventListener("click", () => {
    if (state.selectedMeetingId != null) selectMeeting(state.selectedMeetingId);
  });

  // Click a contact card (but not its ✕) to open the full editor inline.
  $("#detail").querySelectorAll(".detail-contact-card").forEach((card) => {
    card.addEventListener("click", (e) => {
      if (e.target.closest(".detail-contact-unlink")) return;
      toggleDetailPersonCard(card, card.dataset.cid);
    });
  });

  $("#detail").querySelectorAll(".detail-contact-unlink").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const card = btn.closest(".detail-contact-card");
      const { cid, mid } = card.dataset;
      await fetch(`/api/meetings/${mid}/contacts/${cid}`, { method: "DELETE" });
      selectMeeting(mid);
    });
  });

  _wireContactPicker(m);

  $("#detail").querySelectorAll(".callout-edit-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const li = btn.closest(".callout-item");
      const taskId = li.dataset.taskId;
      const textSpan = li.querySelector(".callout-item-text");
      const oldText = textSpan.textContent;
      const input = document.createElement("input");
      input.type = "text";
      input.value = oldText;
      input.className = "callout-inline-input";
      textSpan.replaceWith(input);
      btn.textContent = "✓";
      btn.title = "Save";
      input.focus();
      input.select();

      const save = async () => {
        const newText = input.value.trim();
        if (!newText || newText === oldText) { selectMeeting(m.id); return; }
        const body = { new_text: newText };
        if (taskId) body.id = taskId;
        else body.old_text = oldText;
        await fetch("/api/tasks/edit", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        selectMeeting(m.id);
      };

      btn.onclick = (e) => { e.stopPropagation(); save(); };
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") save();
        if (e.key === "Escape") selectMeeting(m.id);
      });
    });
  });

  $("#detail").querySelectorAll(".bill-edit-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const pill = btn.closest(".bill-pill--editable");
      const billId = pill.dataset.billId;
      const textSpan = pill.querySelector(".bill-pill-text");
      const typeInput = document.createElement("input");
      typeInput.type = "text";
      typeInput.value = pill.dataset.billType;
      typeInput.className = "callout-inline-input";
      typeInput.style.width = "48px";
      const numInput = document.createElement("input");
      numInput.type = "text";
      numInput.value = pill.dataset.billNumber;
      numInput.className = "callout-inline-input";
      numInput.style.width = "80px";
      textSpan.replaceWith(typeInput, document.createTextNode(" "), numInput);
      btn.textContent = "✓";
      typeInput.focus();
      typeInput.select();

      const save = async () => {
        const bt = typeInput.value.trim();
        const bn = numInput.value.trim();
        if (!bn) { selectMeeting(m.id); return; }
        await fetch(`/api/bills/${billId}`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ bill_type: bt, bill_number: bn }),
        });
        selectMeeting(m.id);
      };

      btn.onclick = (e) => { e.stopPropagation(); save(); };
      [typeInput, numInput].forEach((inp) => inp.addEventListener("keydown", (e) => {
        if (e.key === "Enter") save();
        if (e.key === "Escape") selectMeeting(m.id);
      }));
    });
  });
}

// Inline picker on the meeting detail to attach a contact after the fact.
// Searches existing contacts (link) or creates a new one inline (create + link).
function _wireContactPicker(m) {
  const section = $("#detail .detail-contacts");
  if (!section) return;
  const addBtn = section.querySelector(".detail-contact-add-btn");
  const picker = section.querySelector(".contact-picker");
  const input = section.querySelector(".contact-picker-input");
  const results = section.querySelector(".contact-picker-results");
  if (!addBtn || !picker || !input || !results) return;

  const linkedIds = new Set((m.contacts || []).map((c) => c.id));

  const onDocClick = (e) => {
    if (!section.contains(e.target)) close();
  };

  const close = () => {
    picker.classList.add("hidden");
    results.innerHTML = "";
    input.value = "";
    document.removeEventListener("click", onDocClick);
  };

  const linkContact = async (cid) => {
    await fetch(`/api/meetings/${m.id}/contacts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ contact_id: cid }),
    });
    selectMeeting(m.id);
  };

  const createAndLink = async (name) => {
    const res = await fetch("/api/contacts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const data = await res.json();
    if (!data.ok || !data.id) { alert(data.error || "Could not create contact"); return; }
    await linkContact(data.id);
  };

  const render = (matches, q) => {
    const rows = matches
      .filter((c) => !linkedIds.has(c.id))
      .map((c) => {
        const sub = [c.title, c.company].filter(Boolean).join(" · ");
        return `<div class="contact-picker-row" data-cid="${escapeHtml(c.id)}">
          <span class="contact-picker-row-name">${escapeHtml(c.name)}</span>
          ${sub ? `<span class="contact-picker-row-sub">${escapeHtml(sub)}</span>` : ""}
        </div>`;
      });
    if (q) {
      rows.push(`<div class="contact-picker-row contact-picker-create" data-create="1">
        + Create &ldquo;${escapeHtml(q)}&rdquo;</div>`);
    }
    results.innerHTML = rows.join("");

    results.querySelectorAll(".contact-picker-row").forEach((row) => {
      row.addEventListener("click", () => {
        if (row.dataset.create) createAndLink(q);
        else linkContact(row.dataset.cid);
      });
    });
  };

  let timer = null;
  const search = async () => {
    const q = input.value.trim();
    if (!q) { results.innerHTML = ""; return; }
    try {
      const res = await fetch(`/api/contacts?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      const list = Array.isArray(data) ? data : (data.contacts || []);
      render(list, q);
    } catch {
      render([], q);
    }
  };

  addBtn.addEventListener("click", (e) => {
    const wasHidden = picker.classList.contains("hidden");
    if (wasHidden) {
      e.stopPropagation();
      picker.classList.remove("hidden");
      input.focus();
      document.addEventListener("click", onDocClick);
    } else {
      close();
    }
  });

  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(search, 200);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); close(); }
  });
}

// Meeting edit modal
let _meetingEditGroups = null;
async function _loadCanonicalGroups() {
  if (_meetingEditGroups) return _meetingEditGroups;
  try {
    const res = await fetch("/api/groups/canonical");
    const data = await res.json();
    _meetingEditGroups = data.groups || [];
    return _meetingEditGroups;
  } catch {
    return [];
  }
}

async function _openMeetingEditModal(mid) {
  const m = state.meetings.find((x) => x.id === mid);
  if (!m) return;
  const backdrop = $("#meeting-edit-backdrop");
  const groupInput = $("#meeting-edit-group");
  const topicInput = $("#meeting-edit-topic");
  const attendeesInput = $("#meeting-edit-attendees");
  const deadlineInput = $("#meeting-edit-deadline");
  const outcomeInput = $("#meeting-edit-outcome");

  groupInput.value = m.raw_group || m.group || "";
  topicInput.value = m.topic || "";
  attendeesInput.value = m.attendees || "";
  deadlineInput.value = m.deadline || "";
  outcomeInput.value = m.outcome || "";

  // Load and populate canonical groups datalist
  const groups = await _loadCanonicalGroups();
  const datalist = $("#meeting-edit-group-list");
  datalist.innerHTML = groups.map(g => `<option value="${escapeHtml(g)}">`).join("");

  backdrop.classList.remove("hidden");
  groupInput.focus();
  groupInput.dataset.mid = mid;
}

async function _saveMeetingEdit() {
  const groupInput = $("#meeting-edit-group");
  const mid = groupInput.dataset.mid;
  const group = groupInput.value.trim();
  if (!group) { alert("Group is required"); return; }

  const btn = $("#meeting-edit-save");
  btn.disabled = true;
  btn.textContent = "Saving…";
  try {
    const res = await fetch(`/api/meetings/${mid}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        group: group,
        topic: $("#meeting-edit-topic").value.trim(),
        attendees: $("#meeting-edit-attendees").value.trim(),
        deadline: $("#meeting-edit-deadline").value.trim(),
        outcome: $("#meeting-edit-outcome").value.trim(),
      }),
    });
    const data = await res.json();
    if (!data.ok) { alert(data.error || "Save failed"); btn.disabled = false; return; }

    // Close modal and refresh (clear group cache so datalist reflects new names)
    _meetingEditGroups = null;
    $("#meeting-edit-backdrop").classList.add("hidden");
    await refreshMeetings();
    selectMeeting(mid);
    btn.disabled = false;
    btn.textContent = "Save";
  } catch (e) {
    alert("Save failed");
    btn.disabled = false;
    btn.textContent = "Save";
  }
}

// Delete note handler (delegated from #detail)
$("#detail").addEventListener("click", async (e) => {
  const deleteBtn = e.target.closest(".detail-delete-btn");
  if (deleteBtn) {
    const mid = deleteBtn.dataset.mid;
    if (!confirm("Delete this note? This also removes all its tasks.")) return;
    deleteBtn.disabled = true;
    deleteBtn.textContent = "Deleting…";
    try {
      const res = await fetch(`/api/meetings/${mid}`, { method: "DELETE" });
      const data = await res.json();
      if (!data.ok) { alert(data.error || "Delete failed"); deleteBtn.disabled = false; return; }
      state.selectedMeetingId = null;
      renderDetail(null);
      await refreshMeetings();
      await loadFacets();
    } catch {
      alert("Delete failed");
      deleteBtn.disabled = false;
    }
    return;
  }

  const editBtn = e.target.closest(".detail-edit-btn");
  if (editBtn) {
    const mid = editBtn.dataset.mid;
    await _openMeetingEditModal(mid);
    return;
  }
});

function renderGroupsTable(groups) {
  // Legacy function kept for compatibility — now delegates to org table
  renderOrgsTable(groups.map((g) => ({
    id: g.group,
    name: g.group,
    meeting_count: g.meeting_count,
    last_meeting: g.last_contact,
    open_asks: 0,
    open_commitments: 0,
    open_tasks: g.open_action_items || 0,
  })));
}

function renderOrgsTable(orgs) {
  const tbody = $("#orgs-body");
  if (!tbody) return;
  if (!orgs.length) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:30px;">No organizations yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = orgs.map((o) => `
    <tr class="stakeholder-row" data-org-id="${escapeHtml(o.id)}" style="cursor:pointer">
      <td><strong>${escapeHtml(o.name)}</strong></td>
      <td class="num">${o.meeting_count || 0}</td>
      <td>${o.last_meeting ? escapeHtml(o.last_meeting) : "—"}</td>
      <td class="num">${o.open_asks ? `<span class="count-badge count-badge--ask">${o.open_asks}</span>` : "—"}</td>
      <td class="num">${o.open_commitments ? `<span class="count-badge count-badge--commitment">${o.open_commitments}</span>` : "—"}</td>
      <td class="num">${o.open_tasks || "—"}</td>
    </tr>
  `).join("");
  tbody.querySelectorAll(".stakeholder-row").forEach((row) => {
    row.addEventListener("click", () => selectOrg(row.dataset.orgId));
  });
}

async function selectOrg(orgId, { skipToggle = false } = {}) {
  // Toggle-deselect: clicking active org collapses the panel
  if (!skipToggle && orgId && orgId === state.selectedOrgId) {
    orgId = null;
  }

  // Immediate visual feedback so the click feels responsive while we fetch.
  $$("#orgs-body .stakeholder-row").forEach((r) =>
    r.classList.toggle("active", orgId && r.dataset.orgId === orgId));

  const label = $("#rel-meetings-label");
  const badges = $("#rel-org-badges");
  const orgDetail = $("#rel-org-detail");
  const content = $("#org-detail-content");

  if (!orgId) {
    // Collapse: flip depth first so panels slide out, then clear content
    // after the transition completes — keeps content visible during slide-out.
    state.selectedOrgId = null;
    state.selectedMeetingId = null;
    state.meetingFilters.group = "";
    updateRelDepth();
    setTimeout(() => {
      if (state.selectedOrgId !== null) return;  // user re-selected mid-animation
      if (label) label.textContent = "All Meetings";
      if (badges) badges.innerHTML = "";
      if (orgDetail) orgDetail.classList.add("hidden");
      const ml = $("#meetings");
      if (ml) ml.style.display = "";
      renderDetail(null);
      refreshMeetingsNow();
    }, 300);
    return;
  }

  try {
    // Fetch org data first — panels stay hidden until everything is ready.
    const org = await api(`/api/organizations/${orgId}`);
    const openAsks = (org.asks || []).filter((a) =>
      !["completed","declined","no_action"].includes(a.status));
    const openCommits = (org.commitments || []).filter((c) =>
      ["open","needs_review","task_created"].includes(c.status));
    const openTasks = org.open_tasks || [];
    const completedTasks = org.completed_tasks || [];

    // Filter the meeting list and re-render it (still inside hidden col-2).
    state.meetingFilters.group = org.name;
    await refreshMeetingsNow();

    // Build org detail panel (asks / commitments / tasks / bills / contacts)
    const asksHtml = openAsks.length
      ? openAsks.map((a) => `
          <div class="org-entity-row">
            <span class="callout-badge callout-badge--ask">${_SCAN_ICONS.ask}Ask</span>
            <span class="entity-text">${escapeHtml(a.text)}</span>
            <span class="entity-status status-${a.status}">${escapeHtml(a.status.replace("_"," "))}</span>
            <button class="entity-status-btn" data-ask-id="${a.id}" data-status="completed">✓</button>
            <button class="entity-edit-btn" data-ask-edit="${a.id}" title="Edit">✎</button>
            <button class="entity-del-btn" data-ask-del="${a.id}" title="Delete">🗑</button>
          </div>`).join("")
      : "";

    const commitsHtml = openCommits.length
      ? openCommits.map((c) => `
          <div class="org-entity-row">
            <span class="callout-badge callout-badge--commitment">${_SCAN_ICONS.commitment}Commitment</span>
            <span class="entity-text">${escapeHtml(c.text)}</span>
            <span class="entity-status status-${c.status}">${escapeHtml(c.status.replace("_"," "))}</span>
            ${!c.task_id ? `<button class="create-task-btn" data-commit-id="${c.id}">+ Task</button>` : ""}
            <button class="entity-edit-btn" data-commit-edit="${c.id}" title="Edit">✎</button>
            <button class="entity-del-btn" data-commit-del="${c.id}" title="Delete">🗑</button>
          </div>`).join("")
      : "";

    const tasksHtml = openTasks.length
      ? openTasks.map((t) => {
          const deadline = t.deadline ? `<span class="entity-status">${escapeHtml(t.deadline)}</span>` : "";
          const priority = t.priority === "high" ? `<span class="entity-status status-task_created">high</span>` : "";
          return `<div class="org-entity-row org-entity-row--task" data-task-id="${escapeHtml(t.id)}" data-contact-id="${escapeHtml(t.contact_id || "")}">
            <span class="callout-badge callout-badge--task">${_SCAN_ICONS.task}Task</span>
            <span class="entity-text">${escapeHtml(t.text)}</span>${deadline}${priority}
            <button class="entity-status-btn" data-task-toggle="${escapeHtml(t.id)}" title="Mark complete">✓</button>
            <button class="entity-edit-btn" data-task-edit="${escapeHtml(t.id)}" title="Edit">✎</button>
            <button class="entity-del-btn" data-task-del="${escapeHtml(t.id)}" title="Delete">🗑</button>
          </div>`;
        }).join("")
      : "";

    const completedTasksHtml = completedTasks.length
      ? `<details class="org-completed-tasks">
           <summary>Completed tasks (${completedTasks.length})</summary>
           ${completedTasks.map((t) => `
             <div class="org-entity-row org-entity-row--task org-entity-row--done" data-task-id="${escapeHtml(t.id)}" data-contact-id="${escapeHtml(t.contact_id || "")}">
               <span class="callout-badge callout-badge--task">${_SCAN_ICONS.task}Task</span>
               <span class="entity-text">${escapeHtml(t.text)}</span>
               <button class="entity-status-btn" data-task-toggle="${escapeHtml(t.id)}" title="Mark not done">↺</button>
               <button class="entity-del-btn" data-task-del="${escapeHtml(t.id)}" title="Delete">🗑</button>
             </div>`).join("")}
         </details>`
      : "";

    const billsHtml = (org.bills || []).map((b) =>
      `<span class="bill-pill">${escapeHtml(b.bill_type)} ${escapeHtml(b.bill_number)}</span>`
    ).join("");

    const contactsHtml = (org.contacts || []).map((c) => `
      <div class="drawer-card org-person-card" data-person-id="${escapeHtml(c.id)}" style="cursor:pointer">
        ${c.card_image ? `<img class="drawer-card-thumb" src="${c.card_image}" alt="">` : ""}
        <div class="drawer-card-info">
          <div class="drawer-card-name">${escapeHtml(c.name || "(no name)")}</div>
          ${c.title || c.company ? `<div class="drawer-card-sub">${escapeHtml([c.title,c.company].filter(Boolean).join(" · "))}</div>` : ""}
          ${c.email ? `<div class="drawer-card-sub">${escapeHtml(c.email)}</div>` : ""}
        </div>
        <button class="chip-remove org-person-unlink" data-unlink-person="${escapeHtml(c.id)}" title="Remove from this organization" aria-label="Remove from this organization">×</button>
      </div>`).join("");

    // Filter-by-person control for the task sections (only if there are people + tasks)
    const hasTasks = openTasks.length || completedTasks.length;
    const personFilterHtml = (hasTasks && (org.contacts || []).length)
      ? `<div class="org-task-filter">
           <label>Filter tasks by person</label>
           <select id="org-task-person-filter">
             <option value="">Everyone</option>
             ${(org.contacts || []).map((c) => `<option value="${escapeHtml(c.id)}">${escapeHtml(c.name || "(no name)")}</option>`).join("")}
           </select>
         </div>`
      : "";

    const orgEditHtml = `
      <div class="org-detail-header">
        <input class="org-edit-name" id="org-edit-name" value="${escapeHtml(org.name || "")}" placeholder="Organization name">
        <button class="detail-delete-btn" id="org-delete" title="Delete organization">Delete</button>
      </div>
      <div class="org-detail-section org-edit-fields">
        <input id="org-edit-type" placeholder="Type (e.g. trade association)" value="${escapeHtml(org.type || "")}">
        <textarea id="org-edit-notes" placeholder="Notes…">${escapeHtml(org.notes || "")}</textarea>
        <button class="cta-pill ghost" id="org-edit-save">Save details</button>
      </div>`;

    // Side rail = current state + associations (kept actionable). Completed tasks are
    // dropped here — they live in the timeline as their own events.
    const railSection = (lbl, html) =>
      html ? `<div class="org-detail-section"><div class="drawer-section-label">${lbl}</div>${html}</div>` : "";
    const railHtml = [
      railSection("Open Asks", asksHtml),
      railSection("Open Commitments", commitsHtml),
      personFilterHtml,
      railSection("Open Tasks", tasksHtml),
      completedTasksHtml,
      billsHtml && railSection("Bills", `<div class="bill-pills-row">${billsHtml}</div>`),
      contactsHtml && railSection("People", `<div class="drawer-cards">${contactsHtml}</div>`),
      _entityNotesHtml(org.entity_notes),
    ].filter(Boolean).join("");

    // Record page: read-only header, edit hidden behind a button, timeline + rail body.
    const recordHtml = `
      <div class="org-record">
        <div class="org-record-topbar">
          ${org.type ? `<span class="org-type-chip">${escapeHtml(org.type)}</span>` : ""}
          <button class="org-edit-toggle" id="org-edit-toggle" title="Edit organization details">Edit</button>
        </div>
        <div class="org-edit-panel hidden" id="org-edit-panel">${orgEditHtml}</div>
        <div class="org-record-body">
          <div class="org-timeline-wrap">
            <div class="drawer-section-label">Activity</div>
            <div class="org-timeline" id="org-timeline">
              <div class="detail-empty-inline">Loading timeline…</div>
            </div>
          </div>
          <aside class="org-record-rail">${railHtml || `<p class="detail-empty-inline">No open items.</p>`}</aside>
        </div>
      </div>`;

    // Header label + read-only quick stats.
    if (label) label.textContent = org.name;
    if (badges) {
      const stat = (n, w) => n ? `${n} ${w}${n > 1 ? "s" : ""}` : "";
      const parts = [
        stat((org.meetings || []).length, "meeting"),
        stat(openAsks.length, "ask"),
        stat(openCommits.length, "commitment"),
        stat(openTasks.length, "task"),
      ].filter(Boolean);
      badges.innerHTML = parts.map((p) => `<span class="rel-org-badge">${escapeHtml(p)}</span>`).join("");
    }
    if (content) content.innerHTML = recordHtml;
    if (orgDetail) orgDetail.classList.remove("hidden");
    // The old flat meeting list is superseded by the timeline.
    const meetingsList = $("#meetings");
    if (meetingsList) meetingsList.style.display = "none";

    // Edit button reveals the (otherwise hidden) name/type/notes editor.
    $("#org-edit-toggle")?.addEventListener("click", () => {
      $("#org-edit-panel")?.classList.toggle("hidden");
    });

    // Build the activity timeline (async; the rail is already visible).
    const orgTaskLookup = new Map([...openTasks, ...completedTasks].map((t) => [t.id, t]));
    renderOrgTimeline(orgId, $("#org-timeline"), orgTaskLookup, () => selectOrg(orgId, { skipToggle: true }));

    // Standalone org notes
    if (content) _wireEntityNotes(content, "organization", orgId, () => selectOrg(orgId, { skipToggle: true }));

    // Filter task rows by person
    content?.querySelector("#org-task-person-filter")?.addEventListener("change", (e) => {
      const cid = e.target.value;
      content.querySelectorAll(".org-entity-row--task").forEach((row) => {
        row.classList.toggle("hidden", !!cid && row.dataset.contactId !== cid);
      });
    });

    // Person cards → open that person on the People subtab
    content?.querySelectorAll(".org-person-card[data-person-id]").forEach((card) => {
      card.addEventListener("click", () => {
        document.querySelectorAll(".groups-subtab").forEach((b) => b.classList.remove("active"));
        document.querySelector(".groups-subtab[data-subtab='people']")?.classList.add("active");
        document.querySelectorAll(".groups-panel").forEach((p) => p.classList.add("hidden"));
        $("#groups-panel-people")?.classList.remove("hidden");
        selectPerson(card.dataset.personId);
      });
    });

    // Wire interactive buttons
    content?.querySelectorAll(".entity-status-btn[data-ask-id]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await api(`/api/asks/${btn.dataset.askId}/status`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: btn.dataset.status }),
        });
        selectOrg(orgId, { skipToggle: true });
      });
    });
    content?.querySelectorAll(".create-task-btn").forEach((btn) => {
      btn.addEventListener("click", async () => {
        await api(`/api/commitments/${btn.dataset.commitId}/create-task`, {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}),
        });
        selectOrg(orgId, { skipToggle: true });
        await refreshTasks();
      });
    });
    content?.querySelectorAll(".org-entity-row--task").forEach((row) => {
      row.addEventListener("click", () => {
        const task = [...openTasks, ...completedTasks].find((t) => t.id === row.dataset.taskId);
        if (task) openDrawer(task);
      });
    });

    // Complete / uncomplete, edit, delete tasks directly from the org rail.
    content?.querySelectorAll("[data-task-toggle]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const task = [...openTasks, ...completedTasks].find((t) => t.id === btn.dataset.taskToggle);
        if (!task) return;
        await toggleTaskDone(task);
        selectOrg(orgId, { skipToggle: true });
      });
    });
    content?.querySelectorAll("[data-task-edit]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const task = [...openTasks, ...completedTasks].find((t) => t.id === btn.dataset.taskEdit);
        if (task) openEditModal(task);
      });
    });
    content?.querySelectorAll("[data-task-del]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm("Delete this task?")) return;
        const task = [...openTasks, ...completedTasks].find((t) => t.id === btn.dataset.taskDel);
        if (!task) return;
        await deleteTask(task);
        selectOrg(orgId, { skipToggle: true });
      });
    });

    // Unlink a person from this organization
    content?.querySelectorAll("[data-unlink-person]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm("Remove this person from this organization?")) return;
        await api(`/api/people/${btn.dataset.unlinkPerson}/organizations/${orgId}`, { method: "DELETE" });
        selectOrg(orgId, { skipToggle: true });
      });
    });

    // Edit / delete asks
    content?.querySelectorAll("[data-ask-edit]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const a = openAsks.find((x) => x.id === btn.dataset.askEdit);
        const text = prompt("Edit ask:", a?.text || "");
        if (text === null || !text.trim()) return;
        const priority = prompt("Priority (high / normal / low):", a?.priority || "normal");
        if (priority === null) return;
        await api(`/api/asks/${btn.dataset.askEdit}`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: text.trim(), priority: (priority || "normal").trim() }),
        });
        selectOrg(orgId, { skipToggle: true });
      });
    });
    content?.querySelectorAll("[data-ask-del]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("Delete this ask?")) return;
        await api(`/api/asks/${btn.dataset.askDel}`, { method: "DELETE" });
        selectOrg(orgId, { skipToggle: true });
      });
    });

    // Edit / delete commitments
    content?.querySelectorAll("[data-commit-edit]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const c = openCommits.find((x) => x.id === btn.dataset.commitEdit);
        const text = prompt("Edit commitment:", c?.text || "");
        if (text === null || !text.trim()) return;
        const due = prompt("Due date (YYYY-MM-DD, blank for none):", c?.due_date || "");
        if (due === null) return;
        await api(`/api/commitments/${btn.dataset.commitEdit}`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: text.trim(), due_date: due.trim() }),
        });
        selectOrg(orgId, { skipToggle: true });
      });
    });
    content?.querySelectorAll("[data-commit-del]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        if (!confirm("Delete this commitment?")) return;
        await api(`/api/commitments/${btn.dataset.commitDel}`, { method: "DELETE" });
        selectOrg(orgId, { skipToggle: true });
      });
    });

    // Edit / delete the organization itself
    $("#org-edit-save")?.addEventListener("click", async () => {
      const name = $("#org-edit-name").value.trim();
      if (!name) { alert("Organization name is required."); return; }
      try {
        await api(`/api/organizations/${orgId}`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, type: $("#org-edit-type").value, notes: $("#org-edit-notes").value }),
        });
        loadGroups();
        selectOrg(orgId, { skipToggle: true });
      } catch { alert("Couldn't save organization."); }
    });
    $("#org-delete")?.addEventListener("click", async () => {
      if (!confirm(`Delete ${org.name}? This permanently removes the organization.`)) return;
      try {
        await api(`/api/organizations/${orgId}`, { method: "DELETE" });
        selectOrg(null);
        loadGroups();
      } catch { alert("Couldn't delete organization."); }
    });

    // Reveal col-2 — its content is already populated.
    state.selectedOrgId = orgId;
    updateRelDepth();
  } catch {
    if (content) content.innerHTML = `<div class="detail-empty">Couldn't load organization.</div>`;
    if (orgDetail) orgDetail.classList.remove("hidden");
    state.selectedOrgId = orgId;
    updateRelDepth();
  }
}

// Vertical activity timeline for an organization — a single chronological feed merged
// server-side (/api/organizations/<id>/timeline). Meetings are clickable into the detail.
const _TL_META = {
  meeting:        { cls: "meeting",    icon: "📅", title: "Meeting" },
  ask:            { cls: "ask",        icon: "🙋", title: "Ask raised" },
  commitment:     { cls: "commitment", icon: "🤝", title: "Commitment" },
  trigger:        { cls: "trigger",    icon: "👁", title: "Follow-up trigger" },
  task_created:   { cls: "task",       icon: "◻", title: "Task" },
  task_completed: { cls: "done",       icon: "✓", title: "Task completed" },
  note:           { cls: "note",       icon: "📝", title: "Note" },
  bill:           { cls: "bill",       icon: "📄", title: "Bill referenced" },
  bill_notified:  { cls: "billnotif",  icon: "📣", title: "Bill update — notified" },
};

// Renders a list of timeline events (shared by the org and person timelines) into a
// container. Meeting events are clickable into the meeting detail. Task events (created /
// completed) get inline complete/delete controls when a full Task object for them is
// available in taskLookup (id -> Task), so the timeline supports the same actions as the
// tasks page without a dedicated single-task fetch endpoint.
function _renderTimelineEvents(events, container, taskLookup, onTaskChanged) {
  if (!container) return;
  if (!events.length) {
    container.innerHTML = `<div class="detail-empty-inline">No activity yet.</div>`;
    return;
  }
  const fmtDay = (iso) => {
    const d = new Date(iso);
    return isNaN(d) ? "—" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  };
  const monthKey = (iso) => {
    const d = new Date(iso);
    return isNaN(d) ? "" : d.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  };
  const quietStatus = new Set(["open", "logged", "watching", "complete", "done"]);
  let html = `<div class="tl-line"></div>`;
  let lastMonth = null;
  for (const e of events) {
    const meta = _TL_META[e.kind] || { cls: "note", icon: "•", title: e.kind };
    const mk = monthKey(e.ts);
    if (mk && mk !== lastMonth) { html += `<div class="tl-month">${escapeHtml(mk)}</div>`; lastMonth = mk; }
    const badges = [];
    if (e.action_count)   badges.push(`<span class="badge actions" title="Open action items">${e.action_count}A</span>`);
    if (e.reminder_count) badges.push(`<span class="badge reminders" title="Open reminders">${e.reminder_count}R</span>`);
    if (e.status && !quietStatus.has(e.status))
      badges.push(`<span class="entity-status status-${escapeHtml(e.status)}">${escapeHtml(String(e.status).replace(/_/g, " "))}</span>`);
    if (e.priority === "high") badges.push(`<span class="tl-prio">high</span>`);
    if (e.extra)               badges.push(`<span class="tl-extra">due ${escapeHtml(e.extra)}</span>`);
    const isTaskEvent = (e.kind === "task_created" || e.kind === "task_completed");
    const task = isTaskEvent && taskLookup ? taskLookup.get(e.task_id) : null;
    if (task) {
      badges.push(`<span class="tl-event-actions">
        <button class="entity-status-btn" data-tl-task-toggle="${escapeHtml(task.id)}" title="${task.done ? "Mark not done" : "Mark complete"}">${task.done ? "↺" : "✓"}</button>
        <button class="entity-del-btn" data-tl-task-del="${escapeHtml(task.id)}" title="Delete">🗑</button>
      </span>`);
    }
    const clickable = e.kind === "meeting" && e.meeting_id;
    html += `
      <div class="tl-event tl-event--${meta.cls}${clickable ? " tl-event--click" : ""}"${clickable ? ` data-mid="${escapeHtml(e.meeting_id)}"` : ""}>
        <div class="tl-dot tl-dot--${meta.cls}" title="${escapeHtml(meta.title)}">${meta.icon}</div>
        <div class="tl-body">
          <div class="tl-row">
            <span class="tl-date">${escapeHtml(fmtDay(e.ts))}</span>
            <span class="tl-kind">${escapeHtml(meta.title)}</span>
            ${badges.join("")}
          </div>
          <div class="tl-label">${escapeHtml(e.label || "")}</div>
        </div>
      </div>`;
  }
  container.innerHTML = html;
  container.querySelectorAll(".tl-event--click[data-mid]").forEach((row) => {
    row.addEventListener("click", () => selectMeeting(row.dataset.mid));
  });
  container.querySelectorAll("[data-tl-task-toggle]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const task = taskLookup.get(btn.dataset.tlTaskToggle);
      if (!task) return;
      await toggleTaskDone(task);
      if (onTaskChanged) onTaskChanged();
    });
  });
  container.querySelectorAll("[data-tl-task-del]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("Delete this task?")) return;
      const task = taskLookup.get(btn.dataset.tlTaskDel);
      if (!task) return;
      await deleteTask(task);
      if (onTaskChanged) onTaskChanged();
    });
  });
}

async function renderOrgTimeline(orgId, container, taskLookup, onTaskChanged) {
  if (!container) return;
  let events;
  try {
    const r = await api(`/api/organizations/${orgId}/timeline`);
    events = r.events || [];
  } catch {
    container.innerHTML = `<div class="detail-empty-inline">Couldn't load the timeline.</div>`;
    return;
  }
  if (state.selectedOrgId !== orgId) return;   // user navigated away mid-fetch
  _renderTimelineEvents(events, container, taskLookup, onTaskChanged);
}

async function renderPersonTimeline(contactId, container, taskLookup, onTaskChanged) {
  if (!container) return;
  let events;
  try {
    const r = await api(`/api/people/${contactId}/timeline`);
    events = r.events || [];
  } catch {
    container.innerHTML = `<div class="detail-empty-inline">Couldn't load the timeline.</div>`;
    return;
  }
  if (_currentPersonId !== contactId) return;   // user navigated away mid-fetch
  _renderTimelineEvents(events, container, taskLookup, onTaskChanged);
}

function renderPeopleTable(people) {
  const tbody = $("#people-body");
  if (!tbody) return;
  if (!people.length) {
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--muted);padding:30px;">No contacts yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = people.map((p) => `
    <tr class="stakeholder-row" data-person-id="${escapeHtml(p.id)}" style="cursor:pointer">
      <td><strong>${escapeHtml(p.name)}</strong></td>
      <td>${escapeHtml(p.org_name || p.company || "—")}</td>
      <td>${escapeHtml(p.last_seen || "—")}</td>
      <td class="num">${p.meeting_count || 0}</td>
    </tr>
  `).join("");
  tbody.querySelectorAll(".stakeholder-row").forEach((row) => {
    row.addEventListener("click", () => selectPerson(row.dataset.personId));
  });
}

let _currentPersonId = null;   // contact id being edited; null = creating a new person
let _pePendingCard = null;     // data URL of a freshly scanned card, sent on save
let _personRefresh = null;     // how the person-card editor refreshes itself after a save

function _personEditorFields(p = {}) {
  const f = (id, label, val, type = "text") =>
    `<div class="modal-row"><label for="${id}">${label}</label>
       <input id="${id}" type="${type}" value="${escapeHtml(val || "")}" autocomplete="off"></div>`;
  return `
    <div class="person-editor">
      ${f("pe-name", "Name", p.name)}
      ${f("pe-title", "Title", p.title)}
      ${f("pe-company", "Company", p.company)}
      ${f("pe-email", "Email", p.email, "email")}
      ${f("pe-phone", "Phone", p.phone, "tel")}
      ${p.card_image ? `<img id="pe-card-img" class="drawer-card-thumb" src="${p.card_image}" alt="Business card" style="margin-top:6px">` : `<img id="pe-card-img" class="drawer-card-thumb hidden" alt="Business card" style="margin-top:6px">`}
      <input type="file" id="pe-photo" accept="image/*" capture="environment" style="display:none">
      <div class="person-editor-actions">
        <button type="button" class="secondary-btn-sm" id="pe-scan">Scan business card</button>
        <button type="button" class="primary-btn-sm" id="pe-save">Save</button>
      </div>
    </div>`;
}

function _wirePersonEditor() {
  const scanBtn = $("#pe-scan");
  const photo = $("#pe-photo");
  scanBtn?.addEventListener("click", () => photo.click());
  photo?.addEventListener("change", async () => {
    const file = photo.files[0];
    if (!file) return;
    photo.value = "";
    scanBtn.disabled = true;
    scanBtn.textContent = "Reading…";
    try {
      const r = await scanCardImage(file);
      _pePendingCard = r.dataUrl || null;
      if (r.ok) {
        // Only overwrite a field when the scan actually returned a value.
        const set = (id, v) => { if (v) $(id).value = v; };
        set("#pe-name", r.name); set("#pe-company", r.company);
        set("#pe-title", r.title); set("#pe-email", r.email); set("#pe-phone", r.phone);
      } else {
        alert(r.error || "Card scan failed — fill in manually");
      }
      const img = $("#pe-card-img");
      if (img && _pePendingCard) { img.src = _pePendingCard; img.classList.remove("hidden"); }
    } catch {
      alert("Card scan failed — fill in manually");
    } finally {
      scanBtn.disabled = false;
      scanBtn.textContent = "Scan business card";
    }
  });
  $("#pe-save")?.addEventListener("click", async () => {
    const body = {
      name: $("#pe-name").value.trim(),
      title: $("#pe-title").value.trim(),
      company: $("#pe-company").value.trim(),
      email: $("#pe-email").value.trim(),
      phone: $("#pe-phone").value.trim(),
    };
    if (_pePendingCard) body.card_image = _pePendingCard;
    if (!body.name) { alert("Name is required"); $("#pe-name").focus(); return; }
    try {
      let savedId = _currentPersonId;
      if (_currentPersonId) {
        await api(`/api/people/${_currentPersonId}`, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } else {
        const res = await api("/api/contacts", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        savedId = res.id;
      }
      _pePendingCard = null;
      await loadGroups();
      if (savedId) { if (_personRefresh) _personRefresh(savedId); else selectPerson(savedId); }
    } catch {
      alert("Save failed");
    }
  });
}

// Reusable standalone-notes section for an organization or a person.
function _entityNotesHtml(notes) {
  const items = (notes || []).map((n) => `
    <div class="entity-note" data-note-id="${n.id}">
      <span class="entity-note-body">${escapeHtml(n.body)}</span>
      <span class="entity-note-meta">${escapeHtml(n.created_at || "")}</span>
      <button class="entity-note-del" title="Delete note">✕</button>
    </div>`).join("") || `<p class="detail-empty-inline">No notes yet.</p>`;
  return `<div class="org-detail-section">
      <div class="drawer-section-label">Notes</div>
      ${items}
      <div class="entity-note-add">
        <textarea class="entity-note-input" rows="2" placeholder="Add a note…"></textarea>
        <button class="secondary-btn-sm entity-note-save">Add note</button>
      </div>
    </div>`;
}
function _wireEntityNotes(scope, entityType, entityId, refresh) {
  scope.querySelectorAll(".entity-note-del").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.closest(".entity-note").dataset.noteId;
      await api(`/api/entity-notes/${id}`, { method: "DELETE" });
      refresh();
    });
  });
  const save = scope.querySelector(".entity-note-save");
  save?.addEventListener("click", async () => {
    const ta = scope.querySelector(".entity-note-input");
    const body = (ta.value || "").trim();
    if (!body) return;
    await api("/api/entity-notes", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entity_type: entityType, entity_id: entityId, body }),
    });
    refresh();
  });
}

function openAddPersonModal() {
  _currentPersonId = null;
  _pePendingCard = null;
  _personRefresh = (id) => selectPerson(id);
  const tbody = $("#people-body");
  if (!tbody) return;
  tbody.querySelectorAll(".person-expand").forEach((el) => el.remove());
  tbody.querySelectorAll(".stakeholder-row.active").forEach((r) => r.classList.remove("active"));
  const tr = document.createElement("tr");
  tr.className = "person-expand";
  tr.dataset.personId = "__new__";
  tr.innerHTML = `<td class="person-expand-cell" colspan="4"><div class="person-expand-inner">
      <div class="org-detail-header"><h2>Add person</h2></div>
      ${_personEditorFields({})}
    </div></td>`;
  tbody.prepend(tr);
  _wirePersonEditor();
  tr.scrollIntoView({ block: "nearest", behavior: "smooth" });
  setTimeout(() => $("#pe-name")?.focus(), 10);
}

// Render a person's full detail card into `container` and wire all its controls.
// Shared by the People-window inline expansion and the in-meeting attendee card.
//   opts.onRefresh     — re-render in place after a non-destructive edit (default: re-render container)
//   opts.onSaveRefresh — how to refresh after the editor save (which reloads the people list)
//   opts.onDelete      — called after the contact is deleted (default: reload groups)
async function renderPersonInto(container, contactId, opts = {}) {
  if (!container) return;
  const refresh = opts.onRefresh || (() => renderPersonInto(container, contactId, opts));
  _currentPersonId = contactId;
  _pePendingCard = null;
  _personRefresh = opts.onSaveRefresh || refresh;
  const content = container;
  content.innerHTML = `<div class="detail-empty">Loading…</div>`;
  try {
    const p = await api(`/api/people/${contactId}`);
    const asksHtml = (p.asks || []).map((a) =>
      `<div class="org-entity-row">
         <span class="entity-text">${escapeHtml(a.text)}</span>
         <span class="entity-status status-${a.status}">${escapeHtml(a.status)}</span>
       </div>`).join("") || `<p class="detail-empty-inline">No asks on record.</p>`;

    const meetingsHtml = (p.meetings || []).slice(0, 8).map((m) =>
      `<div class="org-meeting-row" data-mid="${escapeHtml(m.id || "")}" style="cursor:pointer">
         <span class="org-meeting-date">${escapeHtml(m.date || "—")}</span>
         <span class="org-meeting-topic">${escapeHtml(m.topic || m.canonical_group || "Meeting")}</span>
       </div>`).join("");

    const orgsHtml = (p.orgs || []).map((o) =>
      `<span class="org-chip-wrap">
         <button class="attendee-chip attendee-chip--link" data-org-id="${escapeHtml(o.id)}">${escapeHtml(o.name)}</button>
         <button class="chip-remove" data-unlink-org="${escapeHtml(o.id)}" title="Remove from organization" aria-label="Remove from organization">×</button>
       </span>`
    ).join("") + `<button class="attendee-chip" id="person-add-org" title="Add organization">+ org</button>`;

    const tasksHtml = (p.tasks || []).map((t) =>
      `<div class="org-entity-row org-entity-row--task${t.done ? " org-entity-row--done" : ""}" data-person-task-id="${escapeHtml(t.id)}">
         <span class="callout-badge callout-badge--task">${_SCAN_ICONS.task}Task</span>
         <span class="entity-text">${escapeHtml(t.text)}</span>
         ${t.done ? `<span class="entity-status">✓ done</span>` : (t.deadline ? `<span class="entity-status">${escapeHtml(t.deadline)}</span>` : "")}
       </div>`).join("") || `<p class="detail-empty-inline">No tasks yet.</p>`;

    const commitsHtml = (p.commitments || []).map((c) =>
      `<div class="org-entity-row">
         <span class="entity-text">${escapeHtml(c.text)}</span>
         <span class="entity-status status-${c.status}">${escapeHtml((c.status || "").replace("_"," "))}</span>
       </div>`).join("");

    content.innerHTML = `
      <div class="org-detail-header">
        <h2>${escapeHtml(p.name)}</h2>
        <button class="detail-delete-btn" id="person-delete" title="Delete contact">Delete</button>
      </div>
      <div class="org-detail-section">
        <div class="drawer-section-label">Organizations</div>
        <div class="attendee-chips">${orgsHtml}</div>
      </div>
      ${_personEditorFields(p)}
      <div class="org-detail-section">
        <div class="drawer-section-label">Tasks</div>
        ${tasksHtml}
      </div>
      ${commitsHtml ? `<div class="org-detail-section"><div class="drawer-section-label">Commitments</div>${commitsHtml}</div>` : ""}
      <div class="org-detail-section">
        <div class="drawer-section-label">Asks Raised</div>
        ${asksHtml}
      </div>
      ${meetingsHtml ? `<div class="org-detail-section"><div class="drawer-section-label">Meeting History</div>${meetingsHtml}</div>` : ""}
      ${_entityNotesHtml(p.entity_notes)}
      <div class="org-detail-section org-timeline-wrap">
        <div class="drawer-section-label">Activity</div>
        <div class="org-timeline" id="person-timeline">
          <div class="detail-empty-inline">Loading timeline…</div>
        </div>
      </div>
    `;
    _wirePersonEditor();
    _wireEntityNotes(content, "contact", contactId, refresh);
    const personTaskLookup = new Map((p.tasks || []).map((t) => [t.id, t]));
    renderPersonTimeline(contactId, content.querySelector("#person-timeline"), personTaskLookup, refresh);
    content.querySelectorAll(".org-meeting-row[data-mid]").forEach((row) => {
      if (!row.dataset.mid) return;
      row.addEventListener("click", () => selectMeeting(row.dataset.mid));
    });
    // Org chips → open that org
    content.querySelectorAll(".attendee-chip--link[data-org-id]").forEach((chip) => {
      chip.addEventListener("click", () => {
        document.querySelectorAll(".groups-subtab").forEach((b) => b.classList.remove("active"));
        document.querySelector(".groups-subtab[data-subtab='orgs']")?.classList.add("active");
        document.querySelectorAll(".groups-panel").forEach((pp) => pp.classList.add("hidden"));
        $("#groups-panel-orgs")?.classList.remove("hidden");
        selectOrg(chip.dataset.orgId, { skipToggle: true });
      });
    });
    // + org → prompt for org name, link, refresh
    $("#person-add-org")?.addEventListener("click", async () => {
      const name = prompt("Add this person to which organization?");
      if (!name || !name.trim()) return;
      await api(`/api/people/${contactId}/organizations`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim() }),
      });
      refresh();
    });
    // Unlink an organization from this person
    content.querySelectorAll("[data-unlink-org]").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        if (!confirm("Remove this person from that organization?")) return;
        await api(`/api/people/${contactId}/organizations/${btn.dataset.unlinkOrg}`, { method: "DELETE" });
        refresh();
      });
    });
    // Delete this contact
    $("#person-delete")?.addEventListener("click", async () => {
      if (!confirm(`Delete ${p.name}? This permanently removes the contact.`)) return;
      await api(`/api/people/${contactId}`, { method: "DELETE" });
      if (opts.onDelete) opts.onDelete(); else loadGroups();
    });
    // Person's tasks open the task drawer
    content.querySelectorAll("[data-person-task-id]").forEach((row) => {
      row.addEventListener("click", () => {
        const t = (p.tasks || []).find((x) => x.id === row.dataset.personTaskId);
        if (t) openDrawer(t);
      });
    });
  } catch {
    content.innerHTML = `<div class="detail-empty">Couldn't load person.</div>`;
  }
}

// People window: click a person row → expand their info inline directly below the row.
// Click the same row again (or another) to collapse/swap — that's how you "click out".
async function selectPerson(contactId) {
  const tbody = $("#people-body");
  if (!tbody) return;
  const wasOpen = !!tbody.querySelector(
    `.person-expand[data-person-id="${CSS.escape(contactId)}"]`);
  // Collapse any currently-open expansion and clear active states.
  tbody.querySelectorAll(".person-expand").forEach((el) => el.remove());
  tbody.querySelectorAll(".stakeholder-row.active").forEach((r) => r.classList.remove("active"));
  if (wasOpen) { _currentPersonId = null; return; }   // re-click → just collapse
  const row = tbody.querySelector(`.stakeholder-row[data-person-id="${CSS.escape(contactId)}"]`);
  if (!row) return;
  row.classList.add("active");
  const tr = document.createElement("tr");
  tr.className = "person-expand";
  tr.dataset.personId = contactId;
  tr.innerHTML = `<td class="person-expand-cell" colspan="4"><div class="person-expand-inner"></div></td>`;
  row.after(tr);
  await renderPersonInto(tr.querySelector(".person-expand-inner"), contactId, {
    onSaveRefresh: (id) => selectPerson(id),   // editor save reloads the list, then re-expand
    onDelete: () => loadGroups(),
  });
  tr.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// Open the full person editor inline below the meeting header. `triggerEl` is the
// element that was clicked (an attendee chip or a contact card); it gets an
// `.active` state and is cleared when the editor closes. `pid` is the contact id.
function toggleDetailPersonCard(triggerEl, pid = triggerEl.dataset.personId) {
  const detail = $("#detail");
  if (!detail) return;
  const clearActive = () => detail
    .querySelectorAll(".attendee-chip--link.active, .detail-contact-card.active")
    .forEach((c) => c.classList.remove("active"));
  const existing = detail.querySelector(".detail-person-card");
  const wasOpenForThis = existing && existing.dataset.personId === pid;
  if (existing) existing.remove();
  clearActive();
  if (wasOpenForThis) return;   // re-click → just collapse
  triggerEl.classList.add("active");
  const card = document.createElement("div");
  card.className = "detail-person-card";
  card.dataset.personId = pid;
  const header = detail.querySelector("header");
  if (header) header.after(card); else detail.prepend(card);
  renderPersonInto(card, pid, {
    onDelete: () => { card.remove(); triggerEl.classList.remove("active"); loadGroups(); },
  });
  card.scrollIntoView({ block: "nearest", behavior: "smooth" });
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

// ===== Intake Notes =====

let _intakeMeetingType = null;
let _intakeScanResult = null; // {text, items: [{type, text, accepted}]}
let _intakeLinkedContacts = []; // [{id, name, company, title, email, phone}]

const _BILL_RE = /\b(H\.R\.|S\.|H\.Res\.|S\.Res\.|H\.Con\.Res\.|S\.Con\.Res\.|H\.J\.Res\.|S\.J\.Res\.)\s*(\d+)\b/gi;

function _extractCallouts(text) {
  const items = [];
  const _hasDue = (s) => /\bdue[:\s]/i.test(s) || /\bdeadline[:\s]/i.test(s);

  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (!line) continue;

    if (/^(\[.*?\]|□|☐)\s*/.test(line)) {
      const t = line.replace(/^(\[.*?\]|□|☐)\s*/, "").trim();
      if (t) {
        items.push({ type: "task", text: t });
        if (_hasDue(t)) items.push({ type: "deadline", text: t });
      }
    } else if (/^!+\s*\S/.test(line)) {
      const t = line.replace(/^!+\s*/, "").trim();
      if (t) {
        items.push({ type: "important", text: t });
        if (_hasDue(t)) items.push({ type: "deadline", text: t });
      }
    } else if (/^\?+\s*\S/.test(line)) {
      const t = line.replace(/^\?+\s*/, "").trim();
      if (t) {
        items.push({ type: "followup", text: t });
        if (_hasDue(t)) items.push({ type: "deadline", text: t });
      }
    } else if (/^~~\s*\S/.test(line) || /^ASK\s+/i.test(line)) {
      const t = line.replace(/^~~\s*/, "").replace(/^ASK\s+/i, "").trim();
      if (t) items.push({ type: "ask", text: t });
    } else if (/^>>>\s*\S/.test(line) || /^COMMIT\s+/i.test(line)) {
      const t = line.replace(/^>>>\s*/, "").replace(/^COMMIT\s+/i, "").trim();
      if (t) items.push({ type: "commitment", text: t });
    } else if (/^FU\s+IF\s+/i.test(line)) {
      const t = line.replace(/^FU\s+IF\s+/i, "").trim();
      if (t) items.push({ type: "trigger", text: t });
    } else if (_hasDue(line)) {
      items.push({ type: "deadline", text: line });
    } else if (/^@([A-Za-z]\w*)/.test(line)) {
      items.push({ type: "person", text: line.replace(/^@/, "").trim() });
    }

    // Bills can appear in any line regardless of other markers
    _BILL_RE.lastIndex = 0;
    let m;
    while ((m = _BILL_RE.exec(line)) !== null) {
      const billType = m[1].replace(/\s+$/, "");
      const billNumber = m[2];
      items.push({ type: "bill", billType, billNumber, text: `${billType} ${billNumber}` });
    }
  }
  return items;
}

function _intakeScan() {
  const text = ($("#intake-notes-text").value || "").trim();
  const items = _extractCallouts(text).map((item) => ({ ...item, accepted: true }));
  _intakeScanResult = { text, items };
  _renderScanResults();
}

const _SCAN_ICONS = {
  task:      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>',
  important: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  followup:  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  deadline:  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
  person:    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>',
  bill:       '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>',
  ask:        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>',
  commitment: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
  trigger:    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
};
const _SCAN_LABELS = {
  task: "Task", important: "Important", followup: "Follow-up",
  deadline: "Deadline", person: "Person", bill: "Bill",
  ask: "Ask", commitment: "Commitment", trigger: "Trigger",
};

function _editScanItemDateInline(anchorEl, idx) {
  if (!_intakeScanResult) return;
  const item = _intakeScanResult.items[idx];
  const inp = document.createElement("input");
  inp.type = "date";
  inp.value = item.due || "";
  inp.className = "scan-item-date-edit";
  anchorEl.replaceWith(inp);
  inp.focus();
  if (inp.showPicker) { try { inp.showPicker(); } catch (_) {} }
  let done = false;
  function commit() {
    if (done) return;
    done = true;
    item.due = inp.value || null;
    _renderScanResults();
  }
  inp.addEventListener("change", commit);
  inp.addEventListener("blur", () => setTimeout(commit, 120));
  inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); inp.blur(); }
    if (e.key === "Escape") { done = true; _renderScanResults(); }
  });
}

// Which callout types the user is allowed to switch between in the review queue.
// Deadline/person/bill have distinct downstream semantics and aren't task-producing,
// so they're not interchangeable with task/followup/important.
const _SWITCHABLE_CALLOUT_TYPES = ["task", "followup", "important", "ask", "commitment", "trigger"];
// Types where setting a due date downstream makes sense.
const _DATABLE_CALLOUT_TYPES = ["task", "followup", "important", "ask", "commitment", "trigger", "deadline"];

const _SCAN_CALENDAR_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>';
const _SCAN_TRASH_SVG = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>';

function _renderScanResults() {
  const queueEl = $("#intake-review-queue");
  if (!_intakeScanResult) { queueEl.classList.add("hidden"); return; }

  const { text, items } = _intakeScanResult;

  $("#scan-result-summary").textContent = items.length === 0
    ? "Scanned — no callouts detected"
    : `${items.length} item${items.length !== 1 ? "s" : ""} kept`;

  $("#scan-result-items").innerHTML = items.map((item, idx) => {
    const switchable = _SWITCHABLE_CALLOUT_TYPES.includes(item.type);
    const datable = _DATABLE_CALLOUT_TYPES.includes(item.type);
    const typeControl = switchable
      ? `<select class="scan-item-type-select" data-idx="${idx}" title="Change type">
           ${_SWITCHABLE_CALLOUT_TYPES.map((k) =>
             `<option value="${k}"${k === item.type ? " selected" : ""}>${_SCAN_LABELS[k]}</option>`
           ).join("")}
         </select>`
      : `<div class="scan-item-type">${escapeHtml(_SCAN_LABELS[item.type] || item.type)}</div>`;
    const dueChip = item.due
      ? `<span class="scan-item-due-chip" data-idx="${idx}" title="Click to change">${escapeHtml(item.due)}</span>`
      : "";
    const dateBtn = datable
      ? `<button class="scan-item-action" data-action="date" data-idx="${idx}" title="Set due date">${_SCAN_CALENDAR_SVG}</button>`
      : "";
    return `
    <div class="scan-item scan-item--accepted">
      <div class="scan-item-icon scan-item-icon--${item.type}">${_SCAN_ICONS[item.type] || ""}</div>
      <div class="scan-item-body">
        ${typeControl}
        <input type="text" class="scan-item-text-input" data-idx="${idx}" value="${escapeHtml(item.text)}">
        ${dueChip}
      </div>
      <div class="scan-item-actions">
        ${dateBtn}
        <button class="scan-item-action" data-action="delete" data-idx="${idx}" title="Delete">${_SCAN_TRASH_SVG}</button>
      </div>
    </div>`;
  }).join("");

  const itemsRoot = $("#scan-result-items");
  itemsRoot.querySelectorAll(".scan-item-action[data-action='delete']").forEach((btn) => {
    btn.addEventListener("click", () => {
      const idx = +btn.dataset.idx;
      _intakeScanResult.items.splice(idx, 1);
      _renderScanResults();
    });
  });
  itemsRoot.querySelectorAll(".scan-item-text-input").forEach((inp) => {
    inp.addEventListener("input", () => {
      const idx = +inp.dataset.idx;
      _intakeScanResult.items[idx].text = inp.value;
    });
    inp.addEventListener("change", () => {
      const idx = +inp.dataset.idx;
      _intakeScanResult.items[idx].text = inp.value;
      $("#scan-result-summary").textContent =
        `${_intakeScanResult.items.length} item${_intakeScanResult.items.length !== 1 ? "s" : ""} kept`;
    });
  });
  itemsRoot.querySelectorAll(".scan-item-action[data-action='date']").forEach((btn) => {
    btn.addEventListener("click", () => _editScanItemDateInline(btn, +btn.dataset.idx));
  });
  itemsRoot.querySelectorAll(".scan-item-due-chip").forEach((chip) => {
    chip.addEventListener("click", () => _editScanItemDateInline(chip, +chip.dataset.idx));
  });
  itemsRoot.querySelectorAll(".scan-item-type-select").forEach((sel) => {
    sel.addEventListener("change", () => {
      const idx = +sel.dataset.idx;
      _intakeScanResult.items[idx].type = sel.value;
      _renderScanResults();
    });
  });

  $("#scan-transcription-text").textContent = text || "(empty)";
  queueEl.classList.remove("hidden");
}

// ---------- Today's Callouts modal ----------
let _todayCalloutsDate = null;  // YYYY-MM-DD currently displayed
let _todayCalloutsData = null;  // {date, meetings: [...]}

function openTodayCalloutsModal() {
  $("#today-callouts-backdrop").classList.remove("hidden");
  const today = new Date().toISOString().slice(0, 10);
  _todayCalloutsDate = today;
  $("#today-callouts-date").value = today;
  _loadTodayCallouts(today);
}

function closeTodayCalloutsModal() {
  $("#today-callouts-backdrop").classList.add("hidden");
  _todayCalloutsData = null;
}

async function _loadTodayCallouts(dateISO) {
  const body = $("#today-callouts-body");
  body.innerHTML = `<div class="detail-empty">Loading…</div>`;
  try {
    const data = await api("/api/scan-items?date=" + encodeURIComponent(dateISO));
    _todayCalloutsData = data;
    _todayCalloutsDate = data.date || dateISO;
    $("#today-callouts-date").value = _todayCalloutsDate;
    _renderTodayCallouts();
  } catch (e) {
    body.innerHTML = `<div class="today-callouts-empty">Couldn't load callouts.</div>`;
  }
}

function _renderTodayCallouts() {
  const body = $("#today-callouts-body");
  const meetings = _todayCalloutsData?.meetings || [];
  if (!meetings.length) {
    body.innerHTML = `<div class="today-callouts-empty">No callouts recorded on this day.</div>`;
    return;
  }
  body.innerHTML = meetings.map((m) => {
    const itemsHtml = (m.items || []).map((item) => {
      const switchable = _SWITCHABLE_CALLOUT_TYPES.includes(item.type);
      const datable = _DATABLE_CALLOUT_TYPES.includes(item.type);
      const typeControl = switchable
        ? `<select class="scan-item-type-select" data-item-id="${item.id}" title="Change type">
             ${_SWITCHABLE_CALLOUT_TYPES.map((k) =>
               `<option value="${k}"${k === item.type ? " selected" : ""}>${_SCAN_LABELS[k]}</option>`
             ).join("")}
           </select>`
        : `<div class="scan-item-type">${escapeHtml(_SCAN_LABELS[item.type] || item.type)}</div>`;
      const due = item.task_deadline;
      const dueChip = due
        ? `<span class="scan-item-due-chip" data-item-id="${item.id}" title="Click to change">${escapeHtml(due)}</span>`
        : "";
      const dateBtn = datable && item.task_id
        ? `<button class="scan-item-action" data-action="date" data-item-id="${item.id}" title="Set due date">${_SCAN_CALENDAR_SVG}</button>`
        : "";
      const doneCls = item.task_done ? " today-callouts-item-done" : "";
      return `
        <div class="scan-item scan-item--accepted${doneCls}" data-item-id="${item.id}">
          <div class="scan-item-icon scan-item-icon--${item.type}">${_SCAN_ICONS[item.type] || ""}</div>
          <div class="scan-item-body">
            ${typeControl}
            <input type="text" class="scan-item-text-input" data-item-id="${item.id}" value="${escapeHtml(item.text)}">
            ${dueChip}
          </div>
          <div class="scan-item-actions">
            ${dateBtn}
            <button class="scan-item-action" data-action="delete" data-item-id="${item.id}" title="Delete">${_SCAN_TRASH_SVG}</button>
          </div>
        </div>`;
    }).join("");
    const dateLabel = m.date ? new Date(m.date + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }) : "";
    return `
      <div class="today-callouts-meeting-block">
        <div class="today-callouts-meeting-header">
          <h3>${escapeHtml(m.topic || m.group || "Meeting")}</h3>
          <span class="meeting-meta">${escapeHtml(m.group || "")}${dateLabel ? " · " + escapeHtml(dateLabel) : ""}</span>
        </div>
        ${itemsHtml}
      </div>`;
  }).join("");
  _wireTodayCalloutsHandlers();
}

function _wireTodayCalloutsHandlers() {
  const body = $("#today-callouts-body");
  body.querySelectorAll(".scan-item-text-input").forEach((inp) => {
    const original = inp.value;
    inp.addEventListener("change", async () => {
      const itemId = inp.dataset.itemId;
      const newText = inp.value.trim();
      if (!newText || newText === original) return;
      try {
        await api(`/api/scan-items/${itemId}/update`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: newText }),
        });
      } catch (_) {
        _loadTodayCallouts(_todayCalloutsDate);
      }
    });
  });
  body.querySelectorAll(".scan-item-action[data-action='delete']").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const itemId = btn.dataset.itemId;
      btn.disabled = true;
      try {
        await api(`/api/scan-items/${itemId}`, { method: "DELETE" });
        _loadTodayCallouts(_todayCalloutsDate);
        await refreshTasks();
      } catch (_) {
        btn.disabled = false;
      }
    });
  });
  body.querySelectorAll(".scan-item-action[data-action='date']").forEach((btn) => {
    btn.addEventListener("click", () => _editTodayCalloutDate(btn, btn.dataset.itemId));
  });
  body.querySelectorAll(".scan-item-due-chip").forEach((chip) => {
    chip.addEventListener("click", () => _editTodayCalloutDate(chip, chip.dataset.itemId));
  });
  body.querySelectorAll(".scan-item-type-select").forEach((sel) => {
    sel.addEventListener("change", async () => {
      const itemId = sel.dataset.itemId;
      const newType = sel.value;
      try {
        await api(`/api/scan-items/${itemId}/update`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ type: newType }),
        });
        _loadTodayCallouts(_todayCalloutsDate);
      } catch (e) {
        _loadTodayCallouts(_todayCalloutsDate);
      }
    });
  });
}

function _editTodayCalloutDate(anchorEl, itemId) {
  const inp = document.createElement("input");
  inp.type = "date";
  inp.className = "scan-item-date-edit";
  // pre-fill from current due chip if visible
  const meeting = (_todayCalloutsData?.meetings || []).find((m) => (m.items || []).some((i) => String(i.id) === String(itemId)));
  const item = meeting?.items.find((i) => String(i.id) === String(itemId));
  if (item?.task_deadline) inp.value = item.task_deadline;
  anchorEl.replaceWith(inp);
  inp.focus();
  if (inp.showPicker) { try { inp.showPicker(); } catch (_) {} }
  let done = false;
  async function commit() {
    if (done) return;
    done = true;
    const val = inp.value || null;
    try {
      await api(`/api/scan-items/${itemId}/update`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ due: val }),
      });
    } catch (_) {}
    _loadTodayCallouts(_todayCalloutsDate);
  }
  inp.addEventListener("change", commit);
  inp.addEventListener("blur", () => setTimeout(commit, 120));
  inp.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); inp.blur(); }
    if (e.key === "Escape") { done = true; _renderTodayCallouts(); }
  });
}

// --- Business card scanning ---

function _renderSavedCards() {
  const el = $("#card-saved-contacts");
  el.innerHTML = _intakeLinkedContacts.map((c, idx) => `
    <div class="saved-contact-chip">
      <span>${escapeHtml(c.name)}${c.company ? " · " + escapeHtml(c.company) : ""}</span>
      <button class="saved-contact-remove" data-idx="${idx}">✕</button>
    </div>`).join("");
  el.querySelectorAll(".saved-contact-remove").forEach((btn) => {
    btn.addEventListener("click", () => {
      _intakeLinkedContacts.splice(+btn.dataset.idx, 1);
      _renderSavedCards();
    });
  });
}

// Shared business-card scan: read a file, send to Claude Vision, return structured fields.
// Used by the intake card scanner and the People person editor.
async function scanCardImage(file) {
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
  const res = await fetch("/api/contacts/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image: dataUrl }),
  });
  const data = await res.json();
  return { ...data, dataUrl };
}

(function _initCardScanner() {
  const fileInput = $("#card-photo-input");
  const scanBtn = $("#card-scan-btn");
  const preview = $("#card-preview");

  scanBtn.addEventListener("click", () => fileInput.click());

  fileInput.addEventListener("change", async () => {
    const file = fileInput.files[0];
    if (!file) return;
    fileInput.value = "";

    scanBtn.disabled = true;
    scanBtn.textContent = "Reading…";

    try {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
      const res = await fetch("/api/contacts/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image: dataUrl }),
      });
      const fields = await res.json();
      if (fields.ok) {
        $("#card-field-name").value    = fields.name    || "";
        $("#card-field-company").value = fields.company || "";
        $("#card-field-title").value   = fields.title   || "";
        $("#card-field-email").value   = fields.email   || "";
        $("#card-field-phone").value   = fields.phone   || "";
        if (fields.title || fields.email || fields.phone) {
          document.querySelectorAll(".card-field--extra").forEach((f) => f.classList.remove("hidden"));
        }
        preview.classList.remove("hidden");
      } else {
        alert(fields.error || "Card scan failed — fill in manually");
        preview.classList.remove("hidden");
      }
    } catch (err) {
      alert("Card scan failed — fill in manually");
      preview.classList.remove("hidden");
    } finally {
      scanBtn.disabled = false;
      scanBtn.innerHTML = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg> Scan Business Card';
    }
  });

  $("#card-preview-dismiss").addEventListener("click", () => preview.classList.add("hidden"));

  $("#card-save-btn").addEventListener("click", async () => {
    const contact = {
      name: $("#card-field-name").value.trim(),
      company: $("#card-field-company").value.trim(),
      title: $("#card-field-title").value.trim(),
      email: $("#card-field-email").value.trim(),
      phone: $("#card-field-phone").value.trim(),
    };
    if (!contact.name) { alert("Name is required"); return; }

    const res = await fetch("/api/contacts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(contact),
    });
    const data = await res.json();
    if (!data.ok) { alert(data.error || "Save failed"); return; }

    _intakeLinkedContacts.push({ ...contact, id: data.id });
    _renderSavedCards();
    preview.classList.add("hidden");
  });
})();

function openIntakeModal() {
  document.body.style.overflow = "hidden";
  document.body.style.touchAction = "none";
  document.body.style.webkitUserSelect = "none";
  document.body.style.userSelect = "none";
  const backdrop = $("#intake-modal-backdrop");
  backdrop.classList.remove("hidden");
  backdrop.classList.add("intake-open");
  const dateInput = $("#intake-date");
  if (!dateInput.value) {
    dateInput.value = new Date().toISOString().split("T")[0];
  }
  $("#intake-result").className = "intake-result hidden";
  $("#intake-result").innerHTML = "";
  const submitBtn = $("#intake-modal-submit");
  submitBtn.disabled = false;
  submitBtn.textContent = "Finalize Notes";
  const cancelBtn = $("#intake-modal-cancel");
  cancelBtn.disabled = false;
  cancelBtn.textContent = "Cancel";
  // Reset to phase 0
  const modal = $(".modal-intake");
  _intakeMeetingType = null;
  modal.dataset.phase = "0";
  $("#intake-modal-title").textContent = "New Meeting";
  $("#intake-purpose-row").classList.remove("intake-show");
  $("#intake-purpose").value = "";
  // Populate group datalist
  const dl = $("#intake-group-list");
  dl.innerHTML = "";
  (state.facets?.groups || []).forEach((g) => {
    const opt = document.createElement("option");
    opt.value = g;
    dl.appendChild(opt);
  });
  _intakeScanResult = null;
  _intakeLinkedContacts = [];
  $("#intake-notes-text").value = "";
  $("#intake-review-queue").classList.add("hidden");
  $("#card-preview").classList.add("hidden");
  $("#card-saved-contacts").innerHTML = "";
}

function closeIntakeModal() {
  $("#intake-modal-backdrop").classList.add("hidden");
  $("#intake-modal-backdrop").classList.remove("intake-open");
  document.body.style.overflow = "";
  document.body.style.touchAction = "";
  document.body.style.webkitUserSelect = "";
  document.body.style.userSelect = "";
}

const _intakeTypePresets = {
  "1on1":       { group: "Rebekah",   topic: "1:1",                attendees: "", skipPremeeting: true,  constituent: false },
  "staff":      { group: "Staff",     topic: "Staff Meeting",      attendees: "", skipPremeeting: true,  constituent: false },
  "legteam":    { group: "Leg. Team", topic: "Leg. Team Meeting",  attendees: "", skipPremeeting: true,  constituent: false },
  "constituent":{ group: "",          topic: "",                                  skipPremeeting: false, constituent: true  },
  "briefing":   { group: "",          topic: "",                                  skipPremeeting: false, constituent: false },
  "other":      { group: "",          topic: "",                                  skipPremeeting: false, constituent: false },
};

function _intakeSelectType(type) {
  _intakeMeetingType = type;
  const preset = _intakeTypePresets[type] || {};
  const modal = $(".modal-intake");

  // Pre-fill fields
  if (preset.group !== undefined) $("#intake-group").value = preset.group;
  if (preset.topic !== undefined) $("#intake-topic").value = preset.topic;
  if (preset.attendees !== undefined) $("#intake-attendees").value = preset.attendees;

  // Show/hide constituent-only purpose row
  const purposeRow = $("#intake-purpose-row");
  if (preset.constituent) {
    purposeRow.classList.add("intake-show");
  } else {
    purposeRow.classList.remove("intake-show");
    $("#intake-purpose").value = "";
  }

  // For fixed-info meeting types, skip straight to in-meeting notes
  if (preset.skipPremeeting) {
    _intakeStartMeeting();
    return;
  }

  // Advance to phase 1 (pre-meeting)
  modal.dataset.phase = "1";
  $("#intake-modal-title").textContent = "Pre-meeting";
  $("#intake-modal-submit").textContent = "Start Meeting →";
  $("#intake-modal-cancel").textContent = "← Back";

  // Focus the most useful field
  if (!preset.group) {
    setTimeout(() => $("#intake-group").focus(), 50);
  } else if (!preset.topic) {
    setTimeout(() => $("#intake-topic").focus(), 50);
  } else {
    setTimeout(() => $("#intake-attendees").focus(), 50);
  }
}

function _intakeBuildChips() {
  const container = $("#intake-meta-chips");
  container.innerHTML = "";

  const fields = [
    { id: "intake-group",     label: "Group",     type: "text"   },
    { id: "intake-topic",     label: "Topic",     type: "text"   },
    { id: "intake-date",      label: "Date",      type: "date"   },
    { id: "intake-attendees", label: "Attendees", type: "text"   },
    { id: "intake-purpose",   label: "Purpose",   type: "select" },
  ];

  for (const f of fields) {
    const src = $(`#${f.id}`);
    const val = src.value.trim();
    if (!val) continue;

    const chip = document.createElement("div");
    chip.className = "intake-meta-chip";
    chip.dataset.field = f.id;

    const labelEl = document.createElement("span");
    labelEl.className = "intake-meta-chip-label";
    labelEl.textContent = f.label;

    const valueEl = document.createElement("span");
    valueEl.className = "intake-meta-chip-value";
    valueEl.textContent = f.id === "intake-date" ? new Date(val + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }) : val;

    const inputEl = document.createElement("input");
    inputEl.className = "intake-meta-chip-input";
    inputEl.type = f.type === "date" ? "date" : "text";
    inputEl.value = val;

    chip.appendChild(labelEl);
    chip.appendChild(valueEl);
    chip.appendChild(inputEl);
    container.appendChild(chip);

    chip.addEventListener("click", (e) => {
      if (chip.classList.contains("intake-meta-chip--expanded")) return;
      document.querySelectorAll(".intake-meta-chip--expanded").forEach((c) => _intakeCollapseChip(c));
      chip.classList.add("intake-meta-chip--expanded");
      inputEl.focus();
      inputEl.select();
      e.stopPropagation();
    });

    inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === "Escape") {
        _intakeCollapseChip(chip);
        e.preventDefault();
      }
    });

    inputEl.addEventListener("blur", () => {
      setTimeout(() => _intakeCollapseChip(chip), 120);
    });
  }
}

function _intakeCollapseChip(chip) {
  if (!chip.classList.contains("intake-meta-chip--expanded")) return;
  const fieldId = chip.dataset.field;
  const inputEl = chip.querySelector(".intake-meta-chip-input");
  const valueEl = chip.querySelector(".intake-meta-chip-value");
  const newVal = inputEl.value.trim();
  const src = $(`#${fieldId}`);
  if (newVal) {
    src.value = newVal;
    valueEl.textContent = fieldId === "intake-date"
      ? new Date(newVal + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })
      : newVal;
  }
  chip.classList.remove("intake-meta-chip--expanded");
}

function _intakeStartMeeting() {
  const modal = $(".modal-intake");
  modal.dataset.phase = "2";
  $("#intake-modal-title").textContent = "In Meeting";
  $("#intake-modal-submit").textContent = "Finalize Notes";
  $("#intake-modal-submit").disabled = false;
  $("#intake-modal-cancel").textContent = "← Back";
  _intakeBuildChips();
  setTimeout(() => $("#intake-notes-text").focus(), 50);
}

async function _intakeSaveNotes() {
  const transcription = (_intakeScanResult?.text || ($("#intake-notes-text").value || "")).trim();

  if (!transcription && !_intakeScanResult) {
    alert("Nothing to save — add some notes or tasks first.");
    return;
  }

  const btn = $("#intake-modal-submit");
  btn.disabled = true;
  btn.textContent = "Saving…";
  const cancelBtn = $("#intake-modal-cancel");
  cancelBtn.disabled = true;
  const resultEl = $("#intake-result");
  resultEl.className = "intake-result hidden";

  const preparedMeetingId = $("#intake-prepared-meeting-id")?.value || null;

  try {
    const res = await fetch("/api/notes/intake", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        group: $("#intake-group").value.trim(),
        topic: $("#intake-topic").value.trim(),
        date: $("#intake-date").value,
        attendees: $("#intake-attendees").value.trim(),
        body: transcription,
        action_items: "",
        reminders: "",
        meeting_type: _intakeMeetingType || null,
        purpose_val: $("#intake-purpose").value.trim() || null,
        prepared_meeting_id: preparedMeetingId || null,
        confirmed_items: _intakeScanResult
          ? _intakeScanResult.items.map((i) => {
              const out = { type: i.type, text: i.text };
              if (i.billType) out.billType = i.billType;
              if (i.billNumber) out.billNumber = i.billNumber;
              if (i.due) out.due = i.due;
              return out;
            })
          : null,
      }),
    });
    const data = await res.json();
    resultEl.classList.remove("hidden");
    if (data.ok) {
      // Link any scanned contacts to the saved meeting
      if (_intakeLinkedContacts.length && data.meeting_id) {
        await Promise.all(_intakeLinkedContacts.map((c) =>
          fetch(`/api/meetings/${data.meeting_id}/contacts`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ contact_id: c.id }),
          })
        ));
      }
      // Mark the prepared calendar-event stub as complete
      if (preparedMeetingId) {
        await fetch(`/api/meetings/${preparedMeetingId}/status`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: "complete" }),
        });
        if ($("#intake-prepared-meeting-id")) $("#intake-prepared-meeting-id").value = "";
        loadUpcomingMeetings();
      }
      resultEl.className = "intake-result intake-result-ok";
      const c = data.created || {};
      const breakdown = [
        c.actions      ? `${c.actions} task${c.actions !== 1 ? "s" : ""}` : "",
        c.followups    ? `${c.followups} follow-up${c.followups !== 1 ? "s" : ""}` : "",
        c.reminders    ? `${c.reminders} reminder${c.reminders !== 1 ? "s" : ""}` : "",
        c.bills        ? `${c.bills} bill${c.bills !== 1 ? "s" : ""}` : "",
        c.asks         ? `${c.asks} ask${c.asks !== 1 ? "s" : ""}` : "",
        c.commitments  ? `${c.commitments} commitment${c.commitments !== 1 ? "s" : ""}` : "",
        c.triggers     ? `${c.triggers} trigger${c.triggers !== 1 ? "s" : ""}` : "",
      ].filter(Boolean).join(" · ");
      const chips = [
        data.topic ? `<span class="intake-chip">${escapeHtml(data.topic)}</span>` : "",
        data.group ? `<span class="intake-chip">${escapeHtml(data.group)}</span>` : "",
        breakdown
          ? `<span class="intake-chip">${escapeHtml(breakdown)}</span>`
          : `<span class="intake-chip">${data.task_count} task${data.task_count !== 1 ? "s" : ""}</span>`,
      ].join("");
      resultEl.innerHTML = `<strong>Saved!</strong> ${chips}
        <button class="intake-view-btn" id="intake-view-meeting">View note →</button>`;
      $("#intake-view-meeting").addEventListener("click", () => {
        switchTab("groups");
        closeIntakeModal();
      });
      await refreshMeetings();
      await loadFacets();
      btn.textContent = "Done";
      cancelBtn.disabled = false;
      setTimeout(() => {
        btn.disabled = false;
        btn.textContent = "End Meeting";
      }, 4000);
    } else {
      resultEl.className = "intake-result intake-result-err";
      resultEl.innerHTML = `<strong>Error:</strong> ${escapeHtml(data.error || "Unknown error")}`;
      btn.disabled = false;
      btn.textContent = "End Meeting";
      cancelBtn.disabled = false;
    }
  } catch (err) {
    resultEl.classList.remove("hidden");
    resultEl.className = "intake-result intake-result-err";
    resultEl.innerHTML = `<strong>Error:</strong> ${escapeHtml(err.message)}`;
    btn.disabled = false;
    btn.textContent = "End Meeting";
    cancelBtn.disabled = false;
  }
}

// Phase 2 → Phase 3 transition: extract callouts from textarea, reveal review queue
function _intakeFinalizeNotes() {
  const modal = $(".modal-intake");
  modal.dataset.phase = "3";
  $("#intake-modal-title").textContent = "Post-meeting";
  $("#intake-modal-submit").textContent = "End Meeting";
  $("#intake-modal-cancel").textContent = "← Back";

  const text = ($("#intake-notes-text").value || "").trim();
  if (text) {
    const items = _extractCallouts(text).map((item) => ({ ...item, accepted: true }));
    _intakeScanResult = { text, items };
    _renderScanResults();
  }
}

async function submitIntake() {
  const modal = $(".modal-intake");
  const phase = modal.dataset.phase;
  if (phase === "1") {
    _intakeStartMeeting();
  } else if (phase === "2") {
    _intakeFinalizeNotes();
  } else {
    await _intakeSaveNotes();
  }
}

// ---------- Data fetches ----------
const refreshMeetingsDebounced = debounce(async () => {
  const qs = meetingsFilters();
  const data = await api("/api/meetings?" + qs);
  state.meetings = data.meetings;
  if (state.selectedMeetingId && !state.meetings.find((m) => m.id === state.selectedMeetingId)) {
    state.selectedMeetingId = null;
    renderDetail(null);
    updateRelDepth();
  }
  renderMeetingsList();
}, 120);

async function refreshMeetings() { return refreshMeetingsDebounced(); }

async function refreshMeetingsNow() {
  const qs = meetingsFilters();
  const data = await api("/api/meetings?" + qs);
  state.meetings = data.meetings;
  if (state.selectedMeetingId && !state.meetings.find((m) => m.id === state.selectedMeetingId)) {
    state.selectedMeetingId = null;
    renderDetail(null);
    updateRelDepth();
  }
  renderMeetingsList();
}

async function loadFacets() {
  state.facets = await api("/api/facets");
}

// People cache used by the Person picker on task modals.
async function loadPeopleCache() {
  try { state.people = await api("/api/people") || []; } catch { state.people = []; }
}
// Fill a <datalist> with person names (value = name) for autosuggest.
function fillPersonDatalist(id) {
  const dl = $("#" + id);
  if (!dl) return;
  dl.innerHTML = state.people.map((p) => `<option value="${escapeHtml(p.name)}"></option>`).join("");
}
// Resolve a typed person name back to a contact id (exact, case-insensitive). null if no match.
function resolvePersonId(name) {
  const n = (name || "").trim().toLowerCase();
  if (!n) return null;
  const hit = state.people.find((p) => (p.name || "").trim().toLowerCase() === n);
  return hit ? hit.id : null;
}
// Resolve a typed person name to a contact id, creating a new contact if none matches.
async function ensurePersonId(name) {
  const n = (name || "").trim();
  if (!n) return null;
  const existing = resolvePersonId(n);
  if (existing) return existing;
  const res = await api("/api/contacts", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: n }),
  });
  await loadPeopleCache();
  return res.id;
}

function renderBillsTable(bills) {
  const tbody = $("#bills-body");
  if (!bills.length) {
    tbody.innerHTML = `<tr><td colspan="3" style="text-align:center;color:var(--muted);padding:30px;">No bills referenced yet.</td></tr>`;
    return;
  }
  tbody.innerHTML = bills.map((b) => {
    const meetingLinks = (b.meetings || []).map((m) =>
      `<button class="bill-meeting-link" data-meeting-id="${escapeHtml(m.meeting_id)}">${escapeHtml(m.topic || m.date || "Meeting")}</button>`
    ).join(" ");
    return `<tr>
      <td><strong>${escapeHtml(b.bill_type)}${escapeHtml(b.bill_number)}</strong></td>
      <td class="bill-meetings-cell">${meetingLinks}</td>
      <td>${escapeHtml(b.last_seen || "—")}</td>
    </tr>`;
  }).join("");
  tbody.querySelectorAll(".bill-meeting-link").forEach((btn) => {
    btn.addEventListener("click", () => {
      switchTab("groups");
      selectMeeting(btn.dataset.meetingId);
    });
  });
}

async function loadGroups() {
  const [orgs, people, bills] = await Promise.all([
    api("/api/organizations"),
    api("/api/people"),
    api("/api/bills"),
  ]);
  renderOrgsTable(orgs);
  renderPeopleTable(people);
  renderBillsTable(bills);
}

async function selectMeeting(id) {
  // Toggle-deselect: slide col-3 closed first, clear content after the transition
  if (id && id === state.selectedMeetingId) {
    state.selectedMeetingId = null;
    $$("#meetings li").forEach((li) => li.classList.remove("active"));
    updateRelDepth();
    setTimeout(() => {
      if (state.selectedMeetingId === null) renderDetail(null);
    }, 300);
    return;
  }

  if (!id) {
    state.selectedMeetingId = null;
    updateRelDepth();
    setTimeout(() => {
      if (state.selectedMeetingId === null) renderDetail(null);
    }, 300);
    return;
  }

  // If meeting isn't in the current filtered list, clear the org filter and
  // re-render the meeting list BEFORE revealing anything.
  if (!state.meetings.find((m) => m.id === id)) {
    state.meetingFilters.group = "";
    state.selectedOrgId = null;
    $$("#orgs-body .stakeholder-row").forEach((r) => r.classList.remove("active"));
    const label = $("#rel-meetings-label");
    const badges = $("#rel-org-badges");
    const orgDetail = $("#rel-org-detail");
    if (label) label.textContent = "All Meetings";
    if (badges) badges.innerHTML = "";
    if (orgDetail) orgDetail.classList.add("hidden");
    await refreshMeetingsNow();
  }

  // Immediate visual feedback so the click feels responsive.
  $$("#meetings li").forEach((li) => li.classList.toggle("active", li.dataset.id === id));

  // Fetch meeting detail BEFORE flipping depth — col-3 reveals already populated.
  const m = await api(`/api/meetings/${id}`);
  state.selectedMeetingId = id;
  renderDetail(m);
  updateRelDepth();
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
  if (input) input.placeholder = "Search people, meetings, tasks, notes…";

  if (tab === "home")   renderHome();
  if (tab === "groups") {
    loadGroups();
    if (!state.meetings.length) refreshMeetings();
    updateRelDepth();
  } else {
    // Reset relationship panel state when leaving groups
    state.selectedOrgId = null;
    state.selectedMeetingId = null;
    updateRelDepth();
  }
  if (tab === "tasks")  refreshTasks();
  if (tab === "bills")  loadBillTracker();
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
  const qEl = $("#q");
  if (qEl) { qEl.blur(); qEl.value = ""; }
  const r = $("#search-results");
  if (r) r.innerHTML = "";
  _searchResultItems = [];
  _searchActiveIdx = -1;
}

// ---------- Global search ----------
let _searchResultItems = [];   // flat list of {item} in display order
let _searchActiveIdx = -1;

function _activateGroupsSubtab(subtab) {
  document.querySelectorAll(".groups-subtab").forEach((b) => b.classList.toggle("active", b.dataset.subtab === subtab));
  document.querySelectorAll(".groups-panel").forEach((p) => p.classList.add("hidden"));
  $(`#groups-panel-${subtab}`)?.classList.remove("hidden");
}

async function navigateToSearchResult(item) {
  if (!item) return;
  closeSearchOverlay();
  const type = item.type === "note"
    ? (item.entity_type === "organization" ? "org" : "person")
    : item.type;
  if (type === "person") {
    switchTab("groups");
    _activateGroupsSubtab("people");
    if (typeof selectPerson === "function") selectPerson(item.id);
  } else if (type === "org") {
    switchTab("groups");
    _activateGroupsSubtab("orgs");
    if (typeof selectOrg === "function") selectOrg(item.id);
  } else if (type === "meeting") {
    switchTab("groups");
    await refreshMeetings();
    if (typeof selectMeeting === "function") await selectMeeting(item.id);
  } else if (type === "bill") {
    switchTab("groups");
    _activateGroupsSubtab("bills");
  } else if (type === "task") {
    switchTab("tasks");
    if (state.paperOrder && state.paperOrder[0] !== "active") bringToFront("active");
    await refreshTasks();
    const idx = state.tasksByStatus.active.findIndex((t) => t.id === item.id);
    if (idx >= 0) {
      selectTask(idx);
      const el = document.querySelector(`ul[data-paper-list="active"] li[data-idx="${idx}"]`);
      if (el) el.scrollIntoView({ block: "nearest" });
    }
  }
}

function _renderSearchResults(groups) {
  const ul = $("#search-results");
  if (!ul) return;
  _searchResultItems = [];
  _searchActiveIdx = -1;
  if (!groups || !groups.length) {
    ul.innerHTML = `<li class="search-empty">No matches.</li>`;
    return;
  }
  let html = "";
  let flatIdx = 0;
  for (const g of groups) {
    html += `<li class="search-result-group-label">${escapeHtml(g.label)}</li>`;
    for (const it of g.items) {
      html += `<li class="search-result-item" data-flat-idx="${flatIdx}">
        <span class="search-result-title">${escapeHtml(it.title || "")}</span>
        ${it.subtitle ? `<span class="search-result-subtitle">${escapeHtml(it.subtitle)}</span>` : ""}
      </li>`;
      _searchResultItems.push(it);
      flatIdx++;
    }
  }
  ul.innerHTML = html;
  ul.querySelectorAll(".search-result-item").forEach((el) => {
    el.addEventListener("click", () => {
      navigateToSearchResult(_searchResultItems[Number(el.dataset.flatIdx)]);
    });
  });
}

function _setSearchActive(idx) {
  const items = $$("#search-results .search-result-item");
  if (!items.length) return;
  _searchActiveIdx = (idx + items.length) % items.length;
  items.forEach((el, i) => el.classList.toggle("active", i === _searchActiveIdx));
  items[_searchActiveIdx]?.scrollIntoView({ block: "nearest" });
}

const _runGlobalSearch = debounce(async (q) => {
  const ul = $("#search-results");
  if (!ul) return;
  if (!q.trim()) { ul.innerHTML = ""; _searchResultItems = []; _searchActiveIdx = -1; return; }
  try {
    const data = await api("/api/search?q=" + encodeURIComponent(q));
    _renderSearchResults(data.groups || []);
  } catch (_) {
    ul.innerHTML = `<li class="search-empty">Search failed.</li>`;
  }
}, 180);

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

// ---------- Bill Tracker (Congress.gov) ----------
let _billsAutoSynced = false;
let _scheduleAutoSynced = false;
let _billsRendered = {};   // id -> bill, for the right-click context menu

function _billLabel(b) {
  return `${escapeHtml(b.bill_type)} ${escapeHtml(b.bill_number)}`;
}

async function loadBillTracker() {
  await Promise.all([refreshTrackedBills(), refreshBillMatches(), refreshBillSchedule()]);
}

function _scheduleStatusLine(data) {
  // Returns {text, error} for the panel status line.
  if (!data.configured) return { text: "Set CONGRESS_API_KEY to enable", error: true };
  if (data.last_error) return { text: "⚠ last sync failed: " + data.last_error, error: true };
  const res = data.last_result || {};
  if (res.floor_ok === false) {
    const c = res.committee_events ?? 0;
    return { text: `⚠ House floor feed unavailable · ${c} committee`, error: true };
  }
  if (data.last_synced) {
    const when = new Date(data.last_synced).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    const c = res.committee_events, f = res.floor_events;
    const counts = (c == null && f == null) ? "" : ` · ${c ?? 0} committee · ${f ?? 0} floor`;
    // The committee scan is time-boxed; tell the user when there's more to cover.
    let coverage = "";
    if (res.committee_scanned != null) {
      coverage = ` · scanned ${res.committee_scanned} meeting${res.committee_scanned === 1 ? "" : "s"}`;
      if (res.committee_more) coverage += " (more next run)";
    }
    return { text: `Updated ${when}${counts}${coverage}`, error: false };
  }
  return { text: "Not yet checked", error: false };
}

async function refreshBillSchedule() {
  const wrap = $("#bill-schedule");
  if (!wrap) return;
  wrap.hidden = false;
  let data;
  try {
    data = await api("/api/bill-schedule?congress=" + encodeURIComponent(state.billsFilter.congress));
  } catch (e) {
    wrap.innerHTML = `<div class="bill-schedule-head">Upcoming — sponsored bills</div>
      <div class="bill-schedule-status is-error">⚠ couldn't load the schedule</div>`;
    return;
  }
  const events = data.events || [];
  const status = _scheduleStatusLine(data);
  const fmtDate = (s) => {
    const d = new Date(s + "T00:00:00");
    return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  };
  // The source notice for this event: committee meeting/markup page or the House
  // floor schedule on docs.house.gov — distinct from the congress.gov bill page.
  const sourceLink = (r) => {
    if (!r.url) return "";
    const label = r.source === "floor" ? "House floor schedule ↗"
      : /markup/i.test(r.event_type || "") ? "Markup notice ↗"
      : "Meeting notice ↗";
    return `<a class="bill-schedule-source" href="${escapeHtml(r.url)}" target="_blank" rel="noopener">${label}</a>`;
  };
  const body = events.length
    ? events.map((r) => {
        const isFloor = r.source === "floor";
        const kind = isFloor ? "House floor"
          : `${escapeHtml(r.event_type || "Meeting")}${r.committee_name ? " — " + escapeHtml(r.committee_name) : ""}`;
        const statusOff = r.status && r.status !== "Scheduled";
        return `
          <div class="bill-schedule-row${statusOff ? " is-off" : ""}">
            <div class="bill-schedule-date">${escapeHtml(fmtDate(r.event_date))}</div>
            <div class="bill-schedule-main">
              <div class="bill-schedule-kind">
                <span class="bill-sched-tag bill-sched-${isFloor ? "floor" : "committee"}">${escapeHtml(kind)}</span>
                ${statusOff ? `<span class="bill-sched-status">${escapeHtml(r.status)}</span>` : ""}
                ${r.working_on ? `<span class="bill-star" title="Will's Bills">★</span>` : ""}
              </div>
              <div class="bill-schedule-bill">
                <a href="${escapeHtml(r.bill_url || r.url || "#")}" target="_blank" rel="noopener">${escapeHtml(r.bill_type)} ${escapeHtml(r.bill_number)}</a>
                ${r.bill_title ? `<span class="bill-schedule-title">${escapeHtml(r.bill_title)}</span>` : ""}
              </div>
              ${r.location ? `<div class="bill-schedule-loc">${escapeHtml(r.location)}</div>` : ""}
              ${sourceLink(r)}
            </div>
          </div>`;
      }).join("")
    : `<div class="bill-schedule-empty">No upcoming committee or floor action for your sponsored bills.</div>`;
  const collapsed = localStorage.getItem("billScheduleCollapsed") === "1";
  wrap.classList.toggle("collapsed", collapsed);
  wrap.innerHTML = `
    <button type="button" class="bill-schedule-head" aria-expanded="${!collapsed}">
      <span class="bill-schedule-chevron">${collapsed ? "▸" : "▾"}</span>
      <span>Upcoming — scheduled committee &amp; floor action on <strong>sponsored</strong> bills</span>
      <span class="bill-schedule-count">${events.length}</span>
    </button>
    <div class="bill-schedule-body">
      <div class="bill-schedule-status${status.error ? " is-error" : ""}">${escapeHtml(status.text)}</div>
      ${body}
    </div>
  `;
  wrap.querySelector(".bill-schedule-head")?.addEventListener("click", () => {
    const nowCollapsed = !wrap.classList.contains("collapsed");
    wrap.classList.toggle("collapsed", nowCollapsed);
    localStorage.setItem("billScheduleCollapsed", nowCollapsed ? "1" : "0");
    const chev = wrap.querySelector(".bill-schedule-chevron");
    if (chev) chev.textContent = nowCollapsed ? "▸" : "▾";
    wrap.querySelector(".bill-schedule-head")?.setAttribute("aria-expanded", String(!nowCollapsed));
  });

  // Own once-a-day auto-refresh, independent of the bill sync.
  if (data.needs_sync && data.configured && !_scheduleAutoSynced) {
    _scheduleAutoSynced = true;
    const st = wrap.querySelector(".bill-schedule-status");
    if (st) { st.textContent = "Checking…"; st.classList.remove("is-error"); }
    try {
      await fetch("/api/bill-schedule/sync", { method: "POST" });
    } catch (e) { /* surfaced on reload via stored error */ }
    await refreshBillSchedule();
  }
}

async function refreshTrackedBills() {
  const body = $("#bills-tracker-body");
  if (body && !body.children.length) body.innerHTML = `<tr><td colspan="5" class="empty">Loading…</td></tr>`;
  let data;
  try {
    const f = state.billsFilter;
    const qs = new URLSearchParams({ relationship: f.relationship, q: f.q, congress: f.congress });
    data = await api("/api/tracked-bills?" + qs.toString());
  } catch (e) {
    if (body) body.innerHTML = `<tr><td colspan="5" class="empty">Failed to load.</td></tr>`;
    return;
  }

  // Sync status label
  const label = $("#bills-sync-label");
  if (label) {
    label.classList.toggle("is-error", !!data.last_error);
    if (!data.configured) label.textContent = "Not configured";
    else if (data.last_error) label.textContent = "⚠ last sync failed: " + data.last_error;
    else if (data.last_synced) label.textContent = "Synced " + new Date(data.last_synced).toLocaleString();
    else label.textContent = "Never synced";
  }

  // Congress selector
  const sel = $("#bills-congress-select");
  if (sel) {
    const cur = data.current_congress;
    const opts = [`<option value="current">${_ordinalNum(cur)} Congress</option>`];
    data.congresses.filter((c) => c !== cur).forEach((c) => {
      opts.push(`<option value="${c}">${_ordinalNum(c)} Congress</option>`);
    });
    opts.push(`<option value="all">All Congresses</option>`);
    sel.innerHTML = opts.join("");
    sel.value = state.billsFilter.congress;
  }

  // Count badges on the sub-tabs
  const counts = data.counts || {};
  $$("[data-bills-count]").forEach((el) => {
    const n = counts[el.dataset.billsCount];
    el.textContent = (n === undefined || n === null) ? "" : n;
  });

  // Bill list
  if (body) {
    const bills = data.bills || [];
    _billsRendered = {};
    bills.forEach((b) => { _billsRendered[b.id] = b; });
    if (!bills.length) {
      body.innerHTML = `<tr><td colspan="5" class="empty">${data.configured ? "No bills yet — try Sync now." : "Set CONGRESS_API_KEY to enable the tracker."}</td></tr>`;
    } else {
      body.innerHTML = bills.map((b) => {
        const recent = _isRecent(b.latest_action_date, 7);
        return `
        <tr data-bill-id="${escapeHtml(b.id)}">
          <td>${b.working_on ? `<span class="bill-star" title="Will's Bills">★</span>` : ""}<a href="${escapeHtml(b.url || "#")}" target="_blank" rel="noopener">${_billLabel(b)}</a></td>
          <td class="bill-title-cell">${escapeHtml(b.title || "")}</td>
          <td><span class="bill-role bill-role-${escapeHtml(b.relationship)}">${b.relationship === "sponsored" ? "Sponsor" : "Cosponsor"}</span></td>
          <td>${escapeHtml(b.introduced_date || "—")}</td>
          <td class="bill-action-cell">${recent ? `<span class="bill-recent-dot" title="Action in the last 7 days"></span>` : ""}${escapeHtml(b.latest_action || "")}</td>
        </tr>`;
      }).join("");
    }
  }

  // Auto-sync on first open of the day (once per session)
  if (data.needs_sync && data.configured && !_billsAutoSynced) {
    _billsAutoSynced = true;
    await syncBills(true);
  }
}

async function refreshBillMatches() {
  const wrap = $("#bill-matches");
  if (!wrap) return;
  let rows;
  try {
    rows = await api("/api/bill-matches");
  } catch (e) {
    wrap.hidden = true;
    return;
  }
  if (!rows.length) {
    wrap.hidden = true;
    wrap.innerHTML = "";
    return;
  }
  wrap.hidden = false;
  wrap.innerHTML = `
    <div class="bill-matches-head">Bills we were asked about — Blake has now acted on these</div>
    ${rows.map((r) => {
      const askers = (r.askers || []).map((a) =>
        escapeHtml(a.name || a.org || "someone") + (a.org && a.name ? ` (${escapeHtml(a.org)})` : "")
      ).filter(Boolean);
      const askedBy = askers.length ? askers.join(", ") : (r.meeting_org ? escapeHtml(r.meeting_org) : "a meeting note");
      const verb = r.relationship === "sponsored" ? "introduced" : "cosponsored";
      const notified = r.status === "notified";
      return `
        <div class="bill-match-card${notified ? " is-notified" : ""}" data-flag-id="${escapeHtml(r.id)}">
          <div class="bill-match-main">
            <div class="bill-match-bill">
              <a href="${escapeHtml(r.url || "#")}" target="_blank" rel="noopener">${escapeHtml(r.bill_type)} ${escapeHtml(r.bill_number)}</a>
              <span class="bill-role bill-role-${escapeHtml(r.relationship)}">Blake ${verb}</span>
            </div>
            <div class="bill-match-title">${escapeHtml(r.title || "")}</div>
            <div class="bill-match-meta">
              Asked by <strong>${askedBy}</strong>
              ${r.meeting_date ? ` · ${escapeHtml(r.meeting_date)}` : ""}
              ${r.meeting_topic ? ` · ${escapeHtml(r.meeting_topic)}` : ""}
            </div>
          </div>
          <div class="bill-match-actions">
            ${notified
              ? `<span class="bill-match-done">✓ Notified</span><button class="linkish" data-match-status="new">Undo</button>`
              : `<button class="primary-btn-sm" data-match-status="notified">Mark notified</button>
                 <button class="linkish" data-match-status="dismissed">Dismiss</button>`}
          </div>
        </div>`;
    }).join("")}
  `;
  wrap.querySelectorAll("[data-match-status]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const card = btn.closest(".bill-match-card");
      const id = card?.dataset.flagId;
      if (!id) return;
      try {
        await api(`/api/bill-matches/${id}/status`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: btn.dataset.matchStatus }),
        });
        refreshBillMatches();
      } catch (e) { /* ignore */ }
    });
  });
}

// POST one sync step and return its JSON, throwing a readable error on failure.
async function _syncStep(qs) {
  const r = await fetch("/api/tracked-bills/sync?" + qs, { method: "POST" });
  const data = await r.json().catch(() => ({}));
  if (!r.ok || data.ok === false) throw new Error(data.error || `HTTP ${r.status}`);
  return data;
}

// Page through one relationship (sponsored|cosponsored), reporting running totals.
async function _syncRelationship(step, onProgress) {
  let offset = 0, stored = 0;
  // Cap iterations defensively so a misbehaving next_offset can't loop forever.
  for (let i = 0; i < 12; i++) {
    const data = await _syncStep(`step=${step}&offset=${offset}`);
    stored += data.stored || 0;
    onProgress(stored);
    if (data.next_offset == null) break;
    offset = data.next_offset;
  }
  return stored;
}

async function syncBills(silent) {
  const btn = $("#bills-sync-btn");
  const label = $("#bills-sync-label");
  const setStatus = (text, isError) => {
    if (!label) return;
    label.textContent = text;
    label.classList.toggle("is-error", !!isError);
  };
  if (btn) { btn.disabled = true; btn.textContent = "Syncing…"; btn.classList.add("is-syncing"); }
  try {
    const parts = [];
    const render = (extra) => setStatus([...parts, extra].filter(Boolean).join(" · "), false);

    render("Fetching sponsored…");
    const sp = await _syncRelationship("sponsored", (n) => render(`Sponsored ${n}…`));
    parts.push(`Sponsored ${sp}`);

    render("Fetching cosponsored…");
    const co = await _syncRelationship("cosponsored", (n) => render(`Cosponsored ${n}…`));
    parts.push(`Cosponsored ${co}`);

    render("Matching meeting notes…");
    const m = await _syncStep("step=match");
    if (m.new_matches) parts.push(`${m.new_matches} new match${m.new_matches === 1 ? "" : "es"}`);

    // Refresh the upcoming schedule too — best-effort, never fails the bill sync.
    render("Updating schedule…");
    try {
      await fetch("/api/bill-schedule/sync", { method: "POST" });
    } catch (e2) { console.error("schedule sync failed", e2); }
    await refreshBillSchedule();
    // refreshTrackedBills() runs last so the label settles on "Synced <time>".
    await Promise.all([refreshTrackedBills(), refreshBillMatches()]);
  } catch (e) {
    setStatus("Sync failed: " + e.message, true);
    console.error("bill sync failed", e);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Sync now"; btn.classList.remove("is-syncing"); }
  }
}

// Right-click a bill row → toggle "Will's Bills"
function openBillContextMenu(e, bill) {
  e.preventDefault();
  const menu = $("#bill-ctx-menu");
  if (!menu) return;
  menu.dataset.billId = bill.id;
  menu.innerHTML = (bill.working_on
    ? `<div class="ctx-item" data-bill-action="unset">☆ Remove from Will's Bills</div>`
    : `<div class="ctx-item" data-bill-action="set">★ Add to Will's Bills</div>`)
    + `<div class="ctx-item" data-bill-action="details">↗ View details</div>`
    + `<div class="ctx-item" data-bill-action="refresh">⟳ Refresh from Congress.gov</div>`;
  const menuW = 220, menuH = 120;
  let x = e.clientX, y = e.clientY;
  if (x + menuW > window.innerWidth)  x = window.innerWidth - menuW - 8;
  if (y + menuH > window.innerHeight) y = window.innerHeight - menuH - 8;
  menu.style.left = x + "px";
  menu.style.top = y + "px";
  menu.classList.remove("hidden");
  menu.classList.add("visible");
}

function closeBillContextMenu() {
  const menu = $("#bill-ctx-menu");
  if (!menu) return;
  menu.classList.remove("visible");
  menu.classList.add("hidden");
  delete menu.dataset.billId;
}

function _ordinalNum(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

// True if an ISO date (YYYY-MM-DD) is within the last `days` days.
function _isRecent(dateStr, days) {
  if (!dateStr) return false;
  const d = new Date(dateStr + "T00:00:00");
  if (isNaN(d)) return false;
  return (Date.now() - d.getTime()) <= days * 86400000;
}

// ---------- Bill detail drawer ----------
function _relTime(iso) {
  if (!iso) return "";
  const then = new Date(iso), now = new Date();
  const mins = Math.round((now - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return then.toLocaleDateString();
}

function closeBillDrawer() {
  const bd = $("#bill-drawer-backdrop");
  if (!bd) return;
  bd.classList.remove("visible");
  bd.classList.add("hidden");
}

async function openBillDrawer(billId, opts) {
  const bd = $("#bill-drawer-backdrop");
  const el = $("#bill-drawer-content");
  if (!bd || !el) return;
  bd.classList.remove("hidden");
  requestAnimationFrame(() => bd.classList.add("visible"));
  const force = !!(opts && opts.force);
  el.innerHTML = `<div class="detail-empty">${force ? "Refreshing from Congress.gov…" : "Loading bill…"}</div>`;
  let data;
  try {
    const id = encodeURIComponent(billId);
    // A forced open re-fetches headline fields + detail via the refresh endpoint;
    // a normal open serves the cached detail (fetching only if it's never been built).
    data = force
      ? await api(`/api/tracked-bills/${id}/refresh`, { method: "POST" })
      : await api(`/api/tracked-bills/${id}/detail`);
  } catch (e) {
    el.innerHTML = `<div class="detail-empty">Couldn't load this bill — ${escapeHtml(e.message)}</div>`;
    return;
  }
  if (force) refreshTrackedBills();
  renderBillDrawer(el, data);
}

function renderBillDrawer(el, data) {
  const b = data.bill || {};
  const d = data.detail || {};
  const fmtDate = (s) => s ? new Date(s + "T00:00:00").toLocaleDateString(undefined,
    { year: "numeric", month: "short", day: "numeric" }) : "";
  const role = b.relationship === "sponsored" ? "Sponsor" : "Cosponsor";

  const actions = (d.actions || []);
  const timeline = actions.length
    ? `<ol class="bill-timeline">${actions.map((a) => `
        <li>
          <span class="bill-timeline-date">${escapeHtml(fmtDate(a.date) || a.date || "")}</span>
          <span class="bill-timeline-text">${escapeHtml(a.text || "")}</span>
        </li>`).join("")}</ol>`
    : `<div class="bill-section-empty">No recorded actions.</div>`;

  const cos = (d.cosponsors || []);
  const cosList = cos.length
    ? `<ul class="bill-cosponsors">${cos.map((c) => `
        <li>${escapeHtml(c.name || "")}${c.party || c.state
          ? ` <span class="muted">(${escapeHtml([c.party, c.state].filter(Boolean).join("-"))})</span>` : ""}</li>`).join("")}</ul>`
    : `<div class="bill-section-empty">No cosponsors.</div>`;

  const committees = (d.committees || []);
  const comList = committees.length
    ? `<ul class="bill-committees">${committees.map((c) => `
        <li>${escapeHtml(c.name || "")}${c.chamber ? ` <span class="muted">(${escapeHtml(c.chamber)})</span>` : ""}</li>`).join("")}</ul>`
    : `<div class="bill-section-empty">No committee referrals.</div>`;

  const texts = (d.text_versions || []).filter((t) => t.url);
  const sources = [];
  if (b.url) sources.push(`<a href="${escapeHtml(b.url)}" target="_blank" rel="noopener">Congress.gov bill page ↗</a>`);
  texts.forEach((t) => sources.push(
    `<a href="${escapeHtml(t.url)}" target="_blank" rel="noopener">${escapeHtml(t.type || "Bill text")}${t.date ? " (" + escapeHtml(fmtDate(t.date) || t.date) + ")" : ""} ↗</a>`));

  el.innerHTML = `
    <header class="bill-drawer-head">
      <h1>${escapeHtml(b.bill_type || "")} ${escapeHtml(b.bill_number || "")}
        <span class="bill-role bill-role-${escapeHtml(b.relationship || "")}">${role}</span>
        ${b.working_on ? `<span class="bill-star" title="Will's Bills">★</span>` : ""}
      </h1>
      ${b.title ? `<div class="bill-drawer-title">${escapeHtml(b.title)}</div>` : ""}
      <div class="bill-drawer-meta">
        ${d.policy_area ? `<span>${escapeHtml(d.policy_area)}</span>` : ""}
        ${b.introduced_date ? `<span>Introduced ${escapeHtml(fmtDate(b.introduced_date))}</span>` : ""}
        ${typeof d.cosponsors_count === "number" ? `<span>${d.cosponsors_count} cosponsor${d.cosponsors_count === 1 ? "" : "s"}</span>` : ""}
      </div>
      <div class="bill-drawer-actions">
        <button class="secondary-btn-sm" id="bill-drawer-refresh" data-bill-id="${escapeHtml(b.id || "")}">Refresh</button>
        <span class="bill-drawer-fresh">${[
          data.detail_synced ? "Detail " + _relTime(data.detail_synced) : "",
          b.last_synced ? "record " + _relTime(b.last_synced) : "",
        ].filter(Boolean).map(escapeHtml).join(" · ")}</span>
      </div>
    </header>
    ${b.latest_action ? `<section class="bill-section">
      <h2>Latest action</h2>
      <div class="bill-latest-action">${escapeHtml(b.latest_action)}${b.latest_action_date ? ` <span class="muted">— ${escapeHtml(fmtDate(b.latest_action_date))}</span>` : ""}</div>
    </section>` : ""}
    ${d.summary ? `<section class="bill-section"><h2>Summary</h2><div class="bill-summary">${escapeHtml(d.summary).replace(/&lt;[^&]*&gt;/g, "")}</div></section>` : ""}
    <section class="bill-section"><h2>Action timeline</h2>${timeline}</section>
    <section class="bill-section"><h2>Cosponsors</h2>${cosList}</section>
    <section class="bill-section"><h2>Committees</h2>${comList}</section>
    <section class="bill-section"><h2>Sources</h2>
      <div class="bill-sources">${sources.length ? sources.join("") : `<span class="bill-section-empty">No source links.</span>`}</div>
    </section>
  `;
  $("#bill-drawer-refresh")?.addEventListener("click", async (e) => {
    const id = e.currentTarget.dataset.billId;
    if (!id) return;
    const btn = e.currentTarget;
    btn.disabled = true; btn.textContent = "Refreshing…";
    try {
      const data = await api(`/api/tracked-bills/${encodeURIComponent(id)}/refresh`, { method: "POST" });
      renderBillDrawer($("#bill-drawer-content"), data);
      refreshTrackedBills();  // headline action may have changed
    } catch (err) {
      btn.disabled = false; btn.textContent = "Refresh";
      console.error("bill refresh failed", err);
    }
  });
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
  { label: "Go to Relationships", icon: "🤝", action: () => { closeCommandPalette(); switchTab("groups"); } },
  { label: "Go to Bill Tracker", icon: "📜", action: () => { closeCommandPalette(); switchTab("bills"); } },
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

  // Tasks segmented control (phone) — reuses bringToFront to swap the visible list
  document.querySelector(".tasks-segmented")?.addEventListener("click", (e) => {
    const b = e.target.closest(".seg-btn");
    if (!b) return;
    document.querySelectorAll(".seg-btn").forEach((x) => x.classList.toggle("active", x === b));
    bringToFront(b.dataset.seg);
  });

  // Safe-triangle dock submenus — handles all .dock-btn-wrap elements
  (function () {
    function inTriangle(px, py, ax, ay, bx, by, cx, cy) {
      const s = (x1, y1, x2, y2, x3, y3) => (x1 - x3) * (y2 - y3) - (x2 - x3) * (y1 - y3);
      const d1 = s(px,py,ax,ay,bx,by), d2 = s(px,py,bx,by,cx,cy), d3 = s(px,py,cx,cy,ax,ay);
      return !((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0));
    }

    document.querySelectorAll(".dock-btn-wrap").forEach((wrap) => {
      const btn = wrap.querySelector(".dock-btn");
      const sub = wrap.querySelector(".dock-submenu");
      if (!btn || !sub) return;
      let closeTimer = null;
      let exitPt = null;

      function open() { clearTimeout(closeTimer); sub.classList.add("open"); }
      function close() { sub.classList.remove("open"); exitPt = null; }
      function scheduleClose() { clearTimeout(closeTimer); closeTimer = setTimeout(close, 80); }

      btn.addEventListener("mouseenter", open);
      btn.addEventListener("mouseleave", (e) => { exitPt = { x: e.clientX, y: e.clientY }; scheduleClose(); });
      sub.addEventListener("mouseenter", () => clearTimeout(closeTimer));
      sub.addEventListener("mouseleave", scheduleClose);

      document.addEventListener("mousemove", (e) => {
        if (!sub.classList.contains("open") || !exitPt) return;
        if (e.target.closest(".dock-btn-wrap") === wrap) { clearTimeout(closeTimer); return; }
        const r = sub.getBoundingClientRect();
        if (inTriangle(e.clientX, e.clientY, exitPt.x, exitPt.y, r.left, r.top - 4, r.left, r.bottom + 4)) {
          clearTimeout(closeTimer);
        } else {
          close();
        }
      });
    });
  })();

  // People shortcut from Relationships submenu
  $("#dock-sub-people").addEventListener("click", () => {
    switchTab("groups");
    document.querySelectorAll(".groups-subtab").forEach((b) => b.classList.remove("active"));
    document.querySelector(".groups-subtab[data-subtab='people']").classList.add("active");
    document.querySelectorAll(".groups-panel").forEach((p) => p.classList.add("hidden"));
    $("#groups-panel-people").classList.remove("hidden");
  });

  // Bills shortcut from Groups submenu
  $("#dock-sub-bills").addEventListener("click", () => {
    switchTab("groups");
    // Activate the Bills sub-tab
    document.querySelectorAll(".groups-subtab").forEach((b) => b.classList.remove("active"));
    document.querySelector(".groups-subtab[data-subtab='bills']").classList.add("active");
    document.querySelectorAll(".groups-panel").forEach((p) => p.classList.add("hidden"));
    $("#groups-panel-bills").classList.remove("hidden");
  });

  // Search overlay
  $("#dock-search-btn").addEventListener("click", openSearchOverlay);
  $("#search-overlay-backdrop").addEventListener("click", closeSearchOverlay);

  // Global search — unified cross-entity results in the overlay
  $("#q").addEventListener("input", (e) => {
    _runGlobalSearch(e.target.value);
  });
  $("#q").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); _setSearchActive(_searchActiveIdx + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); _setSearchActive(_searchActiveIdx - 1); }
    else if (e.key === "Enter") {
      const item = _searchResultItems[_searchActiveIdx] || _searchResultItems[0];
      if (item) { e.preventDefault(); navigateToSearchResult(item); }
    }
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
  $("#me-close").addEventListener("click",  closeMeetingEditModal);
  $("#me-cancel").addEventListener("click", closeMeetingEditModal);
  $("#me-submit").addEventListener("click", submitMeetingEditModal);
  $("#ics-meeting-edit-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "ics-meeting-edit-backdrop") closeMeetingEditModal();
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
    switchTab("groups");
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
  $("#hero-upload-ics").addEventListener("click", () => $("#ics-upload-input").click());
  $("#ics-upload-input").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) uploadICS(file);
  });

  // Deadline strip
  $("#deadlines-strip").addEventListener("click", (e) => {
    const day = e.target.closest(".deadline-day");
    if (!day) return;
    switchTab("tasks");
    $("#q").value = day.dataset.date;
    refreshTasks();
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

  // Subtask toggle chips (event delegation on paper-stack)
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
    // Find the task in any state list
    let task = null;
    for (const paper of ["active", "backburner", "done"]) {
      task = state.tasksByStatus[paper].find((t) => t.id === taskId);
      if (task) break;
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

  // Bill Tracker: sync, sub-tabs, search, congress selector
  $("#bills-sync-btn")?.addEventListener("click", () => syncBills(false));
  $("#view-bills")?.addEventListener("click", (e) => {
    const tab = e.target.closest(".groups-subtab[data-bills-rel]");
    if (tab) {
      document.querySelectorAll(".groups-subtab[data-bills-rel]").forEach((b) => b.classList.remove("active"));
      tab.classList.add("active");
      state.billsFilter.relationship = tab.dataset.billsRel;
      refreshTrackedBills();
      return;
    }
    // Click a bill row (but not the congress.gov link) to open the detail drawer.
    const tr = e.target.closest("#bills-tracker-body tr[data-bill-id]");
    if (tr && !e.target.closest("a")) {
      openBillDrawer(tr.dataset.billId);
    }
  });
  $("#bills-search")?.addEventListener("input", debounce(() => {
    state.billsFilter.q = $("#bills-search").value.trim();
    refreshTrackedBills();
  }, 250));
  $("#bills-congress-select")?.addEventListener("change", () => {
    state.billsFilter.congress = $("#bills-congress-select").value;
    refreshTrackedBills();
    refreshBillSchedule();
  });
  // Right-click a bill row → Will's Bills toggle menu
  $("#bills-tracker-body")?.addEventListener("contextmenu", (e) => {
    const tr = e.target.closest("tr[data-bill-id]");
    if (!tr) return;
    const bill = _billsRendered[tr.dataset.billId];
    if (bill) openBillContextMenu(e, bill);
  });
  $("#bill-ctx-menu")?.addEventListener("click", async (e) => {
    const item = e.target.closest(".ctx-item");
    const menu = $("#bill-ctx-menu");
    const id = menu?.dataset.billId;
    if (!item || !id) return;
    const action = item.dataset.billAction;
    closeBillContextMenu();
    if (action === "details") { openBillDrawer(id); return; }
    if (action === "refresh") { openBillDrawer(id, { force: true }); return; }
    try {
      await api(`/api/tracked-bills/${id}/working`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ working: action === "set" }),
      });
      refreshTrackedBills();
    } catch (err) { console.error("toggle Will's Bills failed", err); }
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#bill-ctx-menu")) closeBillContextMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeBillContextMenu();
  }, true);

  // Bill detail drawer close
  $("#bill-drawer-close")?.addEventListener("click", closeBillDrawer);
  $("#bill-drawer-backdrop")?.addEventListener("click", (e) => {
    if (e.target.id === "bill-drawer-backdrop") closeBillDrawer();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeBillDrawer();
  });

  // Snoozed filter
  $("#t-snoozed")?.addEventListener("change", () => { refreshTasks(); updateTaskFilterToggleState(); });

  // Relationships back buttons
  document.querySelector("#rel-back-to-list")?.addEventListener("click", () => selectOrg(null));
  document.querySelector("#rel-back-to-meetings")?.addEventListener("click", () => selectMeeting(null));

  // Meeting list click
  $("#meetings").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-id]");
    if (li) selectMeeting(li.dataset.id);
  });


  // Groups sub-tab toggle (scoped to the Relationships bar so it ignores other
  // reusers of .groups-subtab such as the Bill Tracker's relationship tabs)
  document.querySelectorAll(".groups-subtabs-bar .groups-subtab").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".groups-subtabs-bar .groups-subtab").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const subtab = btn.dataset.subtab;
      document.querySelectorAll(".groups-panel").forEach((p) => p.classList.add("hidden"));
      $(`#groups-panel-${subtab}`).classList.remove("hidden");
      if (subtab !== "orgs") selectOrg(null);
    });
  });

  $("#add-person-btn")?.addEventListener("click", openAddPersonModal);

  // Intake modal
  $("#intake-modal-close").addEventListener("click", closeIntakeModal);
  $("#intake-modal-cancel").addEventListener("click", () => {
    const modal = $(".modal-intake");
    const phase = modal.dataset.phase;
    if (phase === "3") {
      // Back to meeting (canvas)
      modal.dataset.phase = "2";
      $("#intake-modal-title").textContent = "In Meeting";
      $("#intake-modal-submit").textContent = "Finalize Notes";
      $("#intake-modal-submit").disabled = false;
      $("#intake-modal-cancel").textContent = "← Back";
    } else if (phase === "2") {
      // Back to pre-meeting form
      modal.dataset.phase = "1";
      $("#intake-modal-title").textContent = "Pre-meeting";
      $("#intake-modal-submit").textContent = "Start Meeting →";
      $("#intake-modal-cancel").textContent = "← Back";
    } else if (phase === "1") {
      // Back to type picker
      modal.dataset.phase = "0";
      $("#intake-modal-title").textContent = "New Meeting";
      $("#intake-modal-cancel").textContent = "Cancel";
    } else {
      closeIntakeModal();
    }
  });
  // No backdrop-tap-to-close: too easy to accidentally dismiss with a resting palm on iPad
  $("#intake-modal-submit").addEventListener("click", submitIntake);

  // Meeting edit modal
  $("#meeting-edit-close").addEventListener("click", () => {
    $("#meeting-edit-backdrop").classList.add("hidden");
  });
  $("#meeting-edit-cancel").addEventListener("click", () => {
    $("#meeting-edit-backdrop").classList.add("hidden");
  });
  $("#meeting-edit-save").addEventListener("click", _saveMeetingEdit);
  $("#meeting-edit-backdrop").addEventListener("click", (e) => {
    if (e.target.id === "meeting-edit-backdrop") {
      $("#meeting-edit-backdrop").classList.add("hidden");
    }
  });

  // Populate intake group autocomplete on first focus
  let _intakeGroupsLoaded = false;
  $("#intake-group").addEventListener("focus", async () => {
    if (_intakeGroupsLoaded) return;
    _intakeGroupsLoaded = true;
    const groups = await _loadCanonicalGroups();
    const datalist = $("#intake-group-list");
    datalist.innerHTML = groups.map(g => `<option value="${escapeHtml(g)}">`).join("");
  });

  // Today's Callouts modal
  $("#today-callouts-close")?.addEventListener("click", closeTodayCalloutsModal);
  $("#today-callouts-backdrop")?.addEventListener("click", (e) => {
    if (e.target.id === "today-callouts-backdrop") closeTodayCalloutsModal();
  });
  $("#today-callouts-date")?.addEventListener("change", (e) => {
    if (e.target.value) _loadTodayCallouts(e.target.value);
  });
  $("#today-callouts-prev")?.addEventListener("click", () => {
    const d = new Date((_todayCalloutsDate || new Date().toISOString().slice(0, 10)) + "T12:00:00");
    d.setDate(d.getDate() - 1);
    _loadTodayCallouts(d.toISOString().slice(0, 10));
  });
  $("#today-callouts-next")?.addEventListener("click", () => {
    const d = new Date((_todayCalloutsDate || new Date().toISOString().slice(0, 10)) + "T12:00:00");
    d.setDate(d.getDate() + 1);
    _loadTodayCallouts(d.toISOString().slice(0, 10));
  });
  $("#today-callouts-today-btn")?.addEventListener("click", () => {
    _loadTodayCallouts(new Date().toISOString().slice(0, 10));
  });

  // Phase 0 type picker
  $("#intake-phase0").addEventListener("click", (e) => {
    const card = e.target.closest(".intake-type-card");
    if (card) _intakeSelectType(card.dataset.type);
  });

  // Collapse any expanded chip when clicking outside
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".intake-meta-chip")) {
      document.querySelectorAll(".intake-meta-chip--expanded").forEach((c) => _intakeCollapseChip(c));
    }
  });

  // Pre-meeting brief: fetch org history when group field changes in Phase 1
  const _intakeGroupInput = $("#intake-group");
  let _intakeBriefTimeout = null;
  const _fetchIntakeBrief = async () => {
    const val = _intakeGroupInput.value.trim();
    const briefEl = $("#intake-brief");
    if (!briefEl) return;
    if (!val) { briefEl.classList.add("hidden"); briefEl.innerHTML = ""; return; }
    const slug = val.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);
    if (!slug) { briefEl.classList.add("hidden"); return; }
    try {
      const data = await api(`/api/organizations/${slug}/brief`);
      if (!data || data.error) { briefEl.classList.add("hidden"); return; }
      const lines = [];
      if (data.last_meeting) lines.push(`<div class="brief-row"><span class="brief-label">Last met</span> <span>${escapeHtml(data.last_meeting.date || "")} — ${escapeHtml((data.last_meeting.attendees || []).join(", ") || "no attendees listed")}</span></div>`);
      if (data.open_asks && data.open_asks.length) lines.push(`<div class="brief-row"><span class="brief-label brief-label--ask">Open asks (${data.open_asks.length})</span> <span>${data.open_asks.map((a) => escapeHtml(a.text)).join("; ")}</span></div>`);
      if (data.open_commitments && data.open_commitments.length) lines.push(`<div class="brief-row"><span class="brief-label brief-label--commitment">Commitments owed (${data.open_commitments.length})</span> <span>${data.open_commitments.map((c) => escapeHtml(c.text)).join("; ")}</span></div>`);
      if (data.bills && data.bills.length) lines.push(`<div class="brief-row"><span class="brief-label">Bills raised</span> <span>${data.bills.map((b) => escapeHtml(`${b.bill_type}${b.bill_number}`)).join(", ")}</span></div>`);
      if (!lines.length && !data.last_meeting) {
        briefEl.innerHTML = `<div class="brief-empty">No prior history with this group.</div>`;
      } else {
        briefEl.innerHTML = `<div class="brief-header">Before this meeting</div>${lines.join("")}`;
      }
      briefEl.classList.remove("hidden");
    } catch (_) { briefEl.classList.add("hidden"); }
  };
  _intakeGroupInput.addEventListener("blur", _fetchIntakeBrief);
  _intakeGroupInput.addEventListener("change", () => {
    clearTimeout(_intakeBriefTimeout);
    _intakeBriefTimeout = setTimeout(_fetchIntakeBrief, 400);
  });

  // Re-scan button
  $("#intake-rescan-btn").addEventListener("click", _intakeScan);

  // Scan result clear
  $("#review-queue-clear").addEventListener("click", () => {
    _intakeScanResult = null;
    _renderScanResults();
  });
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
      if (!$("#today-callouts-backdrop").classList.contains("hidden")) { closeTodayCalloutsModal(); return; }
      if ($("#search-overlay").classList.contains("open")) { closeSearchOverlay(); return; }
      if (!$("#intake-modal-backdrop").classList.contains("hidden")) {
        closeIntakeModal();
        return;
      }
      if (!$("#import-modal-backdrop").classList.contains("hidden"))  { closeImportModal(); return; }
      if (!$("#edit-modal-backdrop").classList.contains("hidden"))    { closeEditModal(); return; }
      if (!$("#nl-modal-backdrop").classList.contains("hidden"))       { closeNLModal(); return; }
      if (state.drawerTask) { closeDrawer(); return; }
      if (typing) { document.activeElement.blur(); return; }
    }
    if (typing) return;
    if (e.key === "/") { e.preventDefault(); openSearchOverlay(); return; }
    if (e.key === "1") { e.preventDefault(); switchTab("home"); return; }
    if (e.key === "2") { e.preventDefault(); switchTab("tasks"); return; }
    if (e.key === "3") { e.preventDefault(); switchTab("groups"); return; }
    if (e.key === "4") { e.preventDefault(); switchTab("bills"); return; }
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
    } else if (state.tab === "groups") {
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
  loadPeopleCache();
  switchTab("home");
});

// --- Canvas fullscreen ---
const _openCanvasFullscreen = (function () {
  const overlay = $("#canvas-fullscreen-overlay");
  const img = $("#canvas-fullscreen-img");

  function openFullscreen(src) {
    img.src = src;
    overlay.classList.remove("hidden");
    document.body.style.overflow = "hidden";
  }
  function closeFullscreen() {
    overlay.classList.add("hidden");
    img.src = "";
    document.body.style.overflow = "";
  }

  $("#canvas-fullscreen-close").addEventListener("click", closeFullscreen);

  let _tapCount = 0, _tapTimer = null;
  overlay.addEventListener("click", (e) => {
    if (e.target === $("#canvas-fullscreen-close")) return;
    _tapCount++;
    clearTimeout(_tapTimer);
    if (_tapCount >= 3) { _tapCount = 0; closeFullscreen(); return; }
    _tapTimer = setTimeout(() => { _tapCount = 0; }, 500);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !overlay.classList.contains("hidden")) closeFullscreen();
  });

  return openFullscreen;
})();
