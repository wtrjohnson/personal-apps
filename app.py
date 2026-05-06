#!/usr/bin/env python3
"""Notes Dashboard — Phases 1 & 2.

Phase 1: browse/search meetings from ../meetings/, rendered markdown detail view.
Phase 2: unified task view across all meetings + a tasks.md for free-form tasks,
         with write-back (toggling a task checks it off in the source .md file).

Source of truth remains the .md files. YAML front matter is re-derived from the
body on every write so the two stay consistent.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from datetime import date as date_cls, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import frontmatter
import markdown as md_lib
import yaml
from flask import Flask, jsonify, render_template, request, abort

# --------------------------------------------------
# CONFIG
# --------------------------------------------------

DASHBOARD_DIR = Path(__file__).resolve().parent
NOTES_ROOT = DASHBOARD_DIR.parent
MEETINGS_DIR = NOTES_ROOT / "meetings"
TASKS_FILE = NOTES_ROOT / "tasks.md"
ALIASES_FILE = DASHBOARD_DIR / "groups.yaml"
BACKBURNER_FILE = DASHBOARD_DIR / "backburner.yaml"
COMPLETIONS_FILE = DASHBOARD_DIR / "completions.jsonl"

PORT = int(os.environ.get("DASHBOARD_PORT", "5050"))
HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")

# Reuse the upstream parser so YAML regeneration stays in sync
sys.path.insert(0, str(NOTES_ROOT))
try:
    from process_meeting_notes import extract_fields_and_tasks, process_file as _pm_process_file  # type: ignore
except Exception as e:
    print(f"[warn] Could not import process_meeting_notes: {e}")
    extract_fields_and_tasks = None  # we'll fall back to our own parser below
    _pm_process_file = None


# --------------------------------------------------
# DATA MODEL
# --------------------------------------------------


@dataclass
class Meeting:
    id: str
    filename: str
    path: str
    date: Optional[str]
    raw_group: str
    canonical_group: str
    topic: str
    purpose: List[str]
    outcome: str
    deadline: str
    attendees: str
    action_items_open: List[str]
    action_items_done: List[str]
    reminders_open: List[str]
    reminders_done: List[str]
    body: str
    body_html: str
    mtime: float

    def summary(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "date": self.date,
            "raw_group": self.raw_group,
            "group": self.canonical_group,
            "topic": self.topic,
            "purpose": self.purpose,
            "outcome": self.outcome,
            "deadline": self.deadline,
            "attendees": self.attendees,
            "open_action_items_count": len(self.action_items_open),
            "open_reminders_count": len(self.reminders_open),
        }

    def full(self) -> Dict[str, Any]:
        return {
            **self.summary(),
            "action_items_open": self.action_items_open,
            "action_items_done": self.action_items_done,
            "reminders_open": self.reminders_open,
            "reminders_done": self.reminders_done,
            "body": self.body,
            "body_html": self.body_html,
        }


@dataclass
class Task:
    id: str
    text: str
    type: str                 # "action" | "reminder" | "free"
    done: bool
    backburner: bool
    source_filename: str      # e.g. "2026-04-13 - Rebekah One on One.md" or "tasks.md"
    section: str              # "action_items" | "reminders" | "free"
    meeting_id: Optional[str] # if from a meeting
    group: Optional[str]      # canonical group (notes-derived) or free-form tag
    source_date: Optional[str]
    deadline: Optional[str]   # parsed YYYY-MM-DD if any
    deadline_raw: Optional[str]  # the raw text that was parsed out
    overdue: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "type": self.type,
            "done": self.done,
            "backburner": self.backburner,
            "source_filename": self.source_filename,
            "section": self.section,
            "meeting_id": self.meeting_id,
            "group": self.group,
            "source_date": self.source_date,
            "deadline": self.deadline,
            "deadline_raw": self.deadline_raw,
            "overdue": self.overdue,
        }


# --------------------------------------------------
# BACKBURNER STORE (task IDs hidden from main view)
# --------------------------------------------------


def load_backburner() -> set:
    if not BACKBURNER_FILE.exists():
        return set()
    try:
        data = yaml.safe_load(BACKBURNER_FILE.read_text(encoding="utf-8")) or {}
        items = data.get("backburner", []) or []
        return {str(x) for x in items}
    except Exception as e:
        print(f"[backburner] load error: {e}")
        return set()


def save_backburner(ids: set) -> None:
    data = {"backburner": sorted(ids)}
    BACKBURNER_FILE.write_text(
        "# Task IDs on backburner. Managed by the dashboard — no need to edit by hand.\n"
        "# Remove a line (or clear via the UI) to bring a task back to the main view.\n\n"
        + yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def toggle_backburner(task_id: str, on: bool) -> bool:
    ids = load_backburner()
    if on:
        if task_id in ids:
            return True
        ids.add(task_id)
    else:
        if task_id not in ids:
            return True
        ids.discard(task_id)
    save_backburner(ids)
    return True


# --------------------------------------------------
# COMPLETION LOG (timestamped task completion events)
# --------------------------------------------------

import json
from datetime import timedelta


def log_completion(task_id: str, text: str, section: str, filename: str, done: bool) -> None:
    """Append a timestamped completion event. done=True when a task was marked done;
    done=False when un-marked (so the chart can subtract)."""
    try:
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "date": date_cls.today().isoformat(),
            "id": task_id,
            "text": text[:200],
            "section": section,
            "filename": filename,
            "done": bool(done),
        }
        with COMPLETIONS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[completions] log error: {e}")


def load_completions() -> List[Dict[str, Any]]:
    if not COMPLETIONS_FILE.exists():
        return []
    out = []
    try:
        for line in COMPLETIONS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    except Exception as e:
        print(f"[completions] load error: {e}")
    return out


def completions_per_day(days: int = 30) -> List[Dict[str, Any]]:
    """Return a list of {date, count} for the last `days` days (oldest → newest).
    Net count per day: +1 for done events, -1 for un-done events. Never negative."""
    today = date_cls.today()
    window = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    buckets: Dict[str, int] = {d.isoformat(): 0 for d in window}
    for entry in load_completions():
        d = entry.get("date")
        if not d or d not in buckets:
            continue
        buckets[d] += 1 if entry.get("done") else -1
    return [{"date": d, "count": max(0, n)} for d, n in buckets.items()]


# --------------------------------------------------
# DEADLINE PARSING
# --------------------------------------------------

_DATE_WORD = r"(?:deadline|due|by)"

DEADLINE_PATTERNS = [
    # "deadline 2/03/2026", "due: 2026-03-15", "by 3/15/26"
    re.compile(rf"{_DATE_WORD}\s*:?\s*(\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}[/-]\d{{1,2}}(?:[/-]\d{{2,4}})?)",
               re.IGNORECASE),
    # "(deadline 1/31)" — parenthetical with no year
    re.compile(rf"\(\s*{_DATE_WORD}\s+(\d{{1,2}}[/-]\d{{1,2}}(?:[/-]\d{{2,4}})?)\s*\)", re.IGNORECASE),
]


def _normalize_date(raw: str, context_year: Optional[int] = None) -> Optional[str]:
    """Normalize a parsed date fragment to YYYY-MM-DD. Returns None if unparseable."""
    raw = raw.strip().rstrip(").,;:")
    # YYYY-MM-DD or YYYY/MM/DD
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", raw)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})(?:[-/](\d{2,4}))?", raw)
        if not m:
            return None
        mo, d, y_raw = m.group(1), m.group(2), m.group(3)
        mo, d = int(mo), int(d)
        if y_raw is None:
            y = context_year or datetime.now().year
        else:
            y = int(y_raw)
            if y < 100:
                y += 2000
    try:
        return date_cls(y, mo, d).isoformat()
    except Exception:
        return None


def extract_deadline(text: str, context_year: Optional[int] = None) -> Tuple[Optional[str], Optional[str]]:
    """Return (YYYY-MM-DD, raw_fragment_text) or (None, None)."""
    for pat in DEADLINE_PATTERNS:
        m = pat.search(text)
        if m:
            normalized = _normalize_date(m.group(1), context_year=context_year)
            if normalized:
                return normalized, m.group(0)
    return None, None


# --------------------------------------------------
# INDEX
# --------------------------------------------------


class MeetingIndex:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._meetings: Dict[str, Meeting] = {}
        self._aliases: Dict[str, str] = {}
        self.load_aliases()
        self.rebuild()

    # --- aliases --------------------------------------------------

    def load_aliases(self) -> None:
        if not ALIASES_FILE.exists():
            self._aliases = {}
            return
        try:
            data = yaml.safe_load(ALIASES_FILE.read_text(encoding="utf-8")) or {}
            raw = data.get("aliases", {}) or {}
            self._aliases = {
                str(k).strip().lower(): str(v).strip()
                for k, v in raw.items()
                if v
            }
        except Exception as e:
            print(f"[index] Failed to load aliases: {e}")
            self._aliases = {}

    def canonical(self, raw_group: str) -> str:
        if not raw_group:
            return "Unknown"
        return self._aliases.get(raw_group.strip().lower(), raw_group.strip())

    # --- rebuild --------------------------------------------------

    def rebuild(self) -> None:
        with self._lock:
            self.load_aliases()
            meetings: Dict[str, Meeting] = {}
            if MEETINGS_DIR.exists():
                for path in sorted(MEETINGS_DIR.glob("*.md")):
                    try:
                        m = self._parse(path)
                        meetings[m.id] = m
                    except Exception as e:
                        print(f"[index] Skipping {path.name}: {e}")
            self._meetings = meetings
            print(f"[index] Loaded {len(meetings)} meetings")

    def _parse(self, path: Path) -> Meeting:
        raw = path.read_text(encoding="utf-8")
        post = frontmatter.loads(raw)
        meta = post.metadata or {}

        def s(key: str, default: str = "") -> str:
            val = meta.get(key, default)
            if val is None:
                return default
            return str(val).strip()

        def sl(key: str) -> List[str]:
            val = meta.get(key, []) or []
            if isinstance(val, str):
                return [val.strip()] if val.strip() else []
            return [str(x).strip() for x in val if str(x).strip()]

        raw_group = s("group") or path.stem.split(" - ", 1)[-1]
        date_str = s("date") or self._date_from_filename(path)
        body_md = post.content or ""
        body_html = md_lib.markdown(
            body_md,
            extensions=["fenced_code", "tables", "sane_lists", "nl2br"],
        )
        id_ = hashlib.sha1(path.name.encode("utf-8")).hexdigest()[:16]

        return Meeting(
            id=id_,
            filename=path.name,
            path=str(path),
            date=date_str or None,
            raw_group=raw_group,
            canonical_group=self.canonical(raw_group),
            topic=s("topic"),
            purpose=sl("purpose"),
            outcome=s("outcome"),
            deadline=s("deadline"),
            attendees=s("attendees"),
            action_items_open=sl("action_items_open"),
            action_items_done=sl("action_items_done"),
            reminders_open=sl("reminders_open"),
            reminders_done=sl("reminders_done"),
            body=body_md,
            body_html=body_html,
            mtime=path.stat().st_mtime,
        )

    @staticmethod
    def _date_from_filename(path: Path) -> str:
        m = re.match(r"(\d{4}-\d{2}-\d{2})", path.stem)
        return m.group(1) if m else ""

    # --- query ----------------------------------------------------

    def all(self) -> List[Meeting]:
        with self._lock:
            return list(self._meetings.values())

    def get(self, id_: str) -> Optional[Meeting]:
        with self._lock:
            return self._meetings.get(id_)

    def find_by_filename(self, filename: str) -> Optional[Meeting]:
        with self._lock:
            for m in self._meetings.values():
                if m.filename == filename:
                    return m
        return None

    def facets(self) -> Dict[str, List[str]]:
        groups, purposes, attendees = set(), set(), set()
        raw_groups_seen: Dict[str, str] = {}
        for m in self.all():
            groups.add(m.canonical_group)
            raw_groups_seen.setdefault(m.raw_group, m.canonical_group)
            for p in m.purpose:
                purposes.add(p)
            if m.attendees:
                for a in re.split(r"[;,]", m.attendees):
                    a = a.strip()
                    if a:
                        attendees.add(a)
        unaliased = [
            raw for raw, canon in raw_groups_seen.items()
            if raw.strip().lower() not in self._aliases and raw == canon
        ]
        return {
            "groups": sorted(groups, key=str.casefold),
            "purposes": sorted(purposes, key=str.casefold),
            "attendees": sorted(attendees, key=str.casefold),
            "unaliased_raw_groups": sorted(unaliased, key=str.casefold),
        }

    def groups_summary(self) -> List[Dict[str, Any]]:
        by_group: Dict[str, List[Meeting]] = {}
        for m in self.all():
            by_group.setdefault(m.canonical_group, []).append(m)
        out = []
        for group, meetings in by_group.items():
            dates = [m.date for m in meetings if m.date]
            last = max(dates) if dates else None
            raw_variants = sorted(
                {m.raw_group for m in meetings if m.raw_group != group},
                key=str.casefold,
            )
            out.append({
                "group": group,
                "meeting_count": len(meetings),
                "last_contact": last,
                "open_action_items": sum(len(m.action_items_open) for m in meetings),
                "open_reminders": sum(len(m.reminders_open) for m in meetings),
                "raw_variants": raw_variants,
            })
        out.sort(key=lambda x: (x["last_contact"] or ""), reverse=True)
        return out

    # --- tasks ----------------------------------------------------

    def all_tasks(self, include_done: bool = False) -> List[Task]:
        tasks: List[Task] = []
        today = date_cls.today().isoformat()
        bb = load_backburner()
        for m in self.all():
            year = _year_from_date(m.date)
            for t in m.action_items_open:
                tasks.append(_build_task(
                    text=t, done=False, type_="action", section="action_items",
                    source_filename=m.filename, meeting=m,
                    context_year=year, today=today, backburner_ids=bb,
                ))
            for t in m.reminders_open:
                tasks.append(_build_task(
                    text=t, done=False, type_="reminder", section="reminders",
                    source_filename=m.filename, meeting=m,
                    context_year=year, today=today, backburner_ids=bb,
                ))
            if include_done:
                for t in m.action_items_done:
                    tasks.append(_build_task(
                        text=t, done=True, type_="action", section="action_items",
                        source_filename=m.filename, meeting=m,
                        context_year=year, today=today, backburner_ids=bb,
                    ))
                for t in m.reminders_done:
                    tasks.append(_build_task(
                        text=t, done=True, type_="reminder", section="reminders",
                        source_filename=m.filename, meeting=m,
                        context_year=year, today=today, backburner_ids=bb,
                    ))
        # free-form tasks
        for t in parse_free_tasks(TASKS_FILE, today=today, backburner_ids=bb):
            if t.done and not include_done:
                continue
            tasks.append(t)
        return tasks


def _year_from_date(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    m = re.match(r"(\d{4})", s)
    return int(m.group(1)) if m else None


def _task_id(*parts: str) -> str:
    blob = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()[:16]


def _build_task(
    *,
    text: str,
    done: bool,
    type_: str,
    section: str,
    source_filename: str,
    meeting: Meeting,
    context_year: Optional[int],
    today: str,
    backburner_ids: set,
) -> Task:
    deadline, deadline_raw = extract_deadline(text, context_year=context_year)
    tid = _task_id(source_filename, section, text)
    return Task(
        id=tid,
        text=text,
        type=type_,
        done=done,
        backburner=tid in backburner_ids,
        source_filename=source_filename,
        section=section,
        meeting_id=meeting.id,
        group=meeting.canonical_group,
        source_date=meeting.date,
        deadline=deadline,
        deadline_raw=deadline_raw,
        overdue=bool(deadline and not done and deadline < today),
    )


# --------------------------------------------------
# FREE-FORM TASKS (tasks.md)
# --------------------------------------------------


FREE_TASK_RE = re.compile(r"^(\s*)[-*+]\s*\[(?P<state>[ xX])\]\s*(?P<text>.+?)\s*$")
FREE_GROUP_RE = re.compile(
    r"@group:\s*(.+?)(?=\s+@\w+:|\s+(?:due|deadline|by)\b|\s*$)",
    re.IGNORECASE,
)


def _ensure_tasks_file() -> None:
    if not TASKS_FILE.exists():
        TASKS_FILE.write_text(
            "# Free-form tasks\n\n"
            "Add tasks here or via the dashboard. Format: `- [ ] your task`.\n"
            "Tip: include `@group:Name` and/or a date like `due 2026-05-01` and the dashboard picks them up.\n\n",
            encoding="utf-8",
        )


def parse_free_tasks(path: Path, today: str, backburner_ids: set) -> List[Task]:
    if not path.exists():
        return []
    tasks: List[Task] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = FREE_TASK_RE.match(raw)
        if not m:
            continue
        text = m.group("text").strip()
        done = m.group("state").strip().lower() == "x"
        deadline, deadline_raw = extract_deadline(text, context_year=datetime.now().year)
        group = None
        gm = FREE_GROUP_RE.search(text)
        if gm:
            group = gm.group(1).strip().rstrip(".,;")
        tid = _task_id(path.name, "free", text)
        tasks.append(Task(
            id=tid,
            text=text,
            type="free",
            done=done,
            backburner=tid in backburner_ids,
            source_filename=path.name,
            section="free",
            meeting_id=None,
            group=group,
            source_date=None,
            deadline=deadline,
            deadline_raw=deadline_raw,
            overdue=bool(deadline and not done and deadline < today),
        ))
    return tasks


# --------------------------------------------------
# WRITE-BACK
# --------------------------------------------------


SECTION_HEADER_RE = {
    "reminders": re.compile(r"^\s*\*{0,2}Reminders/Important:\*{0,2}\s*$", re.IGNORECASE),
    "action_items": re.compile(r"^\s*\*{0,2}Action Items:\*{0,2}\s*$", re.IGNORECASE),
}
ANY_SECTION_RE = re.compile(r"^\s*\*{0,2}([A-Za-z0-9 /\.\-]+):\*{0,2}\s*$")
TASK_LINE_RE = re.compile(r"^(?P<indent>\s*)(?P<bullet>[-*+])\s*\[(?P<state>[ xX])\](?P<rest>\s*.+?)\s*$")


def _toggle_line_in_section(lines: List[str], section: str, text: str, target_done: bool) -> bool:
    """Find the task line with matching text in the given section and flip its state.
    Returns True if a line was changed.
    """
    in_section = False
    for i, line in enumerate(lines):
        # section entry/exit
        sec_re = SECTION_HEADER_RE.get(section)
        if sec_re and sec_re.match(line):
            in_section = True
            continue
        if in_section and ANY_SECTION_RE.match(line):
            # some other section header → stop
            if not (sec_re and sec_re.match(line)):
                in_section = False
                continue

        if not in_section:
            continue

        m = TASK_LINE_RE.match(line)
        if not m:
            continue
        line_text = m.group("rest").strip()
        if line_text != text:
            continue
        is_done = m.group("state").strip().lower() == "x"
        if is_done == target_done:
            return True  # already in desired state
        new_state = "x" if target_done else " "
        lines[i] = f"{m.group('indent')}{m.group('bullet')} [{new_state}]{m.group('rest')}"
        return True
    return False


def _rebuild_yaml_for_meeting(path: Path) -> None:
    """Re-extract task lists from the body and rewrite the YAML front matter."""
    raw = path.read_text(encoding="utf-8")
    post = frontmatter.loads(raw)
    body_lines = (post.content or "").splitlines()

    # Re-derive the task lists from the body
    if extract_fields_and_tasks is not None:
        _, _, r_open, r_done, a_open, a_done = extract_fields_and_tasks(body_lines)
    else:
        r_open, r_done, a_open, a_done = _fallback_extract(body_lines)

    meta = dict(post.metadata or {})
    # Preserve existing fields, update task-related ones
    for key in ("reminders_open", "reminders_done", "action_items_open", "action_items_done"):
        meta.pop(key, None)
    if r_open: meta["reminders_open"] = r_open
    if r_done: meta["reminders_done"] = r_done
    if a_open: meta["action_items_open"] = a_open
    if a_done: meta["action_items_done"] = a_done
    meta["open_reminders_count"] = len(r_open)
    meta["open_action_items_count"] = len(a_open)

    # Reassemble: YAML front matter + body
    yaml_block = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    final = f"---\n{yaml_block}\n---\n\n" + (post.content or "")
    if not final.endswith("\n"):
        final += "\n"
    path.write_text(final, encoding="utf-8")


def _fallback_extract(lines: List[str]):
    """Minimal re-implementation if the upstream parser can't be imported."""
    r_open, r_done, a_open, a_done = [], [], [], []
    in_r = in_a = False
    for line in lines:
        if SECTION_HEADER_RE["reminders"].match(line):
            in_r, in_a = True, False; continue
        if SECTION_HEADER_RE["action_items"].match(line):
            in_a, in_r = True, False; continue
        if (in_r or in_a) and ANY_SECTION_RE.match(line):
            in_r = in_a = False
        m = TASK_LINE_RE.match(line)
        if not m:
            continue
        text = m.group("rest").strip()
        done = m.group("state").strip().lower() == "x"
        if in_r:
            (r_done if done else r_open).append(text)
        elif in_a:
            (a_done if done else a_open).append(text)
    return r_open, r_done, a_open, a_done


