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