def toggle_meeting_task(filename: str, section: str, text: str, target_done: bool) -> bool:
    if section not in ("reminders", "action_items"):
        return False
    path = MEETINGS_DIR / filename
    if not path.is_file():
        return False
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    if not _toggle_line_in_section(lines, section, text, target_done):
        return False
    path.write_text("\n".join(lines) + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
    _rebuild_yaml_for_meeting(path)
    return True


def toggle_free_task(text: str, target_done: bool) -> bool:
    _ensure_tasks_file()
    raw = TASKS_FILE.read_text(encoding="utf-8")
    lines = raw.splitlines()
    changed = False
    for i, line in enumerate(lines):
        m = TASK_LINE_RE.match(line)
        if not m:
            continue
        if m.group("rest").strip() != text:
            continue
        is_done = m.group("state").strip().lower() == "x"
        if is_done == target_done:
            return True
        new_state = "x" if target_done else " "
        lines[i] = f"{m.group('indent')}{m.group('bullet')} [{new_state}]{m.group('rest')}"
        changed = True
        break
    if not changed:
        return False
    TASKS_FILE.write_text("\n".join(lines) + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
    return True


def delete_free_task(text: str) -> bool:
    _ensure_tasks_file()
    raw = TASKS_FILE.read_text(encoding="utf-8")
    lines = raw.splitlines()
    new_lines = []
    found = False
    for line in lines:
        m = TASK_LINE_RE.match(line)
        if not found and m and m.group("rest").strip() == text:
            found = True
            continue
        new_lines.append(line)
    if not found:
        return False
    TASKS_FILE.write_text("\n".join(new_lines) + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
    return True


def delete_meeting_task(filename: str, section: str, text: str) -> bool:
    if section not in ("reminders", "action_items"):
        return False
    path = MEETINGS_DIR / filename
    if not path.is_file():
        return False
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    sec_re = SECTION_HEADER_RE.get(section)
    in_section = False
    found = False
    new_lines = []
    for line in lines:
        if sec_re and sec_re.match(line):
            in_section = True
            new_lines.append(line)
            continue
        if in_section and ANY_SECTION_RE.match(line) and not (sec_re and sec_re.match(line)):
            in_section = False
        if in_section and not found:
            m = TASK_LINE_RE.match(line)
            if m and m.group("rest").strip() == text:
                found = True
                continue
        new_lines.append(line)
    if not found:
        return False
    path.write_text("\n".join(new_lines) + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
    _rebuild_yaml_for_meeting(path)
    return True


def edit_free_task(old_text: str, new_text: str) -> bool:
    _ensure_tasks_file()
    raw = TASKS_FILE.read_text(encoding="utf-8")
    lines = raw.splitlines()
    changed = False
    for i, line in enumerate(lines):
        m = TASK_LINE_RE.match(line)
        if not m or m.group("rest").strip() != old_text:
            continue
        lines[i] = f"{m.group('indent')}{m.group('bullet')} [{m.group('state')}] {new_text}"
        changed = True
        break
    if not changed:
        return False
    TASKS_FILE.write_text("\n".join(lines) + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
    return True


def edit_meeting_task(filename: str, section: str, old_text: str, new_text: str) -> bool:
    if section not in ("reminders", "action_items"):
        return False
    path = MEETINGS_DIR / filename
    if not path.is_file():
        return False
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    sec_re = SECTION_HEADER_RE.get(section)
    in_section = False
    changed = False
    for i, line in enumerate(lines):
        if sec_re and sec_re.match(line):
            in_section = True
            continue
        if in_section and ANY_SECTION_RE.match(line) and not (sec_re and sec_re.match(line)):
            in_section = False
        if in_section and not changed:
            m = TASK_LINE_RE.match(line)
            if m and m.group("rest").strip() == old_text:
                lines[i] = f"{m.group('indent')}{m.group('bullet')} [{m.group('state')}] {new_text}"
                changed = True
    if not changed:
        return False
    path.write_text("\n".join(lines) + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
    _rebuild_yaml_for_meeting(path)
    return True


def add_free_task(text: str, group: Optional[str] = None, deadline: Optional[str] = None) -> str:
    """Append a new free-form task to tasks.md. Returns the final line text."""
    _ensure_tasks_file()
    raw = TASKS_FILE.read_text(encoding="utf-8")
    clean = (text or "").strip()
    if not clean:
        raise ValueError("Task text required")
    tags = []
    if group:
        tags.append(f"@group:{group.strip()}")
    if deadline:
        tags.append(f"due {deadline.strip()}")
    full = clean + ((" " + " ".join(tags)) if tags else "")
    line = f"- [ ] {full}"
    if not raw.endswith("\n"):
        raw += "\n"
    raw += line + "\n"
    TASKS_FILE.write_text(raw, encoding="utf-8")
    return full


# --------------------------------------------------
# FLASK APP
# --------------------------------------------------

app = Flask(__name__, template_folder="templates", static_folder="static")
index = MeetingIndex()


# File watcher for live reload
def _start_watcher() -> None:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except Exception:
        return

    class Handler(FileSystemEventHandler):
        def _maybe_rebuild(self, event):
            try:
                p = Path(event.src_path)
                if p.suffix.lower() in (".md", ".yaml", ".yml"):
                    index.rebuild()
            except Exception as e:
                print(f"[watcher] rebuild error: {e}")

        def on_modified(self, event): self._maybe_rebuild(event)
        def on_created(self, event): self._maybe_rebuild(event)
        def on_deleted(self, event): self._maybe_rebuild(event)
        def on_moved(self, event): self._maybe_rebuild(event)

    obs = Observer()
    obs.schedule(Handler(), str(MEETINGS_DIR), recursive=False)
    obs.schedule(Handler(), str(DASHBOARD_DIR), recursive=False)
    obs.schedule(Handler(), str(NOTES_ROOT), recursive=False)  # tasks.md
    obs.daemon = True
    obs.start()


_start_watcher()


# --------------------------------------------------
# HELPERS FOR FILTERING MEETINGS
# --------------------------------------------------


def _date_in_range(d: Optional[str], start: Optional[str], end: Optional[str]) -> bool:
    if not start and not end:
        return True
    if not d:
        return False
    try:
        dt = datetime.strptime(d, "%Y-%m-%d").date()
    except Exception:
        return False
    if start:
        try:
            if dt < datetime.strptime(start, "%Y-%m-%d").date():
                return False
        except Exception: pass
    if end:
        try:
            if dt > datetime.strptime(end, "%Y-%m-%d").date():
                return False
        except Exception: pass
    return True


def _matches_query(m: Meeting, q: str) -> bool:
    if not q:
        return True
    q = q.lower()
    return any(q in (h or "").lower() for h in (
        m.raw_group, m.canonical_group, m.topic, m.outcome, m.deadline,
        m.attendees, m.filename, m.body, " ".join(m.purpose),
        " ".join(m.action_items_open + m.action_items_done),
        " ".join(m.reminders_open + m.reminders_done),
    ))


def filter_meetings(meetings, *, q="", group="", purpose="", attendee="",
                    date_from="", date_to="", has_open_tasks=False):
    out = []
    for m in meetings:
        if group and m.canonical_group != group: continue
        if purpose and purpose not in m.purpose: continue
        if attendee and attendee.lower() not in (m.attendees or "").lower(): continue
        if not _date_in_range(m.date, date_from or None, date_to or None): continue
        if has_open_tasks and not (m.action_items_open or m.reminders_open): continue
        if not _matches_query(m, q): continue
        out.append(m)
    out.sort(key=lambda x: (x.date or ""), reverse=True)
    return out


# --------------------------------------------------
# ROUTES
# --------------------------------------------------


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/meetings")
def api_meetings():
    a = request.args
    results = filter_meetings(
        index.all(),
        q=a.get("q", ""), group=a.get("group", ""), purpose=a.get("purpose", ""),
        attendee=a.get("attendee", ""), date_from=a.get("date_from", ""),
        date_to=a.get("date_to", ""),
        has_open_tasks=a.get("has_open_tasks", "").lower() in ("1", "true", "yes"),
    )
    return jsonify({"count": len(results), "meetings": [m.summary() for m in results]})


@app.route("/api/meetings/<mid>")
def api_meeting(mid: str):
    m = index.get(mid)
    if not m: abort(404)
    return jsonify(m.full())


@app.route("/api/groups")
def api_groups():
    return jsonify(index.groups_summary())


@app.route("/api/facets")
def api_facets():
    return jsonify(index.facets())


@app.route("/api/reload", methods=["POST"])
def api_reload():
    index.rebuild()
    return jsonify({"ok": True, "count": len(index.all())})


# ---- Tasks ----

@app.route("/api/tasks")
def api_tasks():
    a = request.args
    # Status: open = open & not backburner; done = done & not backburner;
    # backburner = on backburner (any done state); all = everything.
    status = a.get("status", "open")
    type_filter = a.get("type", "")
    group_filter = a.get("group", "")
    overdue_only = a.get("overdue", "").lower() in ("1", "true", "yes")
    q = a.get("q", "").lower()

    tasks = index.all_tasks(include_done=True)

    def passes_status(t: Task) -> bool:
        if status == "open": return not t.done and not t.backburner
        if status == "done": return t.done and not t.backburner
        if status == "backburner": return t.backburner
        return True  # all

    # Apply all filters EXCEPT group, to compute the group facet
    pre_group = []
    for t in tasks:
        if not passes_status(t): continue
        if type_filter and t.type != type_filter: continue
        if overdue_only and not t.overdue: continue
        if q and q not in t.text.lower() and q not in (t.group or "").lower(): continue
        pre_group.append(t)

    # Group facet: distinct groups among status-filtered tasks (skip empty groups)
    groups_in_scope = sorted(
        {t.group for t in pre_group if t.group},
        key=str.casefold,
    )

    # Now apply group filter
    out = []
    for t in pre_group:
        if group_filter and (t.group or "") != group_filter: continue
        out.append(t.as_dict())

    def sort_key(t):
        return (
            0 if t["overdue"] else 1,
            t["deadline"] or "9999-99-99",
            -(int((t["source_date"] or "0000-00-00").replace("-", "")) if t["source_date"] else 0),
        )
    out.sort(key=sort_key)
    return jsonify({"count": len(out), "tasks": out, "groups_in_scope": groups_in_scope})


@app.route("/api/tasks/backburner", methods=["POST"])
def api_backburner_task():
    data = request.get_json(force=True, silent=True) or {}
    task_id = (data.get("id") or "").strip()
    on = bool(data.get("backburner", True))
    if not task_id:
        return jsonify({"ok": False, "error": "id required"}), 400
    toggle_backburner(task_id, on)
    return jsonify({"ok": True})


@app.route("/api/tasks/toggle", methods=["POST"])
def api_toggle_task():
    data = request.get_json(force=True, silent=True) or {}
    section = data.get("section", "")
    filename = data.get("source_filename", "")
    text = data.get("text", "")
    done = bool(data.get("done", True))
    if not text or not filename:
        return jsonify({"ok": False, "error": "source_filename and text required"}), 400

    if section == "free":
        ok = toggle_free_task(text, target_done=done)
    elif section in ("reminders", "action_items"):
        ok = toggle_meeting_task(filename, section, text, target_done=done)
    else:
        return jsonify({"ok": False, "error": "invalid section"}), 400

    if not ok:
        return jsonify({"ok": False, "error": "task not found"}), 404

    # Log the completion event (for the dashboard sparkline)
    log_completion(
        task_id=_task_id(filename, section, text),
        text=text, section=section, filename=filename, done=done,
    )

    index.rebuild()
    return jsonify({"ok": True})


@app.route("/api/stats")
def api_stats():
    """Aggregated stats for the Home dashboard."""
    today = date_cls.today()
    today_iso = today.isoformat()
    horizon_days = 7  # upcoming deadlines window

    all_tasks = index.all_tasks(include_done=True)
    open_tasks = [t for t in all_tasks if not t.done and not t.backburner]
    done_tasks = [t for t in all_tasks if t.done and not t.backburner]

    overdue_open = [t for t in open_tasks if t.overdue]
    due_today = [t for t in open_tasks if t.deadline == today_iso]

    # Upcoming deadlines — next `horizon_days` days including today
    window_dates = [today + timedelta(days=i) for i in range(horizon_days)]
    deadlines_by_day = []
    for d in window_dates:
        iso = d.isoformat()
        count = sum(1 for t in open_tasks if t.deadline == iso)
        deadlines_by_day.append({
            "date": iso,
            "day": d.day,
            "dow": d.strftime("%a").upper(),
            "is_today": iso == today_iso,
            "count": count,
        })

    # Most overdue (by days overdue, descending)
    def days_overdue(t):
        try:
            return (today - datetime.strptime(t.deadline, "%Y-%m-%d").date()).days
        except Exception:
            return 0
    overdue_sorted = sorted(overdue_open, key=days_overdue, reverse=True)[:5]
    overdue_top = [{
        "id": t.id,
        "text": t.text,
        "group": t.group,
        "deadline": t.deadline,
        "days_overdue": days_overdue(t),
    } for t in overdue_sorted]

    # By-group breakdown (open tasks only, excluding None group)
    group_counts: Dict[str, int] = {}
    for t in open_tasks:
        if t.group:
            group_counts[t.group] = group_counts.get(t.group, 0) + 1
    by_group = sorted(
        [{"group": g, "count": c} for g, c in group_counts.items()],
        key=lambda x: x["count"], reverse=True,
    )[:8]

    # Completion rate over last 30 days (done-and-not-backburner vs total)
    total_tasks = len(all_tasks) - sum(1 for t in all_tasks if t.backburner)
    pct_complete = round((len(done_tasks) / total_tasks) * 100) if total_tasks else 0

    # Completions per day (from the jsonl log)
    per_day = completions_per_day(days=30)
    total_completions = sum(x["count"] for x in per_day)

    # Most recent meeting
    recent = None
    meetings_with_date = [m for m in index.all() if m.date]
    if meetings_with_date:
        m = sorted(meetings_with_date, key=lambda x: x.date, reverse=True)[0]
        recent = {
            "id": m.id,
            "group": m.canonical_group,
            "topic": m.topic,
            "date": m.date,
            "open_actions": len(m.action_items_open),
            "open_reminders": len(m.reminders_open),
        }

    return jsonify({
        "today": today_iso,
        "open_count": len(open_tasks),
        "overdue_count": len(overdue_open),
        "due_today_count": len(due_today),
        "done_count": len(done_tasks),
        "total_tasks": total_tasks,
        "pct_complete": pct_complete,
        "deadlines": deadlines_by_day,
        "overdue_top": overdue_top,
        "by_group": by_group,
        "completions_per_day": per_day,
        "completions_30d": total_completions,
        "recent_meeting": recent,
        "meetings_total": len(index.all()),
    })


@app.route("/api/tasks/delete", methods=["POST"])
def api_delete_task():
    data = request.get_json(force=True, silent=True) or {}
    section = data.get("section", "")
    filename = data.get("source_filename", "")
    text = data.get("text", "")
    if not text or not filename:
        return jsonify({"ok": False, "error": "source_filename and text required"}), 400
    if section == "free":
        ok = delete_free_task(text)
    elif section in ("reminders", "action_items"):
        ok = delete_meeting_task(filename, section, text)
    else:
        return jsonify({"ok": False, "error": "invalid section"}), 400
    if not ok:
        return jsonify({"ok": False, "error": "task not found"}), 404
    index.rebuild()
    return jsonify({"ok": True})


@app.route("/api/tasks/edit", methods=["POST"])
def api_edit_task():
    data = request.get_json(force=True, silent=True) or {}
    section = data.get("section", "")
    filename = data.get("source_filename", "")
    old_text = (data.get("old_text") or "").strip()
    new_text = (data.get("new_text") or "").strip()
    if not old_text or not new_text or not filename:
        return jsonify({"ok": False, "error": "source_filename, old_text, and new_text required"}), 400
    if section == "free":
        ok = edit_free_task(old_text, new_text)
    elif section in ("reminders", "action_items"):
        ok = edit_meeting_task(filename, section, old_text, new_text)
    else:
        return jsonify({"ok": False, "error": "invalid section"}), 400
    if not ok:
        return jsonify({"ok": False, "error": "task not found"}), 404
    index.rebuild()
    return jsonify({"ok": True})


@app.route("/api/import", methods=["POST"])
def api_import_notes():
    import io, contextlib
    uploaded = request.files.getlist("files")
    if not uploaded:
        return jsonify({"ok": False, "error": "No files uploaded"}), 400
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for f in uploaded:
        fname = Path(f.filename).name
        if not fname.endswith(".md"):
            results.append({"filename": fname, "ok": False, "error": "Not a .md file"})
            continue
        dest = INBOX_DIR / fname
        f.save(str(dest))
        if _pm_process_file is None:
            results.append({"filename": fname, "ok": False, "error": "Processor not available"})
            continue
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                _pm_process_file(dest, dry_run=False)
            output = buf.getvalue()
            warnings = [l.replace("⚠️  WARNING: ", "").strip()
                        for l in output.splitlines() if "WARNING" in l]
            results.append({"filename": fname, "ok": True, "warnings": warnings})
        except Exception as e:
            results.append({"filename": fname, "ok": False, "error": str(e)})
    index.rebuild()
    ok_count = sum(1 for r in results if r.get("ok"))
    return jsonify({"ok": True, "processed": ok_count, "total": len(results), "results": results})


@app.route("/api/tasks/add", methods=["POST"])
def api_add_task():
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    group = (data.get("group") or "").strip() or None
    deadline = (data.get("deadline") or "").strip() or None
    if not text:
        return jsonify({"ok": False, "error": "text required"}), 400
    full = add_free_task(text, group=group, deadline=deadline)
    index.rebuild()
    return jsonify({"ok": True, "text": full})


# --------------------------------------------------
# ENTRY
# --------------------------------------------------


if __name__ == "__main__":
    _ensure_tasks_file()
    print(f"Notes dashboard serving {len(index.all())} meetings from {MEETINGS_DIR}")
    print(f"Free-form tasks file: {TASKS_FILE}")
    print(f"Open http://{HOST}:{PORT}/ in your browser")
    app.run(host=HOST, port=PORT, debug=False)
