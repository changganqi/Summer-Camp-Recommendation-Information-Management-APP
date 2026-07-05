# -*- coding: utf-8 -*-
"""夏令营日程助手：手动录入 + 日历展示 + AI 链接识别。"""

from __future__ import annotations

import calendar
import csv
import html
import json
import os
import re
import shutil
import sqlite3
import ssl
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as xml_escape
from license_keys import activate_license, validate_saved_license

try:
    import certifi
except Exception:  # pragma: no cover - certifi is optional in source mode.
    certifi = None

if getattr(sys, "frozen", False):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")


APP_NAME = "夏令营日程助手"
BASE_DIR = Path(__file__).resolve().parent


def resource_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", BASE_DIR)).joinpath(*parts)
    return BASE_DIR.joinpath(*parts)


def apply_app_icon(window: tk.Misc) -> None:
    icon_path = resource_path("assets", "app.ico")
    if icon_path.exists():
        try:
            window.iconbitmap(str(icon_path))
        except tk.TclError:
            pass


def resolve_app_data_dir() -> Path:
    candidates = [
        Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "SummerCampPlanner",
        BASE_DIR / "user_data",
    ]
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except Exception:
            continue
    return BASE_DIR


APP_DATA_DIR = resolve_app_data_dir()
DB_PATH = APP_DATA_DIR / "summer_camps.sqlite3"
SETTINGS_PATH = APP_DATA_DIR / "settings.json"

EDITABLE_FIELDS = [
    "school",
    "college",
    "registration_number",
    "notice_url",
    "signup_start",
    "signup_end",
    "signup_url",
    "result_date",
    "result_url",
    "camp_start",
    "camp_end",
    "camp_format",
    "camp_address",
    "advisor",
    "status",
    "priority",
    "project_type",
    "notes",
]

DATE_FIELDS = ["signup_start", "signup_end", "result_date", "camp_start", "camp_end"]
STATUS_OPTIONS = ["待确认", "已报名", "已入营", "放弃/落选"]
STATUS_ALIASES = {
    "待确认": "待确认",
    "待报名": "待确认",
    "未报名": "待确认",
    "已报名": "已报名",
    "入营待公布": "已报名",
    "待公布": "已报名",
    "已入营": "已入营",
    "已结束": "已入营",
    "未入营": "放弃/落选",
    "落选": "放弃/落选",
    "放弃": "放弃/落选",
    "放弃/落选": "放弃/落选",
}
STATUS_SORT_RANK = {"待确认": 0, "已报名": 0, "已入营": 0, "放弃/落选": 1}
PRIORITY_OPTIONS = ["普通", "关注"]
PROJECT_TYPE_OPTIONS = ["硕士", "直博"]
FORMAT_OPTIONS = ["待定", "线上", "线下", "线上或线下"]

FIELD_LABELS = {
    "school": "学校名",
    "college": "学院/项目",
    "registration_number": "报名号",
    "notice_url": "通知链接",
    "signup_start": "报名开始",
    "signup_end": "报名截止",
    "signup_url": "报名网址",
    "result_date": "公布时间",
    "result_url": "公布网址",
    "camp_start": "参营开始",
    "camp_end": "参营结束",
    "camp_format": "形式",
    "camp_address": "参营地址",
    "advisor": "意向导师",
    "status": "状态",
    "priority": "优先级",
    "project_type": "类型（硕士/直博）",
    "notes": "备注",
}

DEFAULT_SETTINGS = {
    "api_url": "",
    "model": "",
    "api_key": "",
    "timeout_seconds": 60,
}

EVENT_STYLE = {
    "pending_signup": ("待确认", "#854d0e", "#fef9c3"),
    "signup_deadline": ("报名截止", "#b91c1c", "#fee2e2"),
    "signup": ("报名", "#1d4ed8", "#dbeafe"),
    "result": ("公布", "#7c3aed", "#f3e8ff"),
    "camp": ("开营", "#15803d", "#dcfce7"),
}
EVENT_SORT_RANK = {"pending_signup": 0, "signup_deadline": 1, "result": 2, "signup": 3, "camp": 4}
TREE_EVENT_SORT_RANK = {"pending_signup": 0, "signup": 1, "result": 2, "camp": 3}
CAMP_FORMAT_EVENT_STYLE = {
    "offline": ("#c2410c", "#ffedd5"),
    "other": EVENT_STYLE["camp"][1:],
}
NOTE_FOCUS_MARKERS = ("【重点】", "【风险】", "【歧义】", "【需操作】", "【注意】")
PERSONAL_PROFILE_PATH = APP_DATA_DIR / "personal_profile.txt"
RICH_TEXT_PREFIX = "__SUMMER_RICH_TEXT_V1__\n"
RICH_BASE_TAGS = ("rt_bold", "rt_red", "rt_italic")
RICH_DEFAULT_SIZES = (9, 10, 12, 14, 16)
RICH_SIZE_TAGS = tuple(f"rt_size_{size}" for size in RICH_DEFAULT_SIZES)
RICH_STYLE_TAGS = RICH_BASE_TAGS + RICH_SIZE_TAGS
RICH_TOOL_FONT = ("Microsoft YaHei UI", 9, "bold")
RICH_PENDING_ATTR = "_summer_rich_pending_tags"
RICH_SELECTION_ATTR = "_summer_rich_last_selection"
RICH_ACTIVE_SELECTION_TAG = "rich_active_sel"
RICH_RENDER_PREFIX = "_rt_font_"


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def compact_status_text(text: str | None, limit: int = 52) -> str:
    cleaned = re.sub(r"\s+", " ", safe_text(text)).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)] + "..."


def promote_focus_notes(value: str) -> str:
    lines = safe_text(value).splitlines()
    focus_lines: list[str] = []
    other_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and any(marker in stripped for marker in NOTE_FOCUS_MARKERS):
            focus_lines.append(line)
        else:
            other_lines.append(line)
    if not focus_lines:
        return safe_text(value)
    while other_lines and not other_lines[0].strip():
        other_lines.pop(0)
    while other_lines and not other_lines[-1].strip():
        other_lines.pop()
    return "\n".join(focus_lines + ([""] if other_lines else []) + other_lines)


def format_notes_text(value: str) -> str:
    text = safe_text(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    # Fix AI output that sometimes breaks "1. 申请材料" into two lines.
    text = re.sub(r"(?m)^(\s*\d+[\.、）\)]\s*)\n\s*(?=\S)", lambda m: m.group(1).strip() + " ", text)
    text = re.sub(r"(?m)^(\s*[A-Za-z][\.、）\)]\s*)\n\s*(?=\S)", lambda m: m.group(1).strip() + " ", text)

    # Make dense semicolon-separated notes readable without touching URLs.
    text = re.sub(r"[；;]\s*(?=(?:\d+[\.、）\)]|[A-Za-z][\.、）\)]|【|报名|活动|参营|公布|审核|材料|联系方式|联系|地点|备注|申请))", "；\n", text)
    text = re.sub(r"(?<=[。；;])\s*(?=\d+[\.、）\)]\s*)", "\n", text)
    text = re.sub(r"(?m)^(\s*\d+[\.、）\)]\s*)\n\s*(?=\S)", lambda m: m.group(1).strip() + " ", text)
    text = re.sub(r"(?m)^(\s*[A-Za-z][\.、）\)]\s*)\n\s*(?=\S)", lambda m: m.group(1).strip() + " ", text)

    raw_lines = [line.strip() for line in text.splitlines()]
    lines: list[str] = []
    previous_was_blank = True
    for line in raw_lines:
        if not line:
            if not previous_was_blank:
                lines.append("")
            previous_was_blank = True
            continue
        is_numbered = bool(re.match(r"^(?:\d+[\.、）\)]|[A-Za-z][\.、）\)]|【[^】]+】)", line))
        if is_numbered and lines and lines[-1] != "":
            lines.append("")
        if re.match(r"^\d+[\.、）\)]", line):
            line = "  " + line
        lines.append(line)
        previous_was_blank = False

    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def normalize_notes_text(value: str) -> str:
    return format_notes_text(promote_focus_notes(value))


def is_rich_text(value: str) -> bool:
    return safe_text(value).startswith(RICH_TEXT_PREFIX)


def rich_plain_text(value: str) -> str:
    text = safe_text(value)
    if not is_rich_text(text):
        return text
    try:
        payload = json.loads(text[len(RICH_TEXT_PREFIX) :])
    except Exception:
        return text
    return safe_text(payload.get("text"))


def get_text_plain(text_widget: tk.Text) -> str:
    return text_widget.get("1.0", "end-1c")


def configure_rich_text_tags(text_widget: tk.Text) -> None:
    base_family = "Microsoft YaHei UI"
    text_widget.configure(font=(base_family, 10), exportselection=False)
    text_widget.tag_configure("rt_bold", font=(base_family, 10, "bold"))
    text_widget.tag_configure("rt_red", foreground="#dc2626")
    text_widget.tag_configure("rt_italic", font=(base_family, 10, "italic"))
    text_widget.tag_configure(RICH_ACTIVE_SELECTION_TAG, background="#0078d7", foreground="#ffffff")
    for size in RICH_DEFAULT_SIZES:
        configure_rich_size_tag(text_widget, size)
    setattr(text_widget, RICH_PENDING_ATTR, set())
    setattr(text_widget, RICH_SELECTION_ATTR, None)
    text_widget.bind("<KeyPress>", lambda event, widget=text_widget: apply_pending_rich_tags_on_keypress(widget, event), add="+")
    text_widget.bind("<ButtonPress-1>", lambda _event, widget=text_widget: clear_rich_cached_selection(widget), add="+")
    for event_name in ("<ButtonRelease-1>", "<KeyRelease>", "<<Selection>>"):
        text_widget.bind(event_name, lambda _event, widget=text_widget: remember_rich_selection(widget), add="+")


def rich_size_from_tag(tag: str) -> int | None:
    match = re.fullmatch(r"rt_size_(\d{1,2})", safe_text(tag))
    if not match:
        return None
    size = int(match.group(1))
    if 6 <= size <= 48:
        return size
    return None


def configure_rich_size_tag(text_widget: tk.Text, size: int) -> str:
    size = max(6, min(48, int(size)))
    tag = f"rt_size_{size}"
    text_widget.tag_configure(tag, font=("Microsoft YaHei UI", size))
    return tag


def rich_widget_style_tags(text_widget: tk.Text) -> list[str]:
    tags = list(RICH_BASE_TAGS)
    for tag in text_widget.tag_names():
        if rich_size_from_tag(safe_text(tag)) is not None and tag not in tags:
            tags.append(safe_text(tag))
    return tags


def refresh_rich_render_tags(text_widget: tk.Text) -> None:
    for tag in list(text_widget.tag_names()):
        if safe_text(tag).startswith(RICH_RENDER_PREFIX):
            text_widget.tag_delete(tag)
    text_length = len(get_text_plain(text_widget))
    if text_length <= 0:
        return
    for offset in range(text_length):
        index = f"1.0+{offset}c"
        next_index = f"1.0+{offset + 1}c"
        tags = {safe_text(tag) for tag in text_widget.tag_names(index)}
        size = 10
        for tag in tags:
            parsed_size = rich_size_from_tag(tag)
            if parsed_size is not None:
                size = parsed_size
                break
        weight = "bold" if "rt_bold" in tags else "normal"
        slant = "italic" if "rt_italic" in tags else "roman"
        render_tag = f"{RICH_RENDER_PREFIX}{size}_{weight}_{slant}"
        if render_tag not in text_widget.tag_names():
            text_widget.tag_configure(render_tag, font=("Microsoft YaHei UI", size, weight, slant))
        text_widget.tag_add(render_tag, index, next_index)
    for tag in list(text_widget.tag_names()):
        if safe_text(tag).startswith(RICH_RENDER_PREFIX):
            text_widget.tag_raise(tag)
    text_widget.tag_raise("rt_red")
    text_widget.tag_raise(RICH_ACTIVE_SELECTION_TAG)


def remember_rich_selection(text_widget: tk.Text) -> None:
    try:
        start = text_widget.index("sel.first")
        end = text_widget.index("sel.last")
    except tk.TclError:
        return
    if text_widget.compare(start, "<", end):
        setattr(text_widget, RICH_SELECTION_ATTR, (start, end))
        show_rich_cached_selection(text_widget)


def show_rich_cached_selection(text_widget: tk.Text) -> None:
    text_widget.tag_remove(RICH_ACTIVE_SELECTION_TAG, "1.0", "end")
    cached = getattr(text_widget, RICH_SELECTION_ATTR, None)
    if not cached:
        return
    try:
        start = text_widget.index(cached[0])
        end = text_widget.index(cached[1])
    except tk.TclError:
        return
    if text_widget.compare(start, "<", end):
        text_widget.tag_add(RICH_ACTIVE_SELECTION_TAG, start, end)
        text_widget.tag_raise(RICH_ACTIVE_SELECTION_TAG)


def clear_rich_cached_selection(text_widget: tk.Text) -> None:
    text_widget.tag_remove(RICH_ACTIVE_SELECTION_TAG, "1.0", "end")
    text_widget.tag_remove("sel", "1.0", "end")
    setattr(text_widget, RICH_SELECTION_ATTR, None)


def get_rich_selection_range(text_widget: tk.Text) -> tuple[str, str] | None:
    try:
        start = text_widget.index("sel.first")
        end = text_widget.index("sel.last")
        if text_widget.compare(start, "<", end):
            setattr(text_widget, RICH_SELECTION_ATTR, (start, end))
            return start, end
    except tk.TclError:
        pass
    cached = getattr(text_widget, RICH_SELECTION_ATTR, None)
    if not cached:
        return None
    try:
        start = text_widget.index(cached[0])
        end = text_widget.index(cached[1])
        if text_widget.compare(start, "<", end):
            return start, end
    except tk.TclError:
        return None
    return None


def pending_rich_tags(text_widget: tk.Text) -> set[str]:
    pending = getattr(text_widget, RICH_PENDING_ATTR, None)
    if not isinstance(pending, set):
        pending = set()
        setattr(text_widget, RICH_PENDING_ATTR, pending)
    return pending


def apply_pending_rich_tags_on_keypress(text_widget: tk.Text, event) -> None:
    if getattr(event, "keysym", "") == "Return":
        pending = pending_rich_tags(text_widget)
        for tag in list(pending):
            if rich_size_from_tag(tag) is not None:
                pending.discard(tag)
        return
    if getattr(event, "keysym", "") in {
        "BackSpace",
        "Delete",
        "Left",
        "Right",
        "Up",
        "Down",
        "Home",
        "End",
        "Prior",
        "Next",
        "Escape",
        "Tab",
    }:
        return
    if getattr(event, "state", 0) & 0x4:
        return
    char = getattr(event, "char", "")
    if not char or ord(char) < 32:
        return
    insert_index = text_widget.index("insert")

    def apply_tags(start_index: str = insert_index):
        try:
            end_index = text_widget.index(f"{start_index}+1c")
        except tk.TclError:
            return
        for tag in pending_rich_tags(text_widget):
            text_widget.tag_add(tag, start_index, end_index)
        refresh_rich_render_tags(text_widget)

    text_widget.after_idle(apply_tags)


def load_rich_text(text_widget: tk.Text, value: str, transform_plain=None) -> None:
    text_widget.delete("1.0", "end")
    text = safe_text(value)
    spans: list[dict] = []
    if is_rich_text(text):
        try:
            payload = json.loads(text[len(RICH_TEXT_PREFIX) :])
            text = safe_text(payload.get("text"))
            spans = payload.get("spans") if isinstance(payload.get("spans"), list) else []
        except Exception:
            text = rich_plain_text(value)
            spans = []
    elif transform_plain:
        text = transform_plain(text)
    text_widget.insert("1.0", text)
    for span in spans:
        try:
            tag = safe_text(span.get("tag"))
            start = int(span.get("start"))
            end = int(span.get("end"))
        except Exception:
            continue
        if rich_size_from_tag(tag) is not None:
            configure_rich_size_tag(text_widget, rich_size_from_tag(tag) or 10)
        if (tag in RICH_BASE_TAGS or rich_size_from_tag(tag) is not None) and 0 <= start < end <= len(text):
            text_widget.tag_add(tag, f"1.0+{start}c", f"1.0+{end}c")
    refresh_rich_render_tags(text_widget)


def dump_rich_text(text_widget: tk.Text, normalize_plain=None) -> str:
    text = get_text_plain(text_widget)
    if normalize_plain:
        normalized = normalize_plain(text)
        if normalized != text:
            text_widget.delete("1.0", "end")
            text_widget.insert("1.0", normalized)
            text = normalized
    spans: list[dict] = []
    for tag in rich_widget_style_tags(text_widget):
        ranges = text_widget.tag_ranges(tag)
        for index in range(0, len(ranges), 2):
            start_index = str(ranges[index])
            end_index = str(ranges[index + 1])
            try:
                start = int(text_widget.count("1.0", start_index, "chars")[0])
                end = int(text_widget.count("1.0", end_index, "chars")[0])
            except Exception:
                continue
            if start < end:
                spans.append({"tag": tag, "start": start, "end": end})
    if not spans:
        return text
    return RICH_TEXT_PREFIX + json.dumps({"text": text, "spans": spans}, ensure_ascii=False, separators=(",", ":"))


def toggle_text_tag(text_widget: tk.Text, tag: str, remove_tags: tuple[str, ...] = (), clear_selection: bool = True) -> None:
    selection = get_rich_selection_range(text_widget)
    if not selection:
        pending = pending_rich_tags(text_widget)
        for remove_tag in remove_tags:
            pending.discard(remove_tag)
        if tag in pending:
            pending.discard(tag)
        else:
            pending.add(tag)
        text_widget.focus_set()
        return
    start, end = selection
    for remove_tag in remove_tags:
        text_widget.tag_remove(remove_tag, start, end)
    if text_widget.tag_nextrange(tag, start, end):
        text_widget.tag_remove(tag, start, end)
        applied = False
    else:
        text_widget.tag_add(tag, start, end)
        applied = True
    pending = pending_rich_tags(text_widget)
    for remove_tag in remove_tags:
        pending.discard(remove_tag)
    if applied:
        pending.add(tag)
    else:
        pending.discard(tag)
    refresh_rich_render_tags(text_widget)
    if clear_selection:
        clear_rich_cached_selection(text_widget)
    else:
        show_rich_cached_selection(text_widget)
    text_widget.mark_set("insert", end)
    text_widget.focus_set()


def build_window_control_strip(parent: tk.Widget, controls: list[tuple[str, object, bool]]) -> tk.Frame:
    strip = tk.Frame(parent, bg="#f8fbff", highlightthickness=0, bd=0)

    def make_button(label: str, command, danger: bool = False) -> None:
        normal_bg = "#f8fbff"
        hover_bg = "#e5e7eb" if not danger else "#ef4444"
        fg = "#1f2937" if not danger else "#991b1b"
        hover_fg = "#1f2937" if not danger else "#ffffff"
        if label == "×":
            width = 2
            font = ("Segoe UI Symbol", 17, "bold")
            ipady = 0
        elif label == "□":
            width = 3
            font = ("Segoe UI Symbol", 10)
            ipady = 0
        elif len(label) > 1:
            width = 6
            font = ("Microsoft YaHei UI", 9, "bold")
            ipady = 3
        else:
            width = 4
            font = ("Segoe UI Symbol", 12)
            ipady = 2
        button = tk.Button(
            strip,
            text=label,
            width=width,
            relief="flat",
            bd=0,
            bg=normal_bg,
            activebackground=hover_bg,
            fg=fg,
            activeforeground=hover_fg,
            cursor="hand2",
            font=font,
            command=command,
        )
        button.pack(side="left", ipady=ipady)
        button.bind("<Enter>", lambda _event: button.configure(bg=hover_bg, fg=hover_fg))
        button.bind("<Leave>", lambda _event: button.configure(bg=normal_bg, fg=fg))

    for label, command, danger in controls:
        make_button(label, command, danger)
    return strip


def build_rich_toolbar(parent: tk.Widget, text_widget: tk.Text, expand_command=None, collapse_command=None) -> ttk.Frame:
    toolbar = ttk.Frame(parent, style="RichToolbar.TFrame")

    def make_tool_button(label: str, style: str, command) -> None:
        button = ttk.Button(toolbar, text=label, width=3, style=style, command=command)
        button.bind("<ButtonPress-1>", lambda _event: remember_rich_selection(text_widget), add="+")
        button.pack(side="left", padx=(0, 4 if label != "I" else 8))

    make_tool_button("B", "RichTool.TButton", lambda: toggle_text_tag(text_widget, "rt_bold"))
    make_tool_button("R", "RichRed.TButton", lambda: toggle_text_tag(text_widget, "rt_red"))
    make_tool_button("I", "RichTool.TButton", lambda: toggle_text_tag(text_widget, "rt_italic"))
    size_var = tk.StringVar(value="10")
    size_box = ttk.Combobox(toolbar, textvariable=size_var, values=[str(size) for size in RICH_DEFAULT_SIZES], width=4)
    size_box.pack(side="left")

    def apply_size(_event=None) -> None:
        size_text = safe_text(size_var.get()).strip()
        if not size_text:
            return
        try:
            size = int(float(size_text))
        except ValueError:
            size_var.set("10")
            return
        size = max(6, min(48, size))
        size_var.set(str(size))
        tag = configure_rich_size_tag(text_widget, size)
        size_tags = tuple(tag_name for tag_name in text_widget.tag_names() if rich_size_from_tag(safe_text(tag_name)) is not None)
        toggle_text_tag(text_widget, tag, size_tags, clear_selection=True)

    def remember_and_show(_event=None):
        remember_rich_selection(text_widget)
        show_rich_cached_selection(text_widget)

    size_box.bind("<Button-1>", remember_and_show, add="+")
    size_box.bind("<FocusIn>", remember_and_show, add="+")
    size_box.bind("<<ComboboxSelected>>", apply_size)
    size_box.bind("<Return>", apply_size)
    if expand_command:
        controls = build_window_control_strip(toolbar, [("放大", expand_command, False)])
        controls.pack(side="right", padx=(10, 0))
    if collapse_command:
        controls = build_window_control_strip(
            toolbar,
            [
                ("×", collapse_command, True),
            ],
        )
        controls.pack(side="right", padx=(10, 0))
    return toolbar


def parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def normalize_date(value: str | None, default_year: int | None = None) -> str:
    """接受 2026-07-03、2026年7月3日、7.3、7月3日 等常见写法。"""
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    default_year = default_year or date.today().year
    text = raw.replace("—", "-").replace("－", "-").replace("～", "-").replace("~", "-")
    text = re.sub(r"\s+", "", text)

    full_patterns = [
        r"(?P<y>20\d{2})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日?",
        r"(?P<y>20\d{2})[./-](?P<m>\d{1,2})[./-](?P<d>\d{1,2})",
    ]
    for pattern in full_patterns:
        match = re.search(pattern, text)
        if match:
            return date(int(match.group("y")), int(match.group("m")), int(match.group("d"))).isoformat()

    short_patterns = [
        r"(?P<m>\d{1,2})月(?P<d>\d{1,2})日?",
        r"(?P<m>\d{1,2})[./-](?P<d>\d{1,2})",
    ]
    for pattern in short_patterns:
        match = re.search(pattern, text)
        if match:
            return date(default_year, int(match.group("m")), int(match.group("d"))).isoformat()

    raise ValueError(f"无法识别日期：{raw}")


def extract_date_expression(value: str | None) -> str:
    text = safe_text(value).strip()
    if not text:
        return ""
    text = text.replace("，", ",").replace("。", ".").replace("；", ";")
    patterns = [
        r"20\d{2}年\d{1,2}月\d{1,2}日?(?:左右|前后|约|预计)?",
        r"20\d{2}[./-]\d{1,2}[./-]\d{1,2}",
        r"\d{1,2}月\d{1,2}日?(?:左右|前后|约|预计)?",
        r"\d{1,2}[./-]\d{1,2}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return text


def normalize_date_field_value(value: str | None, default_year: int | None = None) -> tuple[str, str]:
    raw = safe_text(value).strip()
    if not raw:
        return "", ""
    extracted = extract_date_expression(raw)
    normalized = normalize_date(extracted, default_year=default_year)
    return normalized, extracted


def split_date_range(value: str | None, default_year: int | None = None) -> tuple[str, str] | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    default_year = default_year or date.today().year

    full_iso = re.search(
        r"(?P<a>20\d{2}-\d{1,2}-\d{1,2})\s*(?:至|到|—|－|~|～|--)\s*"
        r"(?P<b>20\d{2}-\d{1,2}-\d{1,2})",
        raw,
    )
    if full_iso:
        return normalize_date(full_iso.group("a"), default_year), normalize_date(full_iso.group("b"), default_year)

    full_cn = re.search(
        r"(?P<y>20\d{2})年(?P<m1>\d{1,2})月(?P<d1>\d{1,2})日?\s*"
        r"(?:至|到|—|－|-|~|～)\s*(?:(?P<m2>\d{1,2})月)?(?P<d2>\d{1,2})日?",
        raw,
    )
    if full_cn:
        year = int(full_cn.group("y"))
        month_1 = int(full_cn.group("m1"))
        month_2 = int(full_cn.group("m2") or month_1)
        return date(year, month_1, int(full_cn.group("d1"))).isoformat(), date(
            year, month_2, int(full_cn.group("d2"))
        ).isoformat()

    short = re.search(
        r"(?P<m1>\d{1,2})(?:月|\.)\s*(?P<d1>\d{1,2})日?\s*"
        r"(?:至|到|—|－|-|~|～)\s*(?:(?P<m2>\d{1,2})(?:月|\.))?\s*(?P<d2>\d{1,2})日?",
        raw,
    )
    if short:
        month_1 = int(short.group("m1"))
        month_2 = int(short.group("m2") or month_1)
        return date(default_year, month_1, int(short.group("d1"))).isoformat(), date(
            default_year, month_2, int(short.group("d2"))
        ).isoformat()

    return None


def expand_date_ranges(data: dict, default_year: int | None = None) -> dict:
    default_year = default_year or date.today().year
    for start_field, end_field in [("signup_start", "signup_end"), ("camp_start", "camp_end")]:
        start_value = safe_text(data.get(start_field)).strip()
        end_value = safe_text(data.get(end_field)).strip()
        if start_value and not end_value:
            parsed = split_date_range(start_value, default_year)
            if parsed:
                data[start_field], data[end_field] = parsed
        elif end_value and not start_value:
            parsed = split_date_range(end_value, default_year)
            if parsed:
                data[start_field], data[end_field] = parsed
    return data


FUZZY_DATE_PATTERN = re.compile(r"上旬|中旬|下旬|另行通知|另行公布|待定|暂定|具体时间|拟定")
APPROX_DATE_PATTERN = re.compile(r"左右|约|预计|前后")


def format_date_cn(value: str | None) -> str:
    parsed = parse_iso_date(value)
    if not parsed:
        return ""
    return f"{parsed.month}.{parsed.day}"


def format_range(start: str | None, end: str | None) -> str:
    left = format_date_cn(start)
    right = format_date_cn(end)
    if left and right:
        return left if left == right else f"{left}-{right}"
    return left or right or ""


def safe_text(value: object) -> str:
    return "" if value is None else str(value)


def clean_xml_text(value: object) -> str:
    text = safe_text(value)
    return "".join(
        ch
        for ch in text
        if ch in "\t\n\r" or ord(ch) >= 32
    )


def xlsx_column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name or "A"


def xlsx_column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    index = 0
    for ch in letters:
        index = index * 26 + ord(ch) - 64
    return max(1, index)


def write_simple_xlsx(target: str, rows: list[list[object]], sheet_name: str = "夏令营日程") -> None:
    """Write a small XLSX workbook without third-party dependencies."""
    sheet_title = xml_escape(sheet_name[:31] or "Sheet1", {'"': "&quot;"})
    max_cols = max((len(row) for row in rows), default=1)
    widths: list[int] = []
    for col_idx in range(max_cols):
        width = 10
        for row in rows:
            if col_idx < len(row):
                width = max(width, min(42, len(clean_xml_text(row[col_idx])) + 2))
        widths.append(width)

    cols_xml = "".join(
        f'<col min="{idx}" max="{idx}" width="{width}" customWidth="1"/>'
        for idx, width in enumerate(widths, start=1)
    )
    row_xml_parts: list[str] = []
    for row_idx, row in enumerate(rows, start=1):
        cell_parts: list[str] = []
        for col_idx in range(1, max_cols + 1):
            value = clean_xml_text(row[col_idx - 1] if col_idx <= len(row) else "")
            cell_ref = f"{xlsx_column_name(col_idx)}{row_idx}"
            cell_parts.append(
                f'<c r="{cell_ref}" t="inlineStr"><is><t xml:space="preserve">'
                f"{xml_escape(value)}</t></is></c>"
            )
        row_xml_parts.append(f'<row r="{row_idx}">{"".join(cell_parts)}</row>')
    dimension = f"A1:{xlsx_column_name(max_cols)}{max(1, len(rows))}"
    worksheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<dimension ref="{dimension}"/>'
        f"<cols>{cols_xml}</cols>"
        f'<sheetData>{"".join(row_xml_parts)}</sheetData>'
        "</worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>'
        f'<sheet name="{sheet_title}" sheetId="1" r:id="rId1"/>'
        '</sheets>'
        '</workbook>'
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        '</Relationships>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>'
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Microsoft YaHei"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '</styleSheet>'
    )
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml)
        archive.writestr("_rels/.rels", rels_xml)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        archive.writestr("xl/styles.xml", styles_xml)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet_xml)


def read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"):
        strings.append("".join(node.text or "" for node in item.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
    return strings


def first_worksheet_path(archive: zipfile.ZipFile) -> str:
    names = set(archive.namelist())
    if "xl/workbook.xml" in names and "xl/_rels/workbook.xml.rels" in names:
        try:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            sheet = next(workbook.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"))
            rel_id = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            for rel in rels:
                if rel.attrib.get("Id") == rel_id:
                    target = rel.attrib.get("Target", "worksheets/sheet1.xml")
                    target = target.replace("\\", "/")
                    if target.startswith("/"):
                        return target.lstrip("/")
                    if target.startswith("xl/"):
                        return target
                    return "xl/" + target
        except Exception:
            pass
    return "xl/worksheets/sheet1.xml"


def read_xlsx_cell(cell: ET.Element, shared_strings: list[str]) -> str:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        inline = cell.find(f"{ns}is")
        if inline is None:
            return ""
        return "".join(node.text or "" for node in inline.iter(f"{ns}t"))
    value_node = cell.find(f"{ns}v")
    value = "" if value_node is None or value_node.text is None else value_node.text
    if cell_type == "s":
        try:
            return shared_strings[int(value)]
        except Exception:
            return ""
    if cell_type == "b":
        return "是" if value == "1" else "否"
    return value


def read_simple_xlsx(source: str) -> list[list[str]]:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(source, "r") as archive:
        shared_strings = read_shared_strings(archive)
        worksheet_path = first_worksheet_path(archive)
        if worksheet_path not in archive.namelist():
            raise RuntimeError("备份文件中没有找到工作表。")
        root = ET.fromstring(archive.read(worksheet_path))
        parsed_rows: list[list[str]] = []
        for row in root.iter(f"{ns}row"):
            values_by_col: dict[int, str] = {}
            for cell in row.iter(f"{ns}c"):
                cell_ref = cell.attrib.get("r", "")
                col_idx = xlsx_column_index(cell_ref) if cell_ref else len(values_by_col) + 1
                values_by_col[col_idx] = read_xlsx_cell(cell, shared_strings)
            max_col = max(values_by_col, default=0)
            parsed_rows.append([values_by_col.get(idx, "") for idx in range(1, max_col + 1)])
        return parsed_rows


def normalize_status(value: str | None) -> str:
    text = safe_text(value).strip()
    return STATUS_ALIASES.get(text, "待确认")


def status_sort_rank(value: str | None) -> int:
    return STATUS_SORT_RANK.get(normalize_status(value), 0)


def normalize_priority(value: str | None) -> str:
    text = safe_text(value).strip()
    if text in {"关注", "高", "重要"}:
        return "关注"
    return "普通"


def normalize_project_type(value: str | None) -> str:
    text = safe_text(value).strip()
    if "直博" in text or "博士" in text:
        return "直博"
    return "硕士"


def normalize_camp_format(value: str | None) -> str:
    text = safe_text(value).strip()
    if not text:
        return "待定"
    compact = re.sub(r"\s+", "", text)
    if compact in FORMAT_OPTIONS:
        return compact
    if ("线上" in compact and "线下" in compact) or any(
        marker in compact for marker in ("线上或线下", "线下或线上", "线上/线下", "线上、线下", "线上线下")
    ):
        return "线上或线下"
    if any(marker in compact for marker in ("待定", "暂定", "另行通知", "另行公布", "未定", "不确定")):
        return "待定"
    if any(marker in compact for marker in ("线下", "现场", "到校", "入校", "实地", "报到", "集中活动")):
        return "线下"
    if any(marker in compact for marker in ("线上", "网络", "视频会议", "腾讯会议", "钉钉", "飞书", "zoom", "直播", "云端")):
        return "线上"
    return "待定"


def is_focused(camp: dict) -> bool:
    return normalize_priority(camp.get("priority")) == "关注"


def priority_label(label: str, camp: dict) -> str:
    return f"{label}⭐" if is_focused(camp) else label


def may_require_offline(camp: dict) -> bool:
    text = normalize_camp_format(camp.get("camp_format"))
    return "线下" in text


def format_category(camp_format: str | None) -> str:
    text = normalize_camp_format(camp_format)
    return "offline" if "线下" in text else "other"


def load_settings() -> dict:
    settings = DEFAULT_SETTINGS.copy()
    if SETTINGS_PATH.exists():
        try:
            with SETTINGS_PATH.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                settings.update({k: loaded.get(k, v) for k, v in DEFAULT_SETTINGS.items()})
        except Exception:
            pass
    return settings


def save_settings(settings: dict) -> None:
    data = DEFAULT_SETTINGS.copy()
    data.update(settings)
    with SETTINGS_PATH.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


class CampDatabase:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path, timeout=1.5)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=1500")
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS camps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school TEXT NOT NULL DEFAULT '',
                college TEXT NOT NULL DEFAULT '',
                registration_number TEXT NOT NULL DEFAULT '',
                notice_url TEXT NOT NULL DEFAULT '',
                signup_start TEXT NOT NULL DEFAULT '',
                signup_end TEXT NOT NULL DEFAULT '',
                signup_url TEXT NOT NULL DEFAULT '',
                result_date TEXT NOT NULL DEFAULT '',
                result_url TEXT NOT NULL DEFAULT '',
                camp_start TEXT NOT NULL DEFAULT '',
                camp_end TEXT NOT NULL DEFAULT '',
                camp_format TEXT NOT NULL DEFAULT '',
                camp_address TEXT NOT NULL DEFAULT '',
                advisor TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '待确认',
                priority TEXT NOT NULL DEFAULT '普通',
                project_type TEXT NOT NULL DEFAULT '硕士',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self.migrate_schema()
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_camps_signup_end ON camps(signup_end)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_camps_camp_start ON camps(camp_start)")
        self.conn.commit()

    def migrate_schema(self) -> None:
        rows = self.conn.execute("PRAGMA table_info(camps)").fetchall()
        existing = {row["name"] for row in rows}
        if "registration_number" not in existing:
            self.conn.execute("ALTER TABLE camps ADD COLUMN registration_number TEXT NOT NULL DEFAULT ''")
        if "advisor" not in existing:
            self.conn.execute("ALTER TABLE camps ADD COLUMN advisor TEXT NOT NULL DEFAULT ''")
        if "project_type" not in existing:
            self.conn.execute("ALTER TABLE camps ADD COLUMN project_type TEXT NOT NULL DEFAULT '硕士'")

    def all_camps(self) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT * FROM camps
            ORDER BY
                CASE WHEN signup_end = '' THEN 1 ELSE 0 END,
                signup_end,
                CASE WHEN camp_start = '' THEN 1 ELSE 0 END,
                camp_start,
                school
            """
        ).fetchall()
        camps = [dict(row) for row in rows]
        for camp in camps:
            camp["status"] = normalize_status(camp.get("status"))
            camp["priority"] = normalize_priority(camp.get("priority"))
            camp["project_type"] = normalize_project_type(camp.get("project_type"))
        return camps

    def get(self, camp_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM camps WHERE id = ?", (camp_id,)).fetchone()
        if not row:
            return None
        camp = dict(row)
        camp["status"] = normalize_status(camp.get("status"))
        camp["priority"] = normalize_priority(camp.get("priority"))
        camp["project_type"] = normalize_project_type(camp.get("project_type"))
        return camp

    def save(self, data: dict) -> int:
        payload = {field: safe_text(data.get(field)).strip() for field in EDITABLE_FIELDS}
        payload["status"] = normalize_status(payload.get("status"))
        payload["priority"] = normalize_priority(payload.get("priority"))
        payload["project_type"] = normalize_project_type(payload.get("project_type"))
        current = now_text()
        camp_id = data.get("id")
        if camp_id:
            payload["updated_at"] = current
            assignments = ", ".join(f"{field} = ?" for field in EDITABLE_FIELDS + ["updated_at"])
            values = [payload[field] for field in EDITABLE_FIELDS] + [payload["updated_at"], int(camp_id)]
            self.conn.execute(f"UPDATE camps SET {assignments} WHERE id = ?", values)
            self.conn.commit()
            return int(camp_id)

        payload["created_at"] = current
        payload["updated_at"] = current
        fields = EDITABLE_FIELDS + ["created_at", "updated_at"]
        placeholders = ", ".join("?" for _ in fields)
        cursor = self.conn.execute(
            f"INSERT INTO camps ({', '.join(fields)}) VALUES ({placeholders})",
            [payload[field] for field in fields],
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def delete(self, camp_id: int) -> None:
        self.conn.execute("DELETE FROM camps WHERE id = ?", (camp_id,))
        self.conn.commit()

    def replace_all(self, rows: list[dict]) -> None:
        current = now_text()
        fields = EDITABLE_FIELDS + ["created_at", "updated_at"]
        placeholders = ", ".join("?" for _ in fields)
        with self.conn:
            self.conn.execute("DELETE FROM camps")
            for row in rows:
                payload = {field: safe_text(row.get(field)).strip() for field in EDITABLE_FIELDS}
                payload["status"] = normalize_status(payload.get("status"))
                payload["priority"] = normalize_priority(payload.get("priority"))
                payload["project_type"] = normalize_project_type(payload.get("project_type"))
                payload["created_at"] = safe_text(row.get("created_at")).strip() or current
                payload["updated_at"] = safe_text(row.get("updated_at")).strip() or current
                self.conn.execute(
                    f"INSERT INTO camps ({', '.join(fields)}) VALUES ({placeholders})",
                    [payload[field] for field in fields],
                )

    def close(self) -> None:
        self.conn.close()


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1
            return
        if tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        cleaned = html.unescape(data)
        if cleaned.strip():
            self.parts.append(cleaned)

    def get_text(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        lines = [line.strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line)


def html_to_text(page_html: str) -> tuple[str, list[str]]:
    parser = TextExtractor()
    parser.feed(page_html)
    return parser.get_text(), parser.links


def guess_charset(raw: bytes, content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type, re.I)
    if match:
        return match.group(1)
    head = raw[:4096].decode("ascii", errors="ignore")
    match = re.search(r"charset=['\"]?([\w-]+)", head, re.I)
    if match:
        return match.group(1)
    return "utf-8"


def create_https_context() -> ssl.SSLContext:
    if certifi is not None:
        try:
            context = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            context = ssl.create_default_context()
    else:
        context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def summarize_fetch_error(exc: object, limit: int = 220) -> str:
    text = safe_text(exc).strip()
    if not text:
        return "未知错误"
    if "CERTIFICATE_VERIFY_FAILED" in text or "certificate verify failed" in text:
        return "证书校验失败，已尝试改用浏览器抓取。"
    if "Executable doesn't exist" in text or "playwright install" in text:
        return "未找到可用的浏览器内核。请安装或更新 Microsoft Edge / Google Chrome 后重试。"
    if "Target page, context or browser has been closed" in text:
        return "浏览器被关闭或启动失败，请稍后重试。"
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def registry_browser_path(app_name: str) -> Path | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except Exception:
        return None
    subkeys = (
        rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{app_name}",
        rf"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{app_name}",
    )
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for subkey in subkeys:
            try:
                with winreg.OpenKey(root, subkey) as key:
                    value, _kind = winreg.QueryValueEx(key, "")
            except OSError:
                continue
            candidate = Path(safe_text(value).strip().strip('"'))
            if candidate.exists():
                return candidate
    return None


def system_browser_paths() -> list[Path]:
    candidates: list[Path | None] = []
    if sys.platform == "win32":
        for name in ("msedge.exe", "chrome.exe", "chromium.exe"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
            candidates.append(registry_browser_path(name))
        roots = [
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
            os.environ.get("LOCALAPPDATA"),
        ]
        relative_paths = (
            ("Microsoft", "Edge", "Application", "msedge.exe"),
            ("Google", "Chrome", "Application", "chrome.exe"),
            ("Chromium", "Application", "chromium.exe"),
        )
        for root in roots:
            if not root:
                continue
            for relative in relative_paths:
                candidates.append(Path(root).joinpath(*relative))
    elif sys.platform == "darwin":
        for candidate in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ):
            candidates.append(Path(candidate))
    else:
        for name in ("google-chrome", "microsoft-edge", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))

    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        key = str(resolved).lower()
        if key in seen or not resolved.exists():
            continue
        seen.add(key)
        result.append(resolved)
    return result


def browser_launch_options() -> list[tuple[str, dict]]:
    options: list[tuple[str, dict]] = []
    for path in system_browser_paths():
        label = "Edge" if "edge" in path.name.lower() or "edge" in str(path).lower() else "Chrome/Chromium"
        options.append((label, {"executable_path": str(path)}))
    if sys.platform == "win32":
        options.extend(
            [
                ("Edge", {"channel": "msedge"}),
                ("Chrome", {"channel": "chrome"}),
            ]
        )
    if sys.platform in {"win32", "darwin"}:
        options.append(("内置 Chromium", {}))
    return options


def fetch_url_text(url: str, timeout: int = 20, progress=None) -> tuple[str, str]:
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("链接需要以 http:// 或 https:// 开头")
    first_error: Exception | None = None
    if progress:
        progress("正在快速抓取网页...")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=create_https_context()) as response:
            raw = response.read(3_000_000)
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        first_error = RuntimeError(f"网页返回 HTTP {exc.code}，可能禁止普通程序抓取。\n\n{body[:300]}")
    except Exception as exc:
        first_error = exc
    else:
        charset = guess_charset(raw, content_type)
        page = raw.decode(charset, errors="replace")
        text, links = html_to_text(page)
        if not text.strip():
            text = page
        link_block = "\n".join(f"网页链接：{link}" for link in links[:80])
        combined = f"{text}\n\n{link_block}".strip()
        try:
            validate_notice_text(combined, url)
            return combined, page
        except Exception as exc:
            first_error = exc

    try:
        if progress:
            progress("普通抓取失败，正在启动浏览器抓取...")
        text, page = fetch_url_text_with_playwright(url, timeout, progress=progress)
        validate_notice_text(text, url)
        if progress:
            progress("网页正文已抓取，正在整理...")
        return text, page
    except Exception as browser_exc:
        normal_error = summarize_fetch_error(first_error)
        browser_error = summarize_fetch_error(browser_exc)
        raise RuntimeError(
            "自动抓取失败：普通请求和浏览器抓取都没有拿到可用通知正文。"
            f"\n\n普通抓取：{normal_error}"
            f"\n\n浏览器抓取：{browser_error}"
            "\n\n请在浏览器打开页面后，复制正文粘贴到 AI 文本框，再点“粘贴正文识别”。"
        ) from browser_exc


def fetch_url_text_with_playwright(url: str, timeout: int = 20, progress=None) -> tuple[str, str]:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError("本机没有可用的 Playwright，无法使用浏览器抓取兜底。") from exc

    timeout_ms = max(8000, int(timeout) * 1000)
    errors: list[str] = []
    if sys.platform == "darwin":
        user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        )
    else:
        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        )
    launch_options = browser_launch_options()
    if not launch_options:
        if sys.platform == "darwin":
            raise RuntimeError(
                "macOS 浏览器抓取需要 Google Chrome、Microsoft Edge 或 Chromium。"
                "请先安装其中一个浏览器，或在 Safari 中打开网页后复制正文，再使用“粘贴正文识别”。"
            )
        raise RuntimeError(
            "没有找到可用的 Microsoft Edge / Google Chrome / Chromium。"
            "请先安装或更新浏览器，或在浏览器中打开网页后复制正文，再使用“粘贴正文识别”。"
        )
    with sync_playwright() as pw:
        for browser_label, launch_kwargs in launch_options:
            for headless, wait_ms in [(True, 6000), (False, 12000)]:
                if progress:
                    mode = "无头" if headless else "可视"
                    progress(f"正在用{browser_label}{mode}浏览器读取网页...")
                user_data_dir = tempfile.mkdtemp(prefix="summer-camp-browser-")
                context = None
                try:
                    context = pw.chromium.launch_persistent_context(
                        user_data_dir,
                        headless=headless,
                        **launch_kwargs,
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--disable-dev-shm-usage",
                            "--no-first-run",
                            "--ignore-certificate-errors",
                        ],
                        locale="zh-CN",
                        viewport={"width": 1280, "height": 900},
                        user_agent=user_agent,
                    )
                    page = context.new_page()
                    try:
                        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                    except PlaywrightTimeoutError:
                        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    text = wait_for_playwright_notice_text(page, url, wait_ms)
                    content = page.content()
                    links = page.eval_on_selector_all(
                        "a[href]",
                        "(els) => els.slice(0, 80).map(a => a.href)",
                    )
                    link_block = "\n".join(f"网页链接：{link}" for link in links if link)
                    combined = f"{text}\n\n{link_block}".strip()
                    validate_notice_text(combined, url)
                    return combined, content
                except Exception as exc:
                    errors.append(f"{browser_label}{'无头' if headless else '可视'}浏览器：{summarize_fetch_error(exc)}")
                finally:
                    if context is not None:
                        try:
                            context.close()
                        except Exception:
                            pass
                    shutil.rmtree(user_data_dir, ignore_errors=True)
    raise RuntimeError("\n\n".join(errors) or "浏览器没有返回正文")


def wait_for_playwright_notice_text(page, url: str, wait_ms: int) -> str:
    """Wait for animated / script-rendered university pages to expose useful text."""
    best_text = ""
    stable_rounds = 0
    previous_length = -1
    rounds = max(6, wait_ms // 1000)
    page.wait_for_timeout(1200)
    for index in range(rounds):
        if index in {1, 3, 5}:
            page.mouse.wheel(0, 900)
        page.wait_for_timeout(1000)
        text = page.evaluate("document.body ? document.body.innerText : ''") or ""
        if len(text) > len(best_text):
            best_text = text
        current_length = len(re.sub(r"\s+", "", text))
        if abs(current_length - previous_length) < 20:
            stable_rounds += 1
        else:
            stable_rounds = 0
        previous_length = current_length
        if stable_rounds >= 2:
            try:
                validate_notice_text(text, url)
                return text
            except Exception:
                pass
    return best_text


def validate_notice_text(text: str, url: str = "") -> None:
    meaningful = re.sub(r"网页链接：\S+", "", text)
    meaningful = re.sub(r"\s+", "", meaningful)
    forbidden_markers = [
        "403Forbidden",
        "Forbidden",
        "访问受限",
        "无权访问",
        "AccessDenied",
        "安全验证",
        "验证码",
    ]
    if any(marker in text for marker in forbidden_markers):
        raise RuntimeError(
            "这个网页返回了访问限制/反爬页面，程序没有拿到通知正文。"
            "请在浏览器打开该链接，手动复制通知正文到 AI 文本框，再点“识别正文”。"
        )
    chinese_chars = len(re.findall(r"[\u4e00-\u9fa5]", meaningful))
    date_hits = len(re.findall(r"20\d{2}|报名|申请|夏令营|营员|时间|截止|学院|地址|通知", text))
    if len(meaningful) < 120 or chinese_chars < 40 or date_hits < 2:
        raise RuntimeError(
            "通知正文内容缺失，无法提取具体信息。"
            f"\n链接：{url}"
            "\n\n这通常是学校官网禁止程序抓取、页面需要浏览器脚本加载，或当前网络拿到的是空页面。"
            "\n请在浏览器打开页面后，复制正文粘贴到 AI 文本框，再点“识别正文”。"
        )


def find_urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s\"'<>，。；、）)]+", text)


def is_http_url(value: str | None) -> bool:
    return bool(re.match(r"^https?://", safe_text(value).strip(), re.I))


def section_between(text: str, start_pattern: str, end_pattern: str = "") -> str:
    start = re.search(start_pattern, text)
    if not start:
        return ""
    end = re.search(end_pattern, text[start.end() :]) if end_pattern else None
    stop = start.end() + end.start() if end else len(text)
    return text[start.start() : stop].strip()


def extract_applicant_sections(text: str) -> tuple[str, str]:
    master = section_between(
        text,
        r"(?:\d+[）).、]\s*)?(?:预推免)?硕士生申请|硕士申请者",
        r"(?:预推免)?直博生申请|直博申请者|直博生|直博",
    )
    if not master:
        master = section_between(
            text,
            r"(?:预推免)?硕士生申请|硕士申请者|硕士生|硕士",
            r"(?:预推免)?直博生申请|直博申请者|直博生|直博",
        )
    phd = section_between(
        text,
        r"(?:预推免)?直博生申请|直博申请者|直博生|直博",
        r"(?:[一二三四五六七八九十]+[、.．]\s*)?(?:活动通知|入营通知|名单公布|结果通知|复审通知|线下活动时间|活动时间及地点|其他说明)",
    )
    return master, phd


def extract_camp_time_place(text: str) -> tuple[str, str, str, str]:
    normalized = re.sub(r"\s+", " ", text)
    activity_match = re.search(
        r"(?:仅)?参加\s*((?:20\d{2}年)?\d{1,2}月\d{1,2}日?)"
        r"(.{0,40}?(?:宣讲|活动|营|会议|交流))",
        normalized,
    )
    if activity_match:
        try:
            camp_day = normalize_date(activity_match.group(1))
        except ValueError:
            camp_day = ""
        if camp_day:
            camp_format = "线上" if "线上" in activity_match.group(2) else ("线下" if "线下" in activity_match.group(2) else "")
            return camp_day, camp_day, camp_format, ""
    camp_match = re.search(
        r"((?:20\d{2}年)?\d{1,2}月\d{1,2}日?\s*(?:至|到|—|－|-|~|～)\s*(?:(?:\d{1,2}月)?\d{1,2}日?))"
        r"(.{0,80}?)(?:举行|举办|开展|报到|参加)",
        normalized,
    )
    if camp_match:
        parsed = split_date_range(camp_match.group(1))
        if not parsed:
            return "", "", "", ""
        address = re.sub(r"^(?:在|于)", "", camp_match.group(2)).strip(" ，,。；;")
        camp_format = "线下" if "线下" in normalized[max(0, camp_match.start() - 80) : camp_match.end() + 20] else ""
        return parsed[0], parsed[1], camp_format, address
    single_match = re.search(
        r"((?:20\d{2}年)?\d{1,2}月\d{1,2}日?)"
        r"(.{0,80}?)(?:举行|举办|开展|报到|参加)",
        normalized,
    )
    if not single_match:
        return "", "", "", ""
    try:
        camp_day = normalize_date(single_match.group(1))
    except ValueError:
        return "", "", "", ""
    address = re.sub(r"^(?:在|于)", "", single_match.group(2)).strip(" ，,。；;")
    camp_format = "线下" if "线下" in normalized[max(0, single_match.start() - 80) : single_match.end() + 20] else ""
    return camp_day, camp_day, camp_format, address


def extract_material_notes(text: str) -> str:
    material = section_between(text, r"申请人需提交的材料内容|提交材料|申请材料", r"以上|申请者应保证|四、|五、")
    if not material:
        return ""
    cleaned = re.sub(r"\s+", " ", material).strip(" ：:；;")
    return f"需提交材料：{cleaned}" if cleaned else ""


def extract_college(text: str, school: str = "") -> str:
    patterns = [
        r"(?:^|\n|[\s，。；;])([\u4e00-\u9fa5]{2,24}学院)",
        r"([\u4e00-\u9fa5]{2,18}大学[\u4e00-\u9fa5]{2,24}学院)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            college = match.group(1)
            if school and college.startswith(school):
                college = college[len(school) :]
            if college and college != school and not college.endswith("大学"):
                return college
    return ""


def extract_signup_range(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    match = re.search(
        r"报名时间[：:]\s*(?P<start>公布之日起|即日起|自通知发布之日起|(?:20\d{2}年)?\d{1,2}月\d{1,2}日?)"
        r"\s*(?:至|到|—|－|-|~|～)\s*(?P<end>(?:20\d{2}年)?\d{1,2}月\d{1,2}日?)",
        text,
    )
    if not match:
        return "", ""
    start_text = match.group("start")
    end_text = match.group("end")
    try:
        end = normalize_date(end_text)
    except ValueError:
        return "", ""
    start = ""
    if re.search(r"\d", start_text):
        try:
            start = normalize_date(start_text)
        except ValueError:
            start = ""
    return start, end


def extract_offline_activity_section(text: str) -> str:
    return section_between(
        text,
        r"(?:[一二三四五六七八九十]+[、.．]\s*)?(?:线下活动时间及地点|线下活动时间|活动时间及地点)",
        r"(?:[一二三四五六七八九十]+[、.．]\s*)?(?:其他说明|联系方式|联系人)",
    )


def extract_json_object(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("AI 返回内容里没有找到 JSON 对象")


def fallback_extract(text: str, source_url: str = "") -> dict:
    data = {field: "" for field in EDITABLE_FIELDS}
    data["notice_url"] = source_url
    data["status"] = "待确认"
    data["priority"] = "普通"
    data["camp_format"] = "待定"
    notes: list[str] = []
    normalized = re.sub(r"\s+", " ", text)
    master_section, phd_section = extract_applicant_sections(text)
    data["project_type"] = "直博" if phd_section and not master_section else "硕士"
    target_text = master_section or text
    school_match = re.search(r"([\u4e00-\u9fa5]{2,18}大学)", text) or re.search(r"([\u4e00-\u9fa5]{2,18}(?:大学|学院))", text)
    if school_match:
        data["school"] = school_match.group(1)
    college = extract_college(text, data["school"])
    if college:
        data["college"] = college
    if master_section:
        notes.append("识别优先对象：硕士生申请；直博相关内容已放入备注。")
    signup_start, signup_end = extract_signup_range(target_text)
    signup_source = "硕士生申请" if (signup_start or signup_end) and master_section else ""
    if not (signup_start or signup_end) and phd_section:
        signup_start, signup_end = extract_signup_range(phd_section)
        signup_source = "直博生申请" if (signup_start or signup_end) else signup_source
    if not (signup_start or signup_end):
        signup_start, signup_end = extract_signup_range(text)
        signup_source = "通知全文" if (signup_start or signup_end) else signup_source
    if signup_start or signup_end:
        data["signup_start"] = signup_start
        data["signup_end"] = signup_end
        if master_section and signup_source != "硕士生申请":
            notes.append(f"硕士生申请未给出明确报名起止时间，主表报名时间暂采用{signup_source}字段。")
    result_candidates: list[tuple[int, str]] = []
    result_section_pattern = re.compile(
        r"(?:[一二三四五六七八九十]+[、.．]\s*)?(?:活动通知|入营通知|名单公布|结果通知|复审通知)"
        r"(?P<body>[\s\S]{0,260}?)(?=\n\s*(?:[一二三四五六七八九十]+[、.．]|[0-9]+[、.．])|$)",
    )
    for section in result_section_pattern.finditer(text):
        for match in re.finditer(r"((?:20\d{2}年)?\d{1,2}月\d{1,2}日?(?:左右|前后|约|预计)?)", section.group("body")):
            result_candidates.append((0, match.group(1)))
    for match in re.finditer(
        r"(?:入营|录取|名单|结果|公布|查询|告知)[\s\S]{0,80}?"
        r"((?:20\d{2}年)?\d{1,2}月\d{1,2}日?(?:左右|前后|约|预计)?)",
        text,
    ):
        context = text[max(0, match.start() - 60) : match.end() + 30]
        if re.search(r"报名时间|报名截止|报名系统|材料|提交", context):
            continue
        result_candidates.append((1, match.group(1)))
    if result_candidates:
        _, result_text = sorted(result_candidates, key=lambda item: item[0])[0]
        try:
            data["result_date"] = normalize_date(result_text)
            if APPROX_DATE_PATTERN.search(result_text):
                notes.append(f"公布时间原文：{result_text}")
        except ValueError:
            pass
    target_camp_start, target_camp_end, target_format, target_address = extract_camp_time_place(target_text)
    if target_camp_start:
        data["camp_start"] = target_camp_start
        data["camp_end"] = target_camp_end
        data["camp_format"] = target_format
        data["camp_address"] = target_address
    advisor_match = re.search(r"(?:意向导师|导师|科研团队导师|联系导师)[：:\s]*(.{0,80})", target_text)
    if advisor_match:
        data["advisor"] = advisor_match.group(1).strip(" ，。；;\n")
    if phd_section:
        phd_start, phd_end, phd_format, phd_address = extract_camp_time_place(phd_section)
        if not phd_start and master_section:
            phd_start, phd_end, phd_format, phd_address = extract_camp_time_place(extract_offline_activity_section(text))
        phd_parts = []
        phd_urls = find_urls(phd_section)
        if phd_urls:
            phd_parts.append("直博相关链接：" + "；".join(phd_urls[:3]))
        if phd_parts:
            notes.append("；".join(phd_parts))
    urls = find_urls(text)
    target_urls = find_urls(target_text)
    for url in target_urls + urls:
        lowered = url.lower()
        if not data["signup_url"] and any(key in lowered for key in ("login", "apply", "xly", "signup", "yz", "yjs")):
            data["signup_url"] = url
        if not data["signup_url"] and "wjx.cn" in lowered:
            data["signup_url"] = url
    if urls and not data["notice_url"]:
        data["notice_url"] = urls[0]
    data["camp_format"] = normalize_camp_format(data.get("camp_format"))
    data["notes"] = "\n".join(notes) if notes else "本地规则仅做粗略识别，建议继续使用 AI 或手动复核。"
    return data


def build_ai_prompt(text: str, source_url: str = "") -> str:
    today = date.today().isoformat()
    compact_text = text.strip()
    if len(compact_text) > 18000:
        compact_text = compact_text[:12000] + "\n\n……中间内容已截断……\n\n" + compact_text[-6000:]
    return f"""
今天是 {today}。请从下面的高校夏令营/预推免/招生通知中独立完成全部读取、判断和结构化抽取，程序不会再用本地规则替你修正主字段。

要求：
1. 只输出一个 JSON 对象，不要解释。
2. 日期统一为 YYYY-MM-DD；如果原文只有月日，请结合通知年份、活动年份或今天年份推断。
3. 如果原文是“7月上旬/中旬/下旬/具体时间另行通知/另行通知/待定/暂定”等没有具体日子的模糊时间，不能转换成 7月1日、7月10日等具体日期；对应日期字段必须填空字符串，并在 notes 用一句短话提醒。
4. 如果原文是“7月3日左右/约7月3日/预计7月3日”这类已有具体月日的近似日期，日期字段填写该具体日期；仅在这个不确定性很重要时才在 notes 用一句短话提醒。
5. 报名开始日期的处理：
   - 如果原文写“即日起/今日起/从今天起/自通知发布之日起/发布之日起/公布之日起”，signup_start 填今天 {today}。
   - 如果原文只写报名截止、申请截止、提交截止等结束日期，没有说明报名开始日期，signup_start 也填今天 {today}。
   - 只有原文明确说报名开始时间另行通知/待定且没有截止日期时，signup_start 才留空。
6. 找不到的字段填空字符串。
7. 报名起止时间、公布时间、参营时间要优先从原文明确字段提取，不要编造。
8. 学校和学院要拆开填写：例如“中山大学计算机学院”应输出 school=中山大学，college=计算机学院；学院字段尽量保留“学院/系/研究院/中心”等完整机构名。
9. 你必须优先抽取“硕士/预推免硕士/硕士申请者”的相关信息：
   - 只要通知中存在硕士申请路径，project_type 必须填“硕士”，主字段也以硕士信息为准。
   - 只有全文确实只面向直博/博士、没有硕士申请路径时，project_type 才填“直博”，并抽取直博字段。
   - 如果硕士某个字段缺失，才可以借用直博或全文通用字段；借用时必须在 notes 说明“某字段暂采用直博/通用信息”。
10. 主字段只填写最终要录入系统、最适合硕士申请使用的信息。不要把直博报名系统、直博线下活动、直博导师要求覆盖到硕士主字段，除非硕士对应字段确实没有。
11. advisor 填写硕士申请相关的意向导师、导师联系要求或导师姓名；如果只出现直博导师要求，不要覆盖硕士主判断，可在 advisor 或 notes 中注明来源。
12. notes 只写需要用户特别注意的短提醒，不要摘要全文；有多少特殊要求就写多少条，但每条尽量短且必须完整说清楚。
13. notes 禁止写这些内容：申请条件长段落、申请材料清单、已填写进主字段的报名/公布/参营时间和地点、普通联系方式、普通截止日期、普通活动流程。用户需要细节会自己看原文。
14. notes 只保留这些情况：时间含糊或冲突、还需在另一个系统/问卷/邮箱同步填写或确认、硕士字段借用了直博/通用信息、必须提前联系导师且会影响报名、其他非常特殊的风险。
15. notes 中需要醒目标记的事项单独成行并以“【重点】”开头；普通提醒不用标重点。不要输出半截句子，不要复制长原文。
16. status 只能填写：待确认、已报名、已入营、放弃/落选；新识别出的项目通常填“待确认”。
17. priority 只能填写：普通、关注；除非用户特别标记或文本明显非常重要，否则填“普通”。
18. project_type 只能填写：硕士、直博。
19. camp_format 必须且只能填写以下四个值之一：线上、线下、待定、线上或线下。
   - 原文明确线上宣讲、线上会议、腾讯会议、视频会议、直播等，填“线上”。
   - 原文明确线下、到校、现场、报到、在某校区举行等，填“线下”。
   - 原文明确可能线上也可能线下、形式另行确定但两种都有可能，填“线上或线下”。
   - 原文未说明形式，或只写形式待定/另行通知，填“待定”。
   - 不要输出“线下活动”“线上宣讲”“到校参加”“网络会议”“另行通知”等其他文字。
20. result_url 的填写规则：
   - 如果原文给出了明确的公布/名单/结果查询网址，填写该原始网址。
   - 如果原文没有给具体网址，只写“学院官网公布/官网公布/学院网站公布/报名系统查询/邮件通知”等，result_url 不要猜测网址，直接填写中文短提示，例如“学院官网公布（原文未给具体公布网址）”“报名系统查询或邮件通知（原文未给具体公布网址）”。
   - 不要根据学校或学院名称自行搜索、推断、补全官网链接；不要把通知页链接当作公布网址，除非原文明确说结果就在该链接公布。

JSON 字段：
{{
  "school": "",
  "college": "",
  "registration_number": "",
  "notice_url": "{source_url}",
  "signup_start": "",
  "signup_end": "",
  "signup_url": "",
  "result_date": "",
  "result_url": "",
  "camp_start": "",
  "camp_end": "",
  "camp_format": "",
  "camp_address": "",
  "advisor": "",
  "status": "待确认",
  "priority": "普通",
  "project_type": "硕士",
  "notes": ""
}}

原始通知链接：{source_url}

通知正文：
{compact_text}
""".strip()


def call_chat_completions(settings: dict, runtime_api_key: str, prompt: str) -> dict:
    api_url = normalize_chat_url(os.environ.get("SUMMER_CAMP_AI_API_URL") or safe_text(settings.get("api_url")).strip())
    model = os.environ.get("SUMMER_CAMP_AI_MODEL") or safe_text(settings.get("model")).strip()
    api_key = (
        os.environ.get("SUMMER_CAMP_AI_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or runtime_api_key
        or safe_text(settings.get("api_key")).strip()
    )
    timeout = int(settings.get("timeout_seconds") or 60)
    if not api_url:
        raise RuntimeError("请先在“AI 设置”里填写千问 base_url 或 Chat Completions 接口地址")
    if "{WorkspaceId}" in api_url or "%7BWorkspaceId%7D" in api_url:
        raise RuntimeError("千问北京/新加坡等地域 URL 需要把 {WorkspaceId} 替换成你的业务空间 ID")
    if not model:
        raise RuntimeError("请先在“AI 设置”里填写模型名")
    if not api_key:
        raise RuntimeError("请先在“AI 设置”里填写 API Key，或设置 DASHSCOPE_API_KEY 环境变量")

    messages = [
        {
            "role": "system",
            "content": (
                "你是严谨的信息抽取助手。必须输出可解析 JSON，字段未知时留空，不得编造。"
                "枚举字段必须完全使用用户给定选项；公布网址不得自行搜索、推断或补全。"
            ),
        },
        {"role": "user", "content": prompt},
    ]
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 1600,
        "response_format": {"type": "json_object"},
    }
    try:
        return _post_chat(api_url, api_key, payload, timeout)
    except RuntimeError as exc:
        message = str(exc)
        if "response_format" not in message:
            raise
        payload.pop("response_format", None)
        return _post_chat(api_url, api_key, payload, timeout)


def _post_chat(api_url: str, api_key: str, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        api_url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        raw = _urlopen_bytes(request, timeout)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(format_http_error(exc.code, body)) from exc
    except urllib.error.URLError as exc:
        reason = safe_text(getattr(exc, "reason", exc))
        raise RuntimeError(f"AI 接口连接失败：{reason}") from exc

    reply = json.loads(raw.decode("utf-8", errors="replace"))
    try:
        content = reply["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"AI 返回格式不符合 Chat Completions：{reply}") from exc
    return extract_json_object(content)


def _urlopen_bytes(request: urllib.request.Request, timeout: int) -> bytes:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return response.read()


def format_http_error(code: int, body: str) -> str:
    snippet = safe_text(body).strip()[:1000]
    hint = ""
    if code == 403:
        hint = (
            "\n\n排查建议："
            "\n1. 确认 base_url 的地域和你的业务空间地域一致。"
            "\n2. 北京/新加坡/德国/日本地域要把 {WorkspaceId} 换成真实业务空间 ID。"
            "\n3. 确认 API Key 属于该业务空间，并且模型 qwen3.7-plus 已开通权限。"
        )
    elif code == 401:
        hint = "\n\n排查建议：API Key 可能无效、过期或没有正确填写。"
    elif code == 404:
        hint = "\n\n排查建议：接口地址可能写错；如果填 base_url，应以 /compatible-mode/v1 结尾。"
    return f"AI 接口返回 HTTP {code}: {snippet}{hint}"


def normalize_chat_url(api_url: str) -> str:
    api_url = safe_text(api_url).strip().rstrip("/")
    if not api_url:
        return ""
    if api_url.endswith("/chat/completions"):
        return api_url
    if api_url.endswith("/v1"):
        return api_url + "/chat/completions"
    if api_url.endswith("/compatible-mode/v1"):
        return api_url + "/chat/completions"
    return api_url


def sanitize_ai_data(raw: dict, source_url: str = "", original_text: str = "") -> dict:
    data = {field: safe_text(raw.get(field)).strip() for field in EDITABLE_FIELDS}
    data["status"] = normalize_status(data["status"])
    data["priority"] = normalize_priority(data["priority"])
    data["project_type"] = normalize_project_type(data.get("project_type"))
    data["camp_format"] = normalize_camp_format(data.get("camp_format"))
    if source_url and not data["notice_url"]:
        data["notice_url"] = source_url

    notes_extra: list[str] = []
    data = expand_date_ranges(data)
    for field in DATE_FIELDS:
        value = data.get(field, "")
        if not value:
            continue
        if re.search(r"即日起|今日起|今天起|从今天起|自今日起|自今天起", value):
            data[field] = date.today().isoformat()
            notes_extra.append(f"{FIELD_LABELS[field]}原始值：{value}，已按系统日期填写为 {data[field]}")
            continue
        if re.search(r"自通知发布之日起|通知发布之日起|发布之日起", value):
            notes_extra.append(f"{FIELD_LABELS[field]}原始值：{value}，无法从该表达确定具体日期")
            data[field] = ""
            continue
        if re.search(r"公布之日起", value):
            notes_extra.append(f"{FIELD_LABELS[field]}原始值：{value}，无法从该表达确定具体日期")
            data[field] = ""
            continue
        if FUZZY_DATE_PATTERN.search(value):
            notes_extra.append(f"{FIELD_LABELS[field]}含模糊表述：{value}")
            data[field] = ""
            continue
        date_expression = extract_date_expression(value)
        if APPROX_DATE_PATTERN.search(value):
            notes_extra.append(f"{FIELD_LABELS[field]}含近似表述：{value}")
        try:
            data[field] = normalize_date(date_expression)
        except ValueError:
            notes_extra.append(f"{FIELD_LABELS[field]}原始值：{value}")
            data[field] = ""

    if data.get("signup_end") and not data.get("signup_start"):
        signup_end_day = parse_iso_date(data.get("signup_end"))
        default_start = date.today()
        if signup_end_day and signup_end_day < default_start:
            default_start = signup_end_day
        data["signup_start"] = default_start.isoformat()
        notes_extra.append(f"报名开始原文未明确，已按 {data['signup_start']} 填写。")

    if notes_extra:
        existing = data.get("notes", "")
        data["notes"] = (existing + "\n" if existing else "") + "\n".join(notes_extra)
    data["notes"] = normalize_notes_text(data.get("notes", ""))
    return data


def find_vague_context(text: str, keywords: list[str]) -> str:
    if not text:
        return ""
    normalized = re.sub(r"\s+", " ", text)
    for keyword in keywords:
        index = normalized.find(keyword)
        if index < 0:
            continue
        context = normalized[index : min(len(normalized), index + 120)]
        if FUZZY_DATE_PATTERN.search(context):
            return context.strip()
    return ""


def find_date_context(text: str, value: str) -> str:
    if not text or not value:
        return ""
    candidates = [value]
    parsed = parse_iso_date(value)
    if parsed:
        candidates.extend(
            [
                f"{parsed.month}月{parsed.day}日",
                f"{parsed.month}.{parsed.day}",
                f"{parsed.month}-{parsed.day}",
                f"{parsed.year}年{parsed.month}月",
            ]
        )
    for candidate in candidates:
        index = text.find(candidate)
        if index >= 0:
            start = max(0, index - 40)
            end = min(len(text), index + len(candidate) + 60)
            return re.sub(r"\s+", " ", text[start:end]).strip()
    return ""


@dataclass
class CalendarEvent:
    camp_id: int
    day: date
    kind: str
    label: str
    school: str


@dataclass
class CalendarSpan:
    camp_id: int
    start: date
    end: date
    kind: str
    label: str
    school: str
    camp_format: str = ""
    focused: bool = False
    lane: int = 0


class SettingsDialog(tk.Toplevel):
    def __init__(self, master, settings: dict, runtime_key: str, on_save):
        super().__init__(master)
        self.title("AI 设置")
        apply_app_icon(self)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.on_save = on_save
        self.api_url_var = tk.StringVar(value=safe_text(settings.get("api_url")))
        self.model_var = tk.StringVar(value=safe_text(settings.get("model")))
        self.key_var = tk.StringVar(value=runtime_key or safe_text(settings.get("api_key")))
        self.remember_var = tk.BooleanVar(value=True)
        self.show_key_var = tk.BooleanVar(value=False)
        self.test_status_var = tk.StringVar(value="")
        self._testing = False
        self.test_button: ttk.Button | None = None
        self.api_entry: ttk.Entry | None = None
        self.model_entry: ttk.Entry | None = None
        self.key_entry: ttk.Entry | None = None
        self.placeholders: dict[ttk.Entry, tuple[tk.StringVar, str, bool]] = {}
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.after(50, self.lift)

    def _build(self) -> None:
        body = ttk.Frame(self, padding=16)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="接口地址").grid(row=0, column=0, sticky="w", pady=5)
        self.api_entry = ttk.Entry(body, textvariable=self.api_url_var, width=58)
        self.api_entry.grid(row=0, column=1, sticky="ew", pady=5)
        self.install_placeholder(self.api_entry, self.api_url_var, "url: https://api.deepseek.com")

        ttk.Label(body, text="模型名").grid(row=1, column=0, sticky="w", pady=5)
        self.model_entry = ttk.Entry(body, textvariable=self.model_var, width=58)
        self.model_entry.grid(row=1, column=1, sticky="ew", pady=5)
        self.install_placeholder(self.model_entry, self.model_var, "deepseek-v4-flash")

        ttk.Label(body, text="API Key").grid(row=2, column=0, sticky="w", pady=5)
        self.key_entry = ttk.Entry(body, textvariable=self.key_var, width=58, show="*")
        self.key_entry.grid(row=2, column=1, sticky="ew", pady=5)
        self.install_placeholder(self.key_entry, self.key_var, "sk-1234abcd", is_secret=True)

        ttk.Checkbutton(body, text="显示密钥", variable=self.show_key_var, command=self._toggle_key).grid(
            row=3, column=1, sticky="w", pady=(4, 10)
        )

        note = (
            "支持 OpenAI-compatible Chat Completions 接口。接口地址可填 base_url（如 https://.../v1）"
            "或完整 /chat/completions 地址。也可用环境变量 SUMMER_CAMP_AI_API_URL、"
            "SUMMER_CAMP_AI_MODEL、SUMMER_CAMP_AI_API_KEY。"
        )
        ttk.Label(body, text=note, foreground="#5f6b7a", wraplength=520).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(0, 12)
        )

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="保存", command=self._save).pack(side="right")
        self.test_button = ttk.Button(buttons, text="测试连接", command=self._test_connection)
        self.test_button.pack(side="right", padx=(0, 8))
        ttk.Label(body, textvariable=self.test_status_var, foreground="#2563eb").grid(
            row=6, column=0, columnspan=2, sticky="e", pady=(8, 0)
        )

    def _toggle_key(self) -> None:
        is_placeholder = self.placeholders.get(self.key_entry, (None, "", False))[2]
        if is_placeholder:
            self.key_entry.configure(show="")
        else:
            self.key_entry.configure(show="" if self.show_key_var.get() else "*")

    def install_placeholder(self, entry: ttk.Entry, var: tk.StringVar, placeholder: str, is_secret: bool = False) -> None:
        def show_placeholder() -> None:
            if not var.get().strip():
                self.placeholders[entry] = (var, placeholder, True)
                var.set(placeholder)
                entry.configure(foreground="#94a3b8")
                if is_secret:
                    entry.configure(show="")

        def hide_placeholder() -> None:
            if self.placeholders.get(entry, (None, "", False))[2]:
                self.placeholders[entry] = (var, placeholder, False)
                var.set("")
                entry.configure(foreground="#111827")
                if is_secret:
                    entry.configure(show="" if self.show_key_var.get() else "*")

        entry.bind("<FocusIn>", lambda _event: hide_placeholder())
        entry.bind("<FocusOut>", lambda _event: show_placeholder())
        show_placeholder()

    def entry_value(self, entry: ttk.Entry | None, var: tk.StringVar) -> str:
        if entry is not None and self.placeholders.get(entry, (None, "", False))[2]:
            return ""
        return var.get().strip()

    def _save(self) -> None:
        settings, api_key = self._collect_settings()
        if settings is None:
            return
        runtime_key = ""
        self.on_save(settings, runtime_key)
        self.destroy()

    def _collect_settings(self) -> tuple[dict | None, str]:
        api_key = self.entry_value(self.key_entry, self.key_var)
        settings = {
            "api_url": self.entry_value(self.api_entry, self.api_url_var),
            "model": self.entry_value(self.model_entry, self.model_var) or DEFAULT_SETTINGS["model"],
            "timeout_seconds": 60,
            "api_key": api_key if self.remember_var.get() else "",
        }
        return settings, api_key

    def _test_connection(self) -> None:
        if self._testing:
            return
        settings, api_key = self._collect_settings()
        if settings is None:
            return
        self._testing = True
        self.test_status_var.set("正在检测连接...")
        if self.test_button:
            self.test_button.configure(state="disabled")
        self.update_idletasks()

        def finish_ok(result: str) -> None:
            if not self.winfo_exists():
                return
            self._testing = False
            if self.test_button:
                self.test_button.configure(state="normal")
            self.test_status_var.set("连接成功")
            preview = result.strip()
            if len(preview) > 500:
                preview = preview[:500] + "..."
            messagebox.showinfo("连接成功", f"AI 接口可用。\n返回：{preview}", parent=self)

        def finish_error(message: str) -> None:
            if not self.winfo_exists():
                return
            self._testing = False
            if self.test_button:
                self.test_button.configure(state="normal")
            self.test_status_var.set("连接失败，已显示原因")
            messagebox.showerror("连接失败", message, parent=self)

        def runner() -> None:
            try:
                result = call_chat_completions(settings, api_key, '只输出 JSON：{"ok": true}')
            except Exception as exc:
                try:
                    self.after(0, lambda: finish_error(str(exc)))
                except tk.TclError:
                    pass
                return
            try:
                self.after(0, lambda: finish_ok(result))
            except tk.TclError:
                pass

        threading.Thread(target=runner, daemon=True).start()


class SummerCampPlanner(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        apply_app_icon(self)
        self.geometry("1280x820")
        self.minsize(1080, 680)
        self.db = CampDatabase(DB_PATH)
        self.settings = load_settings()
        self.runtime_api_key = ""
        self.camps: list[dict] = []
        self.current_year = date.today().year
        self.current_month = date.today().month
        self.selected_date: date | None = None
        self.selected_camp_id: int | None = None
        self._loading_selection = False
        self._saving = False
        self._refresh_job: str | None = None
        self.vars: dict[str, tk.StringVar] = {}
        self.url_entries: dict[str, ttk.Entry] = {}
        self.notes_text: tk.Text | None = None
        self.ai_text: tk.Text | None = None
        self.ai_url_entry: ttk.Entry | None = None
        self.status_label: ttk.Label | None = None
        self.ai_busy = False
        self.ai_action_buttons: list[ttk.Button] = []
        self.school_tree: ttk.Treeview | None = None
        self.school_list_tab: ttk.Frame | None = None
        self.form_tab: ttk.Frame | None = None
        self.notes_editor_tab: ttk.Frame | None = None
        self.profile_tab: ttk.Frame | None = None
        self.expanded_notes_text: tk.Text | None = None
        self.profile_text: tk.Text | None = None
        self._refreshing_school_tree = False
        self.school_filter_text = ""
        self.school_filter_status = ""
        self.school_filter_priority = ""
        self.school_search_bar: ttk.Frame | None = None
        self.school_search_var = tk.StringVar(value="")
        self.main_paned: ttk.PanedWindow | None = None
        self.main_paned_ratio = 0.62
        self._layout_initialized = False
        self._syncing_main_sash = False
        self._last_main_paned_width = 0
        self._build_style()
        self._build_ui()
        self.refresh_all()
        self.after(180, self.apply_initial_layout)
        self.after(350, self.show_daily_briefing)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self.configure(bg="#eef3f8")
        style.configure(".", font=("Microsoft YaHei UI", 10), background="#eef3f8", foreground="#1f2937")
        style.configure("TFrame", background="#eef3f8")
        style.configure("Panel.TFrame", background="#ffffff")
        style.configure("Header.TFrame", background="#10233f")
        style.configure("HeaderTitle.TLabel", background="#10233f", foreground="#ffffff", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("HeaderSub.TLabel", background="#10233f", foreground="#c8d6e8", font=("Microsoft YaHei UI", 10))
        style.configure("Status.TLabel", background="#10233f", foreground="#dbeafe")
        style.configure("Section.TLabelframe", background="#ffffff", bordercolor="#d8e0eb", relief="solid")
        style.configure("Section.TLabelframe.Label", background="#ffffff", foreground="#23324a", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("TLabel", background="#eef3f8", foreground="#1f2937")
        style.configure("Panel.TLabel", background="#ffffff")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#6b7280")
        style.configure("TButton", padding=(11, 6), background="#f8fafc", foreground="#1f2937", bordercolor="#cbd5e1")
        style.map("TButton", background=[("active", "#edf2f7")])
        style.configure("Toolbar.TButton", padding=(10, 6), background="#203a5f", foreground="#ffffff", bordercolor="#2e4f7e")
        style.map("Toolbar.TButton", background=[("active", "#2b4c79")], foreground=[("active", "#ffffff")])
        style.configure("Accent.TButton", padding=(12, 7), foreground="#ffffff", background="#2563eb", bordercolor="#2563eb")
        style.map("Accent.TButton", background=[("active", "#1d4ed8")], foreground=[("active", "#ffffff")])
        style.configure("Danger.TButton", padding=(10, 6), foreground="#ffffff", background="#dc2626", bordercolor="#dc2626")
        style.map("Danger.TButton", background=[("active", "#b91c1c")], foreground=[("active", "#ffffff")])
        style.configure("RichToolbar.TFrame", background="#f8fbff")
        style.configure(
            "RichTool.TButton",
            padding=(7, 3),
            background="#ffffff",
            foreground="#334155",
            bordercolor="#d8e2ef",
            font=RICH_TOOL_FONT,
        )
        style.map("RichTool.TButton", background=[("active", "#eef6ff")], foreground=[("active", "#111827")])
        style.configure(
            "RichRed.TButton",
            padding=(7, 3),
            background="#fff7f7",
            foreground="#dc2626",
            bordercolor="#fecaca",
            font=RICH_TOOL_FONT,
        )
        style.map("RichRed.TButton", background=[("active", "#fee2e2")], foreground=[("active", "#b91c1c")])
        style.configure("Treeview", rowheight=31, background="#ffffff", fieldbackground="#ffffff", foreground="#1f2937", bordercolor="#d8e0eb")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"), background="#f1f5f9", foreground="#334155")
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", "#111827")])
        style.configure("TNotebook", background="#eef3f8", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 8), background="#e2e8f0", foreground="#334155")
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")], foreground=[("selected", "#111827")])
        style.configure("Link.TEntry", fieldbackground="#f8fbff", foreground="#1d4ed8", bordercolor="#bfdbfe")

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=(18, 14, 18, 12), style="Header.TFrame")
        toolbar.pack(side="top", fill="x")
        title_box = ttk.Frame(toolbar, style="Header.TFrame")
        title_box.pack(side="left", fill="y")
        ttk.Label(title_box, text="夏令营日程助手", style="HeaderTitle.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="报名、公布、参营节点统一管理", style="HeaderSub.TLabel").pack(anchor="w", pady=(2, 0))

        action_box = ttk.Frame(toolbar, style="Header.TFrame")
        action_box.pack(side="right", fill="y")
        ttk.Button(action_box, text="AI 设置", style="Toolbar.TButton", command=self.open_settings).pack(side="right", padx=(8, 0))
        self.status_var = tk.StringVar(value="就绪")
        self.status_label = ttk.Label(
            toolbar,
            textvariable=self.status_var,
            style="Status.TLabel",
            width=48,
            anchor="e",
        )
        self.status_label.pack(side="right", padx=12)
        ttk.Button(action_box, text="个人信息", style="Toolbar.TButton", command=self.open_personal_profile).pack(side="right", padx=(8, 0))
        ttk.Button(action_box, text="导出日程", style="Toolbar.TButton", command=self.export_schedule).pack(side="right", padx=(8, 0))
        ttk.Button(action_box, text="导出备份", style="Toolbar.TButton", command=self.export_full_backup).pack(side="right", padx=(8, 0))
        ttk.Button(action_box, text="导入备份", style="Toolbar.TButton", command=self.import_backup).pack(side="right", padx=(8, 0))
        ttk.Button(action_box, text="新建", style="Toolbar.TButton", command=self.clear_form).pack(side="right", padx=(8, 0))

        paned = ttk.PanedWindow(self, orient="horizontal")
        self.main_paned = paned
        paned.pack(side="top", fill="both", expand=True, padx=16, pady=16)
        paned.bind("<Configure>", self.on_main_paned_configure)
        paned.bind("<ButtonRelease-1>", self.remember_main_paned_ratio)

        left = ttk.Frame(paned, width=760, style="Panel.TFrame")
        right = ttk.Frame(paned, width=460, style="Panel.TFrame")
        paned.add(left, weight=3)
        paned.add(right, weight=2)

        left_paned = ttk.PanedWindow(left, orient="vertical")
        left_paned.pack(fill="both", expand=True)
        calendar_pane = ttk.Frame(left_paned, style="Panel.TFrame")
        tree_pane = ttk.Frame(left_paned, style="Panel.TFrame")
        left_paned.add(calendar_pane, weight=4)
        left_paned.add(tree_pane, weight=2)

        self._build_calendar(calendar_pane)
        self._build_tree(tree_pane)
        self._build_right_panel(right)

    def apply_initial_layout(self) -> None:
        if self._layout_initialized:
            return
        self._layout_initialized = True
        self.update_idletasks()
        self.sync_main_paned_sash()

    def sync_main_paned_sash(self) -> None:
        if self.main_paned is None:
            return
        try:
            width = self.main_paned.winfo_width()
            if width <= 200:
                return
            self._last_main_paned_width = width
            target = int(width * self.main_paned_ratio)
            current = self.main_paned.sashpos(0)
            if abs(current - target) <= 2:
                return
            self._syncing_main_sash = True
            self.main_paned.sashpos(0, target)
            self.after_idle(self.clear_main_sash_sync_flag)
        except tk.TclError:
            pass

    def clear_main_sash_sync_flag(self) -> None:
        self._syncing_main_sash = False

    def on_main_paned_configure(self, event=None) -> None:
        if self.main_paned is None:
            return
        width = self.main_paned.winfo_width()
        if width <= 200:
            return
        if not self._layout_initialized or abs(width - self._last_main_paned_width) > 2:
            self.after_idle(self.sync_main_paned_sash)

    def remember_main_paned_ratio(self, _event=None) -> None:
        if self.main_paned is None or self._syncing_main_sash:
            return
        try:
            width = self.main_paned.winfo_width()
            pos = self.main_paned.sashpos(0)
        except tk.TclError:
            return
        if width <= 200:
            return
        ratio = pos / width
        if 0.30 <= ratio <= 0.78:
            self.main_paned_ratio = ratio
            self._last_main_paned_width = width

    def _build_calendar(self, parent: ttk.Frame) -> None:
        calendar_box = ttk.LabelFrame(parent, text="日历", style="Section.TLabelframe")
        calendar_box.pack(fill="both", expand=True)
        calendar_box.columnconfigure(0, weight=1)
        calendar_box.rowconfigure(1, weight=1)

        header = ttk.Frame(calendar_box, padding=(12, 10), style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        ttk.Button(header, text="‹", width=4, command=self.prev_month).pack(side="left")
        self.month_label = ttk.Label(header, text="", font=("Microsoft YaHei UI", 14, "bold"), style="Panel.TLabel")
        self.month_label.pack(side="left", padx=12)
        ttk.Button(header, text="›", width=4, command=self.next_month).pack(side="left")
        ttk.Button(header, text="今天", command=self.go_today).pack(side="left", padx=8)
        self.selected_date_var = tk.StringVar(value="")
        ttk.Label(header, textvariable=self.selected_date_var, style="Muted.TLabel").pack(side="right")

        self.calendar_grid = tk.Frame(calendar_box, bg="#d8e2ef")
        self.calendar_grid.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        for col in range(7):
            self.calendar_grid.columnconfigure(col, weight=1, uniform="calendar")
        for row in range(7):
            self.calendar_grid.rowconfigure(row, weight=1 if row else 0)

    def _build_tree(self, parent: ttk.Frame) -> None:
        list_box = ttk.LabelFrame(parent, text="项目列表", style="Section.TLabelframe")
        list_box.pack(fill="both", expand=True, pady=(10, 0))
        columns = ("event", "school", "reg", "date", "format", "status", "next")
        self.tree = ttk.Treeview(list_box, columns=columns, show="headings", height=8)
        headings = {
            "event": "类型",
            "school": "学校/学院",
            "reg": "报名号",
            "date": "时间",
            "format": "形式",
            "status": "状态",
            "next": "提醒",
        }
        widths = {
            "event": 78,
            "school": 220,
            "reg": 110,
            "date": 128,
            "format": 90,
            "status": 90,
            "next": 130,
        }
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor="w")
        scrollbar = ttk.Scrollbar(list_box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Double-1>", self.open_tree_row_links)
        self.tree.tag_configure("pending_signup", background="#fef9c3", foreground="#854d0e")
        self.tree.tag_configure("signup", background="#eff6ff", foreground="#1d4ed8")
        self.tree.tag_configure("result", background="#faf5ff", foreground="#7c3aed")
        self.tree.tag_configure("camp", background="#f0fdf4", foreground="#15803d")
        self.tree.tag_configure("status_pending", background="#fef9c3", foreground="#854d0e")
        self.tree.tag_configure("status_inactive", background="#f1f5f9", foreground="#64748b")

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill="both", expand=True)

        list_outer = ttk.Frame(self.notebook)
        form_outer = ttk.Frame(self.notebook)
        ai_outer = ttk.Frame(self.notebook)
        notes_outer = ttk.Frame(self.notebook)
        profile_outer = ttk.Frame(self.notebook)
        self.school_list_tab = list_outer
        self.form_tab = form_outer
        self.notes_editor_tab = notes_outer
        self.profile_tab = profile_outer
        self.notebook.add(list_outer, text="学校列表")
        self.notebook.add(form_outer, text="手动录入")
        self.notebook.add(ai_outer, text="AI 一键录入")
        self.notebook.add(notes_outer, text="备注编辑")
        self.notebook.add(profile_outer, text="个人信息")
        self.notebook.hide(notes_outer)
        self.notebook.hide(profile_outer)

        self._build_school_list(list_outer)
        self._build_form(form_outer)
        self._build_ai_panel(ai_outer)
        self._build_notes_editor(notes_outer)
        self._build_profile_panel(profile_outer)

    def _build_notes_editor(self, parent: ttk.Frame) -> None:
        body = ttk.Frame(parent, padding=16, style="Panel.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        header = ttk.Frame(body, style="Panel.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(header, text="备注大编辑", font=("Microsoft YaHei UI", 14, "bold"), style="Panel.TLabel").pack(side="left")

        self.expanded_notes_text = tk.Text(body, wrap="word", undo=True, font=("Microsoft YaHei UI", 10))
        configure_rich_text_tags(self.expanded_notes_text)
        build_rich_toolbar(
            body,
            self.expanded_notes_text,
            collapse_command=self.close_notes_editor,
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        notes_scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.expanded_notes_text.yview)
        self.expanded_notes_text.configure(yscrollcommand=notes_scrollbar.set)
        self.expanded_notes_text.grid(row=2, column=0, sticky="nsew")
        notes_scrollbar.grid(row=2, column=1, sticky="ns")
        self.expanded_notes_text.tag_configure("note_focus", foreground="#b91c1c", font=("Microsoft YaHei UI", 10, "bold"))
        self.expanded_notes_text.tag_configure("note_section", foreground="#1d4ed8", font=("Microsoft YaHei UI", 10, "bold"))
        self.expanded_notes_text.bind("<KeyRelease>", lambda _event: self.after_idle(self.highlight_expanded_notes), add="+")

        actions = ttk.Frame(body, style="Panel.TFrame")
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="保存并缩小", style="Accent.TButton", command=self.close_notes_editor).pack(side="left")
        ttk.Button(actions, text="取消", command=self.cancel_notes_editor).pack(side="left", padx=8)
        self.bind_mousewheel(self.expanded_notes_text)

    def _build_profile_panel(self, parent: ttk.Frame) -> None:
        body = ttk.Frame(parent, padding=16, style="Panel.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        header = ttk.Frame(body, style="Panel.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(header, text="个人资料备忘录", font=("Microsoft YaHei UI", 14, "bold"), style="Panel.TLabel").pack(side="left")

        self.profile_text = tk.Text(body, wrap="word", undo=True, font=("Microsoft YaHei UI", 10))
        configure_rich_text_tags(self.profile_text)
        build_rich_toolbar(
            body,
            self.profile_text,
            collapse_command=self.close_profile_panel,
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        profile_scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.profile_text.yview)
        self.profile_text.configure(yscrollcommand=profile_scrollbar.set)
        self.profile_text.grid(row=2, column=0, sticky="nsew")
        profile_scrollbar.grid(row=2, column=1, sticky="ns")

        actions = ttk.Frame(body, style="Panel.TFrame")
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="保存", style="Accent.TButton", command=self.save_profile_panel).pack(side="left")
        ttk.Button(actions, text="清空", command=self.clear_profile_panel).pack(side="left", padx=8)
        self.bind_mousewheel(self.profile_text)

    def _build_school_list(self, parent: ttk.Frame) -> None:
        body = ttk.Frame(parent, padding=12, style="Panel.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        self.school_search_bar = ttk.Frame(body, style="Panel.TFrame")
        self.school_search_var.set(self.school_filter_text)
        ttk.Label(self.school_search_bar, text="搜索", style="Panel.TLabel").pack(side="left")
        search_entry = ttk.Entry(self.school_search_bar, textvariable=self.school_search_var, width=24)
        search_entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(self.school_search_bar, text="确定", style="Accent.TButton", command=self.apply_school_text_filter).pack(side="left")
        ttk.Button(self.school_search_bar, text="清除", command=self.clear_school_text_filter).pack(side="left", padx=(6, 0))
        search_entry.bind("<Return>", lambda _event: self.apply_school_text_filter())

        columns = ("school", "status", "priority", "signup", "result", "camp", "hint")
        self.school_tree = ttk.Treeview(body, columns=columns, show="headings", height=18)
        headings = {
            "school": "学校/学院",
            "status": "状态",
            "priority": "优先级",
            "signup": "报名",
            "result": "公布",
            "camp": "开营",
            "hint": "提醒",
        }
        widths = {
            "school": 260,
            "status": 72,
            "priority": 62,
            "signup": 94,
            "result": 70,
            "camp": 94,
            "hint": 88,
        }
        for col in columns:
            if col == "school":
                self.school_tree.heading(col, text=headings[col], command=self.filter_school_text)
            elif col == "status":
                self.school_tree.heading(col, text=headings[col], command=self.cycle_school_status_filter)
            elif col == "priority":
                self.school_tree.heading(col, text=headings[col], command=self.cycle_school_priority_filter)
            else:
                self.school_tree.heading(col, text=headings[col])
            self.school_tree.column(col, width=widths[col], minwidth=widths[col], anchor="w", stretch=False)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.school_tree.yview)
        xscrollbar = ttk.Scrollbar(body, orient="horizontal", command=self.school_tree.xview)
        self.school_tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=xscrollbar.set)
        self.school_tree.grid(row=1, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=1, sticky="ns")
        xscrollbar.grid(row=2, column=0, sticky="ew")
        self.school_tree.bind("<<TreeviewSelect>>", self.on_school_tree_select)
        self.school_tree.tag_configure("focused", background="#fff7ed", foreground="#9a3412")
        self.school_tree.tag_configure("pending", background="#fef9c3", foreground="#854d0e")
        self.school_tree.tag_configure("followup", background="#fee2e2", foreground="#b91c1c")
        self.school_tree.tag_configure("inactive", background="#f1f5f9", foreground="#64748b")
        self.bind_mousewheel(self.school_tree)

    def _build_form(self, parent: ttk.Frame) -> None:
        canvas = tk.Canvas(parent, highlightthickness=0, bg="#ffffff")
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas, padding=16, style="Panel.TFrame")

        def update_scrollregion(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        body.bind("<Configure>", update_scrollregion)
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")

        def resize_body(event) -> None:
            canvas.itemconfigure(body_window, width=event.width, height=max(event.height, body.winfo_reqheight()))
            update_scrollregion()

        canvas.bind("<Configure>", resize_body)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        body.columnconfigure(1, weight=1)

        self.id_var = tk.StringVar(value="")
        ttk.Label(body, text="当前 ID", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=self.id_var, state="readonly").grid(row=0, column=1, sticky="ew", pady=4)

        row = 1
        for field in ["school", "college", "registration_number"]:
            self.vars[field] = tk.StringVar()
            ttk.Label(body, text=FIELD_LABELS[field], style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(body, textvariable=self.vars[field]).grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
            row += 1

        self.vars["status"] = tk.StringVar(value="待确认")
        self.vars["priority"] = tk.StringVar(value="普通")
        ttk.Label(body, text="状态", style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(body, textvariable=self.vars["status"], values=STATUS_OPTIONS, state="readonly").grid(
            row=row, column=1, sticky="ew", pady=4
        )
        row += 1
        ttk.Label(body, text="优先级", style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(body, textvariable=self.vars["priority"], values=PRIORITY_OPTIONS, state="readonly").grid(
            row=row, column=1, sticky="ew", pady=4
        )
        row += 1
        self.vars["project_type"] = tk.StringVar(value="硕士")
        ttk.Label(body, text="类型（硕士/直博）", style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(body, textvariable=self.vars["project_type"], values=PROJECT_TYPE_OPTIONS, state="readonly").grid(
            row=row, column=1, sticky="ew", pady=4
        )
        row += 1

        ttk.Separator(body).grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)
        row += 1

        for field in DATE_FIELDS:
            self.vars[field] = tk.StringVar()
            ttk.Label(body, text=FIELD_LABELS[field], style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(body, textvariable=self.vars[field]).grid(row=row, column=1, sticky="ew", pady=4)
            ttk.Button(body, text="今天", command=lambda f=field: self.vars[f].set(date.today().isoformat())).grid(
                row=row, column=2, sticky="e", padx=(6, 0)
            )
            row += 1

        self.vars["camp_format"] = tk.StringVar(value="待定")
        ttk.Label(body, text="形式", style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(body, textvariable=self.vars["camp_format"], values=FORMAT_OPTIONS).grid(
            row=row, column=1, columnspan=2, sticky="ew", pady=4
        )
        row += 1

        self.vars["camp_address"] = tk.StringVar()
        ttk.Label(body, text="参营地址", style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=self.vars["camp_address"]).grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
        row += 1

        self.vars["advisor"] = tk.StringVar()
        ttk.Label(body, text="意向导师", style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=self.vars["advisor"]).grid(row=row, column=1, columnspan=2, sticky="ew", pady=4)
        row += 1

        ttk.Separator(body).grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)
        row += 1

        for field in ["notice_url", "signup_url", "result_url"]:
            self.vars[field] = tk.StringVar()
            ttk.Label(body, text=FIELD_LABELS[field], style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(body, textvariable=self.vars[field], style="Link.TEntry", cursor="hand2")
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            entry.bind("<Control-Button-1>", lambda _event, f=field: self.open_url(self.vars[f].get()))
            entry.bind("<Double-1>", lambda _event, f=field: self.open_url(self.vars[f].get()))
            self.url_entries[field] = entry
            ttk.Button(body, text="打开", command=lambda f=field: self.open_url(self.vars[f].get())).grid(
                row=row, column=2, sticky="e", padx=(6, 0)
            )
            row += 1

        notes_row = row
        body.rowconfigure(notes_row, weight=1, minsize=180)
        ttk.Label(body, text="备注", style="Panel.TLabel").grid(row=notes_row, column=0, sticky="nw", pady=4)
        notes_box = ttk.Frame(body, style="Panel.TFrame")
        notes_box.grid(row=notes_row, column=1, columnspan=2, sticky="nsew", pady=4)
        notes_box.columnconfigure(0, weight=1)
        notes_box.rowconfigure(1, weight=1)
        self.notes_text = tk.Text(notes_box, height=8, wrap="word", undo=True)
        configure_rich_text_tags(self.notes_text)
        build_rich_toolbar(notes_box, self.notes_text, expand_command=self.open_notes_editor).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4)
        )
        self.notes_text.grid(row=1, column=0, sticky="nsew")
        self.notes_text.tag_configure("note_focus", foreground="#b91c1c", font=("Microsoft YaHei UI", 10, "bold"))
        self.notes_text.tag_configure("note_section", foreground="#1d4ed8", font=("Microsoft YaHei UI", 10, "bold"))
        self.notes_text.bind("<KeyRelease>", lambda _event: self.after_idle(self.highlight_notes), add="+")
        notes_scrollbar = ttk.Scrollbar(notes_box, orient="vertical", command=self.notes_text.yview)
        notes_scrollbar.grid(row=1, column=1, sticky="ns")
        self.notes_text.configure(yscrollcommand=notes_scrollbar.set)
        row += 1

        actions = ttk.Frame(body, style="Panel.TFrame")
        actions.grid(row=row, column=0, columnspan=3, sticky="ew", pady=12)
        ttk.Button(actions, text="保存", style="Accent.TButton", command=self.save_current).pack(side="left")
        ttk.Button(actions, text="删除", style="Danger.TButton", command=self.delete_current).pack(side="left", padx=8)

        for field in EDITABLE_FIELDS:
            self.vars.setdefault(field, tk.StringVar())
        self.bind_mousewheel(canvas, canvas)
        self.bind_mousewheel_recursive(body, canvas)
        self.bind_mousewheel(self.notes_text, add=False)

    def _build_ai_panel(self, parent: ttk.Frame) -> None:
        body = ttk.Frame(parent, padding=16, style="Panel.TFrame")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)

        ttk.Label(body, text="通知链接", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.ai_url_var = tk.StringVar()
        self.ai_url_entry = ttk.Entry(body, textvariable=self.ai_url_var)
        self.ai_url_entry.grid(row=1, column=0, sticky="ew", pady=(4, 8))

        buttons = ttk.Frame(body, style="Panel.TFrame")
        buttons.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        fetch_parse_button = ttk.Button(buttons, text="抓取并识别", style="Accent.TButton", command=self.ai_from_url)
        fetch_parse_button.pack(side="left")
        fetch_button = ttk.Button(buttons, text="仅抓取", command=self.fetch_ai_url_only)
        fetch_button.pack(side="left", padx=8)
        text_parse_button = ttk.Button(buttons, text="粘贴正文识别", command=self.ai_from_text)
        text_parse_button.pack(side="left")
        ttk.Button(buttons, text="AI 设置", command=self.open_settings).pack(side="right")

        self.ai_text = tk.Text(body, height=20, wrap="word", undo=True)
        self.ai_text.grid(row=3, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.ai_text.yview)
        scrollbar.grid(row=3, column=1, sticky="ns")
        self.ai_text.configure(yscrollcommand=scrollbar.set)

        bottom = ttk.Frame(body, style="Panel.TFrame")
        bottom.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        local_button = ttk.Button(bottom, text="本地粗识别", command=self.local_extract_from_text)
        local_button.pack(side="left")
        clear_button = ttk.Button(bottom, text="清空正文", command=lambda: self.set_ai_text(""))
        clear_button.pack(side="left", padx=8)
        self.ai_action_buttons = [fetch_parse_button, fetch_button, text_parse_button, local_button, clear_button]
        self.bind_mousewheel(self.ai_text)

    def bind_mousewheel(self, widget, target=None, add=True) -> None:
        def on_mousewheel(event):
            scroll_target = target or widget
            if getattr(event, "num", None) == 4:
                units = -3
            elif getattr(event, "num", None) == 5:
                units = 3
            elif getattr(event, "delta", 0):
                units = -1 if event.delta > 0 else 1
            else:
                units = 0
            if units and hasattr(scroll_target, "yview_scroll"):
                scroll_target.yview_scroll(units, "units")
            return "break"

        add_flag = "+" if add else None
        widget.bind("<MouseWheel>", on_mousewheel, add=add_flag)
        widget.bind("<Button-4>", on_mousewheel, add=add_flag)
        widget.bind("<Button-5>", on_mousewheel, add=add_flag)

    def bind_mousewheel_recursive(self, root, target) -> None:
        self.bind_mousewheel(root, target)
        for child in root.winfo_children():
            self.bind_mousewheel_recursive(child, target)

    def set_notes_text(self, value: str) -> None:
        if not self.notes_text:
            return
        load_rich_text(self.notes_text, value, transform_plain=normalize_notes_text)
        self.highlight_notes()

    def get_notes_value(self) -> str:
        if not self.notes_text:
            return ""
        return dump_rich_text(self.notes_text, normalize_plain=normalize_notes_text)

    def set_expanded_notes_text(self, value: str) -> None:
        if not self.expanded_notes_text:
            return
        load_rich_text(self.expanded_notes_text, value, transform_plain=normalize_notes_text)
        self.highlight_expanded_notes()

    def dump_expanded_notes_text(self) -> str:
        if not self.expanded_notes_text:
            return ""
        return dump_rich_text(self.expanded_notes_text, normalize_plain=normalize_notes_text)

    def highlight_notes(self) -> None:
        if not self.notes_text:
            return
        self.highlight_note_widget(self.notes_text)

    def highlight_expanded_notes(self) -> None:
        if not self.expanded_notes_text:
            return
        self.highlight_note_widget(self.expanded_notes_text)

    def highlight_note_widget(self, text_widget: tk.Text) -> None:
        text_widget.tag_remove("note_focus", "1.0", "end")
        text_widget.tag_remove("note_section", "1.0", "end")
        line_count = int(text_widget.index("end-1c").split(".")[0])
        for line_no in range(1, line_count + 1):
            start = f"{line_no}.0"
            end = f"{line_no}.end"
            text = text_widget.get(start, end).strip()
            if not text:
                continue
            if text.startswith(("##", "###")):
                text_widget.tag_add("note_section", start, end)
            if any(marker in text for marker in NOTE_FOCUS_MARKERS):
                text_widget.tag_add("note_focus", start, end)

    def open_notes_editor(self) -> None:
        if self.expanded_notes_text is None or self.notes_editor_tab is None:
            return
        self.set_expanded_notes_text(self.get_notes_value())
        self.notebook.add(self.notes_editor_tab, text="备注编辑")
        self.notebook.select(self.notes_editor_tab)
        self.expanded_notes_text.focus_set()

    def close_notes_editor(self) -> None:
        if self.expanded_notes_text is not None:
            self.set_notes_text(self.dump_expanded_notes_text())
        self.hide_notes_editor()

    def cancel_notes_editor(self) -> None:
        self.hide_notes_editor()

    def hide_notes_editor(self) -> None:
        if self.notes_editor_tab is not None:
            try:
                self.notebook.hide(self.notes_editor_tab)
            except tk.TclError:
                pass
        if self.form_tab is not None:
            self.notebook.select(self.form_tab)

    def refresh_all(self) -> None:
        self.camps = self.db.all_camps()
        self.refresh_views()
        self.update_status()

    def refresh_views(self) -> None:
        self.draw_calendar()
        self.refresh_tree()
        self.refresh_school_list()

    def schedule_refresh_views(self) -> None:
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(35, self._run_scheduled_refresh)

    def _run_scheduled_refresh(self) -> None:
        self._refresh_job = None
        self.refresh_views()

    def update_status(self, text: str | None = None) -> None:
        if text:
            self.status_var.set(compact_status_text(text))
            return
        total = len(self.camps)
        urgent = 0
        today = date.today()
        for camp in self.camps:
            deadline = parse_iso_date(camp.get("signup_end"))
            if deadline and 0 <= (deadline - today).days <= 3:
                urgent += 1
        self.status_var.set(compact_status_text(f"{total} 个项目，{urgent} 个报名截止临近"))

    def set_ai_busy(self, busy: bool, message: str = "") -> bool:
        if busy and self.ai_busy:
            self.update_status("已有 AI 任务正在执行，请稍等...")
            return False
        self.ai_busy = busy
        state = "disabled" if busy else "normal"
        for button in self.ai_action_buttons:
            try:
                button.configure(state=state)
            except tk.TclError:
                pass
        if self.ai_text is not None:
            try:
                self.ai_text.configure(state=state)
            except tk.TclError:
                pass
        if self.ai_url_entry is not None:
            try:
                self.ai_url_entry.configure(state=state)
            except tk.TclError:
                pass
        if message:
            self.update_status(message)
        return True

    def get_ai_text(self) -> str:
        if self.ai_text is None:
            return ""
        original_state = safe_text(self.ai_text.cget("state"))
        try:
            if original_state == "disabled":
                self.ai_text.configure(state="normal")
            return self.ai_text.get("1.0", "end").strip()
        finally:
            if original_state == "disabled":
                self.ai_text.configure(state="disabled")

    def set_ai_text(self, value: str) -> None:
        if self.ai_text is None:
            return
        original_state = safe_text(self.ai_text.cget("state"))
        try:
            if original_state == "disabled":
                self.ai_text.configure(state="normal")
            self.ai_text.delete("1.0", "end")
            self.ai_text.insert("1.0", value)
        finally:
            if original_state == "disabled":
                self.ai_text.configure(state="disabled")

    def collect_spans(self) -> list[CalendarSpan]:
        spans: list[CalendarSpan] = []

        def add_span(camp: dict, start_text: str, end_text: str, kind: str) -> None:
            start = parse_iso_date(start_text)
            end = parse_iso_date(end_text)
            start = start or end
            end = end or start
            if not start or not end:
                return
            if end < start:
                start, end = end, start
            label = EVENT_STYLE.get(kind, ("事件", "", ""))[0]
            spans.append(
                CalendarSpan(
                    camp_id=int(camp["id"]),
                    start=start,
                    end=end,
                    kind=kind,
                    label=label,
                    school=safe_text(camp.get("school") or "未命名"),
                    camp_format=safe_text(camp.get("camp_format")),
                    focused=is_focused(camp),
                )
            )

        for camp in self.camps:
            status = normalize_status(camp.get("status"))
            if status == "放弃/落选":
                continue
            signup_start = parse_iso_date(camp.get("signup_start"))
            signup_end = parse_iso_date(camp.get("signup_end"))
            if signup_end:
                add_span(camp, camp.get("signup_end", ""), camp.get("signup_end", ""), "signup_deadline")
            signup_kind = "pending_signup" if status == "待确认" else "signup"
            signup_bar_end = signup_end - timedelta(days=1) if signup_start and signup_end else signup_end
            if signup_start and signup_bar_end and signup_bar_end >= signup_start:
                add_span(camp, camp.get("signup_start", ""), signup_bar_end.isoformat(), signup_kind)
            elif signup_start and not signup_end:
                add_span(camp, camp.get("signup_start", ""), camp.get("signup_start", ""), signup_kind)
            add_span(camp, camp.get("result_date", ""), camp.get("result_date", ""), "result")
            add_span(camp, camp.get("camp_start", ""), camp.get("camp_end", ""), "camp")
        spans.sort(key=lambda span: (span.start, EVENT_SORT_RANK.get(span.kind, 99), span.end, span.school))
        for index, span in enumerate(spans):
            span.lane = index % 4
        return spans

    def camp_conflicts_by_day(self, data: dict | None = None, exclude_id: int | None = None) -> dict[date, list[str]]:
        camps = [
            camp
            for camp in self.camps
            if int(camp.get("id") or 0) != int(exclude_id or 0)
            and normalize_status(camp.get("status")) != "放弃/落选"
            and may_require_offline(camp)
        ]
        if data:
            candidate = data.copy()
            candidate["id"] = data.get("id") or 0
            if normalize_status(candidate.get("status")) != "放弃/落选" and may_require_offline(candidate):
                camps.append(candidate)
        conflicts: dict[date, list[str]] = {}
        by_day: dict[date, list[str]] = {}
        for camp in camps:
            start = parse_iso_date(camp.get("camp_start"))
            end = parse_iso_date(camp.get("camp_end")) or start
            start = start or end
            if not start or not end:
                continue
            if end < start:
                start, end = end, start
            day = start
            school = safe_text(camp.get("school") or "未命名")
            while day <= end:
                by_day.setdefault(day, []).append(school)
                day += timedelta(days=1)
        for day, schools in by_day.items():
            unique = sorted(set(schools))
            if len(unique) > 1:
                conflicts[day] = unique
        return conflicts

    def camp_conflicts_for_item(self, data: dict, exclude_id: int | None = None) -> dict[date, list[str]]:
        if normalize_status(data.get("status")) == "放弃/落选":
            return {}
        if not may_require_offline(data):
            return {}
        start = parse_iso_date(data.get("camp_start"))
        end = parse_iso_date(data.get("camp_end")) or start
        start = start or end
        if not start or not end:
            return {}
        if end < start:
            start, end = end, start

        conflicts: dict[date, list[str]] = {}
        for camp in self.camps:
            if int(camp.get("id") or 0) == int(exclude_id or 0):
                continue
            if normalize_status(camp.get("status")) == "放弃/落选":
                continue
            if not may_require_offline(camp):
                continue
            other_start = parse_iso_date(camp.get("camp_start"))
            other_end = parse_iso_date(camp.get("camp_end")) or other_start
            other_start = other_start or other_end
            if not other_start or not other_end:
                continue
            if other_end < other_start:
                other_start, other_end = other_end, other_start
            overlap_start = max(start, other_start)
            overlap_end = min(end, other_end)
            if overlap_start > overlap_end:
                continue
            school = self.camp_display_name(camp)
            day = overlap_start
            while day <= overlap_end:
                conflicts.setdefault(day, []).append(school)
                day += timedelta(days=1)
        return {day: sorted(set(schools)) for day, schools in conflicts.items()}

    def camp_display_name(self, camp: dict) -> str:
        school = safe_text(camp.get("school") or "未命名")
        college = safe_text(camp.get("college")).strip()
        return f"{school} / {college}" if college else school

    def show_daily_briefing(self) -> None:
        today = date.today()
        group_specs = [
            ("开始报名", "signup_start", "pending_signup", "今天开始报名"),
            ("截止报名", "signup_end", "signup_deadline", "今天截止报名"),
            ("公布结果", "result_date", "result", "今天公布结果"),
            ("开营", "camp_start", "camp", "今天开始参营"),
        ]
        groups: list[tuple[str, str, list[str]]] = []
        for label, field, kind, title in group_specs:
            names = [
                self.camp_display_name(camp)
                for camp in self.camps
                if normalize_status(camp.get("status")) != "放弃/落选"
                and parse_iso_date(camp.get(field)) == today
            ]
            if names:
                groups.append((title, kind, sorted(set(names))))
        self.show_daily_briefing_dialog(today, groups)

    def show_daily_briefing_dialog(self, today: date, groups: list[tuple[str, str, list[str]]]) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("今日早报")
        apply_app_icon(dialog)
        dialog.configure(bg="#f7f9fc")
        dialog.transient(self)
        dialog.grab_set()

        parent_width = max(self.winfo_width(), 900)
        parent_height = max(self.winfo_height(), 650)
        width = min(820, max(700, int(parent_width * 0.62)))
        height = min(560, max(430, int(parent_height * 0.62)))
        dialog.geometry(f"{width}x{height}")
        dialog.minsize(660, 400)
        dialog.resizable(True, True)

        total = sum(len(names) for _title, _kind, names in groups)
        header = tk.Frame(dialog, bg="#10233f")
        header.pack(side="top", fill="x")
        tk.Label(
            header,
            text=f"{today.strftime('%m.%d')} 今日早报",
            bg="#10233f",
            fg="#ffffff",
            font=("Microsoft YaHei UI", 18, "bold"),
            anchor="w",
        ).pack(side="top", fill="x", padx=22, pady=(18, 2))
        summary = f"今天有 {total} 个关键节点需要关注" if total else "今天没有新的开始、截止、公布或开营节点"
        tk.Label(
            header,
            text=summary,
            bg="#10233f",
            fg="#c8d6e8",
            font=("Microsoft YaHei UI", 11),
            anchor="w",
        ).pack(side="top", fill="x", padx=22, pady=(0, 18))

        content = tk.Frame(dialog, bg="#f7f9fc")
        content.pack(side="top", fill="both", expand=True, padx=18, pady=16)
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)

        canvas = tk.Canvas(content, bg="#f7f9fc", highlightthickness=0)
        scrollbar = ttk.Scrollbar(content, orient="vertical", command=canvas.yview)
        body = tk.Frame(canvas, bg="#f7f9fc")
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(8, 0))

        def sync_scrollregion(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def sync_body_width(event) -> None:
            canvas.itemconfigure(body_window, width=event.width)

        body.bind("<Configure>", sync_scrollregion)
        canvas.bind("<Configure>", sync_body_width)

        if groups:
            for title, kind, names in groups:
                self.add_daily_briefing_section(body, title, kind, names)
        else:
            empty = tk.Frame(body, bg="#ffffff", highlightthickness=1, highlightbackground="#d8e0eb")
            empty.pack(side="top", fill="x", pady=(0, 12))
            tk.Label(
                empty,
                text="今天暂时没有需要立刻处理的节点",
                bg="#ffffff",
                fg="#1f2937",
                font=("Microsoft YaHei UI", 14, "bold"),
                anchor="w",
            ).pack(side="top", fill="x", padx=18, pady=(18, 4))
            tk.Label(
                empty,
                text="可以继续查看项目列表里的后续截止、公布和开营安排。",
                bg="#ffffff",
                fg="#64748b",
                font=("Microsoft YaHei UI", 11),
                anchor="w",
            ).pack(side="top", fill="x", padx=18, pady=(0, 18))

        self.bind_mousewheel(canvas, canvas)
        self.bind_mousewheel_recursive(body, canvas)

        footer = tk.Frame(dialog, bg="#ffffff")
        footer.pack(side="bottom", fill="x")
        ttk.Button(footer, text="知道了", command=dialog.destroy).pack(side="right", padx=18, pady=12)

        dialog.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - dialog.winfo_width()) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.focus_set()
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

    def add_daily_briefing_section(self, parent: tk.Widget, title: str, kind: str, names: list[str]) -> None:
        style = EVENT_STYLE.get(kind, ("提醒", "#1f2937", "#e2e8f0"))
        label, fg, bg = style
        section = tk.Frame(parent, bg="#ffffff", highlightthickness=1, highlightbackground="#d8e0eb")
        section.pack(side="top", fill="x", pady=(0, 12))
        accent = tk.Frame(section, bg=bg, width=8)
        accent.pack(side="left", fill="y")

        inner = tk.Frame(section, bg="#ffffff")
        inner.pack(side="left", fill="both", expand=True, padx=16, pady=14)
        top = tk.Frame(inner, bg="#ffffff")
        top.pack(side="top", fill="x")
        tk.Label(
            top,
            text=title,
            bg="#ffffff",
            fg="#111827",
            font=("Microsoft YaHei UI", 13, "bold"),
            anchor="w",
        ).pack(side="left")
        tk.Label(
            top,
            text=f"{label} · {len(names)}",
            bg=bg,
            fg=fg,
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=8,
            pady=2,
        ).pack(side="right")

        for name in names:
            tk.Label(
                inner,
                text=f"• {name}",
                bg="#ffffff",
                fg="#263244",
                font=("Microsoft YaHei UI", 11),
                anchor="w",
                justify="left",
                wraplength=680,
            ).pack(side="top", fill="x", pady=(8, 0))

    def show_missing_fields(self) -> None:
        followups: list[tuple[str, str]] = []
        for camp in self.camps:
            hint = self.school_followup_hint(camp)
            if hint:
                followups.append((self.camp_display_name(camp), hint))

        if not followups:
            messagebox.showinfo("未填提醒", "目前没有报名截止后需要补录的信息。", parent=self)
            return
        lines = ["这些项目需要及时补录后续信息：", ""]
        for name, hint in sorted(set(followups)):
            lines.append(f"  - {name}：{hint}")
        messagebox.showinfo("未填提醒", "\n".join(lines), parent=self)

    def draw_calendar(self) -> None:
        for widget in self.calendar_grid.winfo_children():
            widget.destroy()
        self.month_label.configure(text=f"{self.current_year} 年 {self.current_month} 月")
        day_names = ["一", "二", "三", "四", "五", "六", "日"]
        for col, name in enumerate(day_names):
            label = tk.Label(
                self.calendar_grid,
                text=name,
                bg="#e6edf7",
                fg="#334155",
                font=("Microsoft YaHei UI", 10, "bold"),
                pady=7,
            )
            label.grid(row=0, column=col, sticky="nsew", padx=1, pady=1)

        today = date.today()
        spans = self.collect_spans()
        camp_conflicts = self.camp_conflicts_by_day()
        month_days = calendar.Calendar(firstweekday=0).monthdatescalendar(self.current_year, self.current_month)
        date_cells: dict[date, tk.Frame] = {}
        for row_index, week in enumerate(month_days, start=1):
            for col_index, day_value in enumerate(week):
                in_month = day_value.month == self.current_month
                is_today = day_value == today
                is_selected = day_value == self.selected_date
                bg = "#ffffff" if in_month else "#f7f9fc"
                if is_today:
                    bg = "#fff7d6"
                if is_selected:
                    bg = "#e4efff"
                cell = tk.Frame(self.calendar_grid, bg=bg, bd=0, highlightthickness=1, highlightbackground="#d7e1ee")
                cell.grid(row=row_index, column=col_index, sticky="nsew", padx=1, pady=1)
                cell.bind("<Button-1>", lambda _event, d=day_value: self.select_date(d))

                date_header = tk.Frame(cell, bg=bg)
                date_header.pack(side="top", fill="x", padx=5, pady=(4, 1))
                date_header.bind("<Button-1>", lambda _event, d=day_value: self.select_date(d))
                date_label = tk.Label(
                    date_header,
                    text=str(day_value.day),
                    anchor="w",
                    bg=bg,
                    fg="#111827" if in_month else "#9aa4b2",
                    font=("Microsoft YaHei UI", 10, "bold" if is_today else "normal"),
                )
                date_label.pack(side="left")
                date_label.bind("<Button-1>", lambda _event, d=day_value: self.select_date(d))
                if day_value in camp_conflicts:
                    conflict_label = tk.Label(
                        date_header,
                        text="冲突",
                        bg="#dc2626",
                        fg="#ffffff",
                        font=("Microsoft YaHei UI", 8, "bold"),
                        padx=4,
                    )
                    conflict_label.pack(side="right")
                    conflict_label.bind("<Button-1>", lambda _event, d=day_value: self.select_date(d))
                date_cells[day_value] = cell

        self.draw_calendar_spans(spans, date_cells)

    def draw_calendar_spans(
        self,
        spans: list[CalendarSpan],
        date_cells: dict[date, tk.Frame],
    ) -> None:
        grouped: dict[date, dict[str, list[CalendarSpan]]] = {}
        for span in spans:
            day = span.start
            while day <= span.end:
                if day in date_cells:
                    grouped.setdefault(day, {}).setdefault(span.kind, []).append(span)
                day += timedelta(days=1)

        for day, groups in grouped.items():
            cell = date_cells.get(day)
            if cell is None:
                continue
            for kind, day_spans in sorted(groups.items(), key=lambda item: EVENT_SORT_RANK.get(item[0], 99)):
                if kind in {"result", "camp"}:
                    render_groups = [[span] for span in sorted(day_spans, key=lambda item: item.school)]
                else:
                    render_groups = [day_spans]
                for render_spans in render_groups:
                    fg, bg = EVENT_STYLE.get(kind, ("", "#334155", "#e2e8f0"))[1:]
                    if kind == "camp":
                        fg, bg = CAMP_FORMAT_EVENT_STYLE.get(
                            format_category(render_spans[0].camp_format),
                            CAMP_FORMAT_EVENT_STYLE["other"],
                        )
                    schools = sorted({span.school for span in render_spans})
                    school_text = schools[0] if schools else ""
                    if len(schools) > 1:
                        school_text = f"{school_text}等"
                    label = EVENT_STYLE.get(kind, ("事件", "", ""))[0]
                    if any(span.focused for span in render_spans):
                        label = f"{label}⭐"
                    title = f"■ {label} {school_text}".strip()
                    first_span = render_spans[0]
                    bar = tk.Label(
                        cell,
                        text=title,
                        anchor="w",
                        bg=bg,
                        fg=fg,
                        font=("Microsoft YaHei UI", 8, "bold"),
                        padx=6,
                        pady=1,
                    )
                    bar.pack(side="top", fill="x", padx=4, pady=1)
                    bar.bind("<Button-1>", lambda _event, cid=first_span.camp_id, d=day: self.select_event(cid, d))

    def prev_month(self) -> None:
        if self.current_month == 1:
            self.current_year -= 1
            self.current_month = 12
        else:
            self.current_month -= 1
        self.schedule_refresh_views()

    def next_month(self) -> None:
        if self.current_month == 12:
            self.current_year += 1
            self.current_month = 1
        else:
            self.current_month += 1
        self.schedule_refresh_views()

    def go_today(self) -> None:
        today = date.today()
        self.current_year = today.year
        self.current_month = today.month
        self.select_date(today)

    def select_date(self, selected: date) -> None:
        self.selected_date = selected
        self.selected_date_var.set(f"选中：{selected.isoformat()}")
        self.schedule_refresh_views()

    def select_event(self, camp_id: int, selected: date) -> None:
        self.selected_date = selected
        self.selected_date_var.set(f"选中：{selected.isoformat()}")
        self.load_camp(camp_id)
        self.schedule_refresh_views()

    def clear_date_filter(self) -> None:
        self.selected_date = None
        self.selected_date_var.set("从今天起")
        self.schedule_refresh_views()

    def camp_has_event_on(self, camp: dict, day: date) -> bool:
        singles = [camp.get("signup_start"), camp.get("signup_end"), camp.get("result_date")]
        if any(parse_iso_date(value) == day for value in singles):
            return True
        start = parse_iso_date(camp.get("camp_start"))
        end = parse_iso_date(camp.get("camp_end")) or start
        return bool(start and end and start <= day <= end)

    def upcoming_items(self, camp: dict, base_day: date) -> list[tuple[date, str, str, str, str, date]]:
        items: list[tuple[date, str, str, str, str, date]] = []
        today = date.today()
        signup_start = parse_iso_date(camp.get("signup_start"))
        signup_end = parse_iso_date(camp.get("signup_end"))
        status = normalize_status(camp.get("status"))
        if status == "放弃/落选":
            return self.inactive_summary_item(camp, base_day)
        signup_kind = "pending_signup" if status == "待确认" else "signup"
        signup_label = EVENT_STYLE["signup"][0]
        if signup_start and signup_end:
            if signup_end >= base_day:
                if status == "待确认":
                    event_day = max(signup_start, base_day)
                    if signup_start <= today <= signup_end:
                        hint_day = today
                    elif today > signup_end:
                        hint_day = signup_end
                    else:
                        hint_day = signup_start
                    hint_label = signup_label
                else:
                    event_day = signup_end
                    hint_day = signup_end
                    hint_label = "报名截止"
                items.append(
                    (
                        event_day,
                        signup_kind,
                        signup_label,
                        format_range(camp.get("signup_start"), camp.get("signup_end")),
                        hint_label,
                        hint_day,
                    )
                )
        elif signup_end:
            if signup_end >= base_day:
                items.append((signup_end, signup_kind, signup_label, format_date_cn(signup_end.isoformat()), "报名截止", signup_end))
        elif signup_start:
            if signup_start >= base_day:
                hint_day = today if signup_start <= today else signup_start
                items.append((signup_start, signup_kind, signup_label, format_date_cn(signup_start.isoformat()), signup_label, hint_day))

        result_day = parse_iso_date(camp.get("result_date"))
        if result_day and result_day >= base_day:
            label = EVENT_STYLE["result"][0]
            items.append((result_day, "result", label, format_date_cn(camp.get("result_date")), label, result_day))

        camp_start = parse_iso_date(camp.get("camp_start"))
        camp_end = parse_iso_date(camp.get("camp_end"))
        camp_start = camp_start or camp_end
        camp_end = camp_end or camp_start
        if camp_start and camp_end and camp_end >= base_day:
            event_day = max(camp_start, base_day)
            hint_day = today if camp_start <= today <= camp_end else camp_start
            label = EVENT_STYLE["camp"][0]
            items.append((event_day, "camp", label, format_range(camp.get("camp_start"), camp.get("camp_end")), label, hint_day))
        return items

    def inactive_summary_item(self, camp: dict, base_day: date) -> list[tuple[date, str, str, str, str, date]]:
        camp_start = parse_iso_date(camp.get("camp_start"))
        camp_end = parse_iso_date(camp.get("camp_end")) or camp_start
        camp_start = camp_start or camp_end
        if camp_start or camp_end:
            event_day = camp_start or camp_end
            label = EVENT_STYLE["camp"][0]
            return [(event_day, "camp", label, format_range(camp.get("camp_start"), camp.get("camp_end")), label, event_day)]

        result_day = parse_iso_date(camp.get("result_date"))
        if result_day:
            label = EVENT_STYLE["result"][0]
            return [(result_day, "result", label, format_date_cn(camp.get("result_date")), label, result_day)]

        signup_start = parse_iso_date(camp.get("signup_start"))
        signup_end = parse_iso_date(camp.get("signup_end")) or signup_start
        signup_start = signup_start or signup_end
        if signup_start or signup_end:
            event_day = signup_end or signup_start
            label = EVENT_STYLE["signup"][0]
            return [(event_day, "signup", label, format_range(camp.get("signup_start"), camp.get("signup_end")), label, event_day)]
        return []

    def refresh_tree(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        base_day = self.selected_date or date.today()
        rows = []
        for camp in self.camps:
            upcoming = self.upcoming_items(camp, base_day)
            if not upcoming:
                continue
            school = self.camp_display_name(camp)
            for event_day, kind, label, date_text, hint_label, hint_day in upcoming:
                rows.append((event_day, kind, label, date_text, hint_label, hint_day, camp, school))
        rows.sort(
            key=lambda row: (
                self.tree_row_sort_group(row[1], row[6].get("status")),
                row[0],
                TREE_EVENT_SORT_RANK.get(row[1], 99),
                row[7],
            )
        )
        for index, (event_day, kind, label, date_text, hint_label, hint_day, camp, school) in enumerate(rows):
            status = normalize_status(camp.get("status"))
            if status == "放弃/落选":
                tags = ("status_inactive",)
            elif kind == "pending_signup":
                tags = ("pending_signup",)
            else:
                tags = (kind,)
            values = (
                priority_label(label, camp),
                school,
                camp.get("registration_number"),
                date_text,
                safe_text(camp.get("camp_format")) or "待定",
                status,
                self.days_hint(hint_day, hint_label, date.today()),
            )
            self.tree.insert("", "end", iid=f"{camp['id']}:{kind}:{index}", values=values, tags=tags)

    def show_school_list(self) -> None:
        self.refresh_school_list()
        if self.school_list_tab is not None:
            self.notebook.select(self.school_list_tab)

    def update_school_headings(self) -> None:
        if self.school_tree is None:
            return
        school_title = "学校/学院"
        if self.school_filter_text:
            school_title = f"学校/学院：{self.school_filter_text}"
        status_title = "状态" if not self.school_filter_status else f"状态：{self.school_filter_status}"
        priority_title = "优先级" if not self.school_filter_priority else f"优先级：{self.school_filter_priority}"
        self.school_tree.heading("school", text=school_title, command=self.filter_school_text)
        self.school_tree.heading("status", text=status_title, command=self.cycle_school_status_filter)
        self.school_tree.heading("priority", text=priority_title, command=self.cycle_school_priority_filter)

    def filter_school_text(self) -> None:
        if self.school_search_bar is None:
            return
        self.school_search_var.set(self.school_filter_text)
        self.school_search_bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        for child in self.school_search_bar.winfo_children():
            if isinstance(child, ttk.Entry):
                child.focus_set()
                child.selection_range(0, "end")
                break

    def apply_school_text_filter(self) -> None:
        self.school_filter_text = self.school_search_var.get().strip()
        self.refresh_school_list()

    def clear_school_text_filter(self) -> None:
        self.school_filter_text = ""
        self.school_search_var.set("")
        if self.school_search_bar is not None:
            self.school_search_bar.grid_remove()
        self.refresh_school_list()

    def cycle_school_status_filter(self) -> None:
        options = ["", "待确认", "已报名", "已入营", "放弃/落选"]
        index = options.index(self.school_filter_status) if self.school_filter_status in options else 0
        self.school_filter_status = options[(index + 1) % len(options)]
        self.refresh_school_list()

    def cycle_school_priority_filter(self) -> None:
        options = ["", "关注", "普通"]
        index = options.index(self.school_filter_priority) if self.school_filter_priority in options else 0
        self.school_filter_priority = options[(index + 1) % len(options)]
        self.refresh_school_list()

    def school_matches_filters(self, camp: dict) -> bool:
        if self.school_filter_text:
            haystack = self.camp_display_name(camp).lower()
            if self.school_filter_text.lower() not in haystack:
                return False
        if self.school_filter_status and normalize_status(camp.get("status")) != self.school_filter_status:
            return False
        if self.school_filter_priority and normalize_priority(camp.get("priority")) != self.school_filter_priority:
            return False
        return True

    def refresh_school_list(self) -> None:
        if self.school_tree is None:
            return
        self._refreshing_school_tree = True
        try:
            self.update_school_headings()
            for item in self.school_tree.get_children():
                self.school_tree.delete(item)
            rows = sorted(
                [camp for camp in self.camps if self.school_matches_filters(camp)],
                key=lambda camp: (
                    1 if normalize_status(camp.get("status")) == "放弃/落选" else 0,
                    self.school_sort_date(camp) or date.max,
                    self.camp_display_name(camp),
                ),
            )
            for camp in rows:
                status = normalize_status(camp.get("status"))
                followup_hint = self.school_followup_hint(camp)
                tags = []
                if followup_hint:
                    tags.append("followup")
                elif status == "放弃/落选":
                    tags.append("inactive")
                elif status == "待确认":
                    tags.append("pending")
                elif is_focused(camp):
                    tags.append("focused")
                self.school_tree.insert(
                    "",
                    "end",
                    iid=str(camp["id"]),
                    values=(
                        self.camp_display_name(camp),
                        status,
                        normalize_priority(camp.get("priority")),
                        format_range(camp.get("signup_start"), camp.get("signup_end")),
                        format_date_cn(camp.get("result_date")),
                        format_range(camp.get("camp_start"), camp.get("camp_end")),
                        followup_hint,
                    ),
                    tags=tuple(tags),
                )
        finally:
            self._refreshing_school_tree = False

    def school_followup_hint(self, camp: dict, today: date | None = None) -> str:
        today = today or date.today()
        status = normalize_status(camp.get("status"))
        if status not in {"已报名", "已入营"}:
            return ""
        camp_start = parse_iso_date(camp.get("camp_start"))
        camp_end = parse_iso_date(camp.get("camp_end"))
        result_day = parse_iso_date(camp.get("result_date"))
        signup_end = parse_iso_date(camp.get("signup_end"))
        if status == "已报名" and not result_day and signup_end and signup_end < today:
            return "补录公布"
        if camp_start or camp_end:
            return ""
        if status == "已入营":
            return "补录开营"
        if result_day and result_day < today:
            return "补录开营"
        return ""

    def school_primary_date(self, camp: dict) -> date | None:
        for field in ("signup_start", "signup_end", "result_date", "camp_start", "camp_end"):
            parsed = parse_iso_date(camp.get(field))
            if parsed:
                return parsed
        return None

    def school_sort_date(self, camp: dict, today: date | None = None) -> date | None:
        today = today or date.today()
        signup_start = parse_iso_date(camp.get("signup_start"))
        signup_end = parse_iso_date(camp.get("signup_end"))
        result_day = parse_iso_date(camp.get("result_date"))
        camp_start = parse_iso_date(camp.get("camp_start"))
        camp_end = parse_iso_date(camp.get("camp_end"))
        signup_anchor = signup_start or signup_end
        camp_anchor = camp_start or camp_end

        if signup_end and signup_end >= today:
            return signup_anchor or result_day or camp_anchor
        if not signup_end and signup_start and signup_start >= today:
            return signup_start
        if result_day:
            if result_day >= today:
                return result_day
            return camp_anchor or result_day
        return signup_anchor or camp_anchor

    def on_school_tree_select(self, _event=None) -> None:
        if self._refreshing_school_tree or self.school_tree is None:
            return
        selection = self.school_tree.selection()
        if not selection:
            return
        camp_id = int(selection[0])
        self._refreshing_school_tree = True
        try:
            self.school_tree.selection_remove(selection)
        finally:
            self._refreshing_school_tree = False
        self.load_camp(camp_id)

    def tree_row_sort_group(self, kind: str, status_text: str | None) -> int:
        status = normalize_status(status_text)
        if status == "待确认" and kind == "pending_signup":
            return 0
        if status == "放弃/落选":
            return 2
        return 1

    def days_hint(self, day: date, label: str, base_day: date | None = None) -> str:
        base_day = base_day or date.today()
        delta = (day - base_day).days
        if delta == 0:
            return f"{'今天' if base_day == date.today() else '当天'} {label}"
        if delta < 0:
            return f"已过 {abs(delta)} 天 {label}"
        return f"{delta} 天后 {label}"

    def date_in_range(self, day: date, start: date | None, end: date | None) -> bool:
        start = start or end
        end = end or start
        return bool(start and end and start <= day <= end)

    def next_hint(self, camp: dict) -> str:
        today = date.today()
        candidates = [
            ("报名开始", parse_iso_date(camp.get("signup_start"))),
            ("报名截止", parse_iso_date(camp.get("signup_end"))),
            ("名单公布", parse_iso_date(camp.get("result_date"))),
            ("参营开始", parse_iso_date(camp.get("camp_start"))),
        ]
        future = [(name, day) for name, day in candidates if day and day >= today]
        if not future:
            return ""
        name, day = min(future, key=lambda item: item[1])
        delta = (day - today).days
        if delta == 0:
            return f"今天 {name}"
        return f"{delta} 天后 {name}"

    def on_tree_select(self, _event=None) -> None:
        if getattr(self, "_loading_selection", False):
            return
        selection = self.tree.selection()
        if not selection:
            return
        camp_id_text = str(selection[0]).split(":", 1)[0]
        self.load_camp(int(camp_id_text))

    def clear_form(self) -> None:
        self.selected_camp_id = None
        self.id_var.set("")
        for field, var in self.vars.items():
            if field == "status":
                var.set("待确认")
            elif field == "priority":
                var.set("普通")
            elif field == "project_type":
                var.set("硕士")
            elif field == "camp_format":
                var.set("待定")
            else:
                var.set("")
        if self.notes_text:
            self.set_notes_text("")
        self.tree.selection_remove(self.tree.selection())
        if self.form_tab is not None:
            self.notebook.select(self.form_tab)

    def load_camp(self, camp_id: int) -> None:
        camp = self.db.get(camp_id)
        if not camp:
            return
        self._loading_selection = True
        try:
            self.selected_camp_id = camp_id
            self.id_var.set(str(camp_id))
            for field in EDITABLE_FIELDS:
                if field == "notes":
                    continue
                if field == "status":
                    value = normalize_status(camp.get(field))
                elif field == "priority":
                    value = normalize_priority(camp.get(field))
                elif field == "project_type":
                    value = normalize_project_type(camp.get(field))
                else:
                    value = safe_text(camp.get(field))
                self.vars[field].set(value)
            if self.notes_text:
                self.set_notes_text(safe_text(camp.get("notes")))
            if self.form_tab is not None:
                self.notebook.select(self.form_tab)
        finally:
            self._loading_selection = False

    def read_form(self) -> dict | None:
        data = {"id": self.selected_camp_id}
        for field in EDITABLE_FIELDS:
            if field == "notes":
                data[field] = self.get_notes_value()
            else:
                data[field] = self.vars[field].get().strip()
        if not data["school"]:
            messagebox.showwarning("缺少学校名", "请至少填写学校名。", parent=self)
            return None
        data["status"] = normalize_status(data.get("status"))
        data["priority"] = normalize_priority(data.get("priority"))
        data["project_type"] = normalize_project_type(data.get("project_type"))
        data["camp_format"] = normalize_camp_format(data.get("camp_format"))
        data = expand_date_ranges(data)
        try:
            for field in DATE_FIELDS:
                data[field] = normalize_date(data[field]) if data[field] else ""
        except ValueError as exc:
            messagebox.showerror("日期格式错误", str(exc), parent=self)
            return None
        if not self.validate_ranges(data):
            return None
        return data

    def validate_ranges(self, data: dict) -> bool:
        ranges = [("报名", "signup_start", "signup_end"), ("参营", "camp_start", "camp_end")]
        for label, start_field, end_field in ranges:
            start = parse_iso_date(data.get(start_field))
            end = parse_iso_date(data.get(end_field))
            if start and end and end < start:
                messagebox.showerror("日期顺序错误", f"{label}结束日期不能早于开始日期。", parent=self)
                return False
        return True

    def save_current(self) -> None:
        if self._saving:
            return
        data = self.read_form()
        if not data:
            return
        conflicts = self.camp_conflicts_for_item(data, exclude_id=self.selected_camp_id)
        if conflicts:
            lines = []
            for day, schools in sorted(conflicts.items())[:8]:
                lines.append(f"{day.isoformat()}：{'、'.join(schools)}")
            if not messagebox.askyesno(
                "参营时间冲突",
                "当前项目的参营时间与已有项目重叠：\n\n"
                + "\n".join(lines)
                + "\n\n仍然保存吗？",
                parent=self,
            ):
                self.update_status("已取消保存")
                return
        self._saving = True
        self.update_status("正在保存...")
        try:
            camp_id = self.db.save(data)
            self.selected_camp_id = camp_id
            self.id_var.set(str(camp_id))
            self.camps = self.db.all_camps()
            self.draw_calendar()
            self.refresh_tree()
            self.refresh_school_list()
            self.update_status("已保存")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc), parent=self)
            self.update_status("保存失败")
        finally:
            self._saving = False

    def delete_current(self) -> None:
        if not self.selected_camp_id:
            messagebox.showinfo("未选择项目", "请先选择要删除的项目。", parent=self)
            return
        camp = self.db.get(self.selected_camp_id)
        name = camp.get("school") if camp else str(self.selected_camp_id)
        if not messagebox.askyesno("确认删除", f"删除“{name}”？", parent=self):
            return
        self.db.delete(self.selected_camp_id)
        self.clear_form()
        self.refresh_all()
        self.update_status("已删除")

    def open_url(self, url: str) -> None:
        url = url.strip()
        if not url:
            messagebox.showinfo("没有网址", "这个字段还没有填写网址。", parent=self)
            return
        if not is_http_url(url):
            messagebox.showinfo("不是网址", url, parent=self)
            return
        webbrowser.open(url)

    def open_personal_profile(self) -> None:
        if self.profile_text is None or self.profile_tab is None:
            return
        if PERSONAL_PROFILE_PATH.exists():
            try:
                load_rich_text(self.profile_text, PERSONAL_PROFILE_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        else:
            self.profile_text.delete("1.0", "end")
        self.notebook.add(self.profile_tab, text="个人信息")
        self.notebook.select(self.profile_tab)
        self.profile_text.focus_set()

    def save_profile_panel(self) -> None:
        if self.profile_text is None:
            return
        PERSONAL_PROFILE_PATH.write_text(dump_rich_text(self.profile_text).rstrip() + "\n", encoding="utf-8")
        self.update_status("个人信息已保存")

    def clear_profile_panel(self) -> None:
        if self.profile_text is None:
            return
        if messagebox.askyesno("确认清空", "清空个人信息备忘录？", parent=self):
            self.profile_text.delete("1.0", "end")

    def close_profile_panel(self) -> None:
        if self.profile_tab is not None:
            try:
                self.notebook.hide(self.profile_tab)
            except tk.TclError:
                pass
        if self.form_tab is not None:
            self.notebook.select(self.form_tab)

    def ensure_ai_ready(self) -> bool:
        api_url = normalize_chat_url(os.environ.get("SUMMER_CAMP_AI_API_URL") or safe_text(self.settings.get("api_url")).strip())
        api_key = (
            os.environ.get("SUMMER_CAMP_AI_API_KEY")
            or os.environ.get("DASHSCOPE_API_KEY")
            or self.runtime_api_key
            or safe_text(self.settings.get("api_key")).strip()
        )
        model = os.environ.get("SUMMER_CAMP_AI_MODEL") or safe_text(self.settings.get("model")).strip()
        missing = []
        if not api_url:
            missing.append("接口地址")
        if not model:
            missing.append("模型名")
        if not api_key:
            missing.append("API Key")
        if missing:
            messagebox.showwarning(
                "AI 设置不完整",
                "请先在“AI 设置”里填写：" + "、".join(missing) + "。\n\n接口地址需要使用 OpenAI-compatible Chat Completions 接口。",
                parent=self,
            )
            self.open_settings()
            return False
        return True

    def open_selected_links(self, _event=None) -> None:
        if not self.selected_camp_id:
            return
        camp = self.db.get(self.selected_camp_id)
        if not camp:
            return
        for field in ("notice_url", "signup_url", "result_url"):
            if camp.get(field):
                self.open_url(camp[field])
                break

    def open_tree_row_links(self, event=None) -> str | None:
        if event is not None:
            region = self.tree.identify_region(event.x, event.y)
            if region not in {"cell", "tree"}:
                return "break"
            row_id = self.tree.identify_row(event.y)
            if not row_id:
                return "break"
        self.open_selected_links()
        return "break"

    def export_csv(self) -> None:
        target = filedialog.asksaveasfilename(
            parent=self,
            title="导出 CSV",
            defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
            initialfile="夏令营日程.csv",
        )
        if not target:
            return
        fields = ["id"] + EDITABLE_FIELDS + ["created_at", "updated_at"]
        with open(target, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for camp in self.db.all_camps():
                writer.writerow({field: camp.get(field, "") for field in fields})
        self.update_status(f"已导出：{target}")

    def backup_fields(self) -> list[str]:
        return EDITABLE_FIELDS + ["created_at", "updated_at"]

    def backup_headers(self) -> dict[str, str]:
        labels = FIELD_LABELS.copy()
        labels.update({"created_at": "创建时间", "updated_at": "更新时间"})
        return {field: labels.get(field, field) for field in self.backup_fields()}

    def export_schedule(self) -> None:
        target = filedialog.asksaveasfilename(
            parent=self,
            title="导出日程",
            defaultextension=".xlsx",
            filetypes=[("Excel 工作簿", "*.xlsx"), ("CSV 文件", "*.csv")],
            initialfile=f"夏令营日程-{date.today().isoformat()}.xlsx",
        )
        if not target:
            return
        fields = self.backup_fields()
        headers = self.backup_headers()

        def export_value(camp: dict, field: str) -> str:
            value = camp.get(field, "")
            if field == "notes":
                return rich_plain_text(value)
            return value

        try:
            if target.lower().endswith(".csv"):
                with open(target, "w", encoding="utf-8-sig", newline="") as fh:
                    writer = csv.writer(fh)
                    writer.writerow([headers[field] for field in fields])
                    for camp in self.db.all_camps():
                        writer.writerow([export_value(camp, field) for field in fields])
            else:
                rows = [[headers[field] for field in fields]]
                rows.extend([export_value(camp, field) for field in fields] for camp in self.db.all_camps())
                write_simple_xlsx(target, rows)
        except Exception as exc:
            messagebox.showerror("导出日程失败", str(exc), parent=self)
            return
        self.update_status(f"已导出日程：{target}")

    def export_full_backup(self) -> None:
        target = filedialog.asksaveasfilename(
            parent=self,
            title="导出备份",
            defaultextension=".json",
            filetypes=[("JSON 备份", "*.json"), ("所有文件", "*.*")],
            initialfile=f"夏令营完整备份-{date.today().isoformat()}.json",
        )
        if not target:
            return
        try:
            personal_profile = ""
            if PERSONAL_PROFILE_PATH.exists():
                personal_profile = PERSONAL_PROFILE_PATH.read_text(encoding="utf-8")
            payload = {
                "version": 2,
                "app": APP_NAME,
                "exported_at": now_text(),
                "camps": [
                    {field: camp.get(field, "") for field in self.backup_fields()}
                    for camp in self.db.all_camps()
                ],
                "personal_profile": personal_profile,
                "settings": {field: self.settings.get(field, DEFAULT_SETTINGS[field]) for field in DEFAULT_SETTINGS},
            }
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except Exception as exc:
            messagebox.showerror("导出备份失败", str(exc), parent=self)
            return
        self.update_status(f"已导出备份：{target}")

    def import_backup(self) -> None:
        source = filedialog.askopenfilename(
            parent=self,
            title="导入备份",
            filetypes=[
                ("备份文件", "*.json *.xlsx *.csv"),
                ("JSON 备份", "*.json"),
                ("Excel 工作簿", "*.xlsx"),
                ("CSV 文件", "*.csv"),
            ],
        )
        if not source:
            return
        if not messagebox.askyesno(
            "确认导入备份",
            "导入备份会覆盖当前所有项目；JSON 完整备份还会恢复个人信息和 AI 设置。建议先导出一份当前备份。\n\n继续导入吗？",
            parent=self,
        ):
            return
        try:
            rows, restored = self.read_backup_payload(source)
            if not rows:
                raise RuntimeError("备份文件里没有可导入的数据。")
            self.db.replace_all(rows)
            if "personal_profile" in restored:
                PERSONAL_PROFILE_PATH.write_text(safe_text(restored.get("personal_profile")), encoding="utf-8")
            if "settings" in restored and isinstance(restored.get("settings"), dict):
                settings = DEFAULT_SETTINGS.copy()
                settings.update({field: restored["settings"].get(field, settings[field]) for field in DEFAULT_SETTINGS})
                self.settings = settings
                self.runtime_api_key = ""
                save_settings(settings)
            self.selected_camp_id = None
            self.clear_form()
            self.refresh_all()
        except Exception as exc:
            messagebox.showerror("导入备份失败", str(exc), parent=self)
            return
        self.update_status(f"已导入备份：{len(rows)} 条")

    def read_backup_payload(self, source: str) -> tuple[list[dict], dict]:
        if source.lower().endswith(".json"):
            with open(source, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if isinstance(payload, list):
                return self.clean_backup_rows(payload), {}
            if not isinstance(payload, dict):
                raise RuntimeError("JSON 备份格式不正确。")
            rows = payload.get("camps") or []
            if not isinstance(rows, list):
                raise RuntimeError("JSON 备份里的 camps 不是列表。")
            restored = {}
            if "personal_profile" in payload:
                restored["personal_profile"] = payload.get("personal_profile", "")
            if isinstance(payload.get("settings"), dict):
                restored["settings"] = payload["settings"]
            return self.clean_backup_rows(rows), restored
        return self.read_backup_rows(source), {}

    def clean_backup_rows(self, rows: list[dict]) -> list[dict]:
        clean_rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            mapped = {field: safe_text(row.get(field)).strip() for field in self.backup_fields()}
            if not any(mapped.get(field) for field in EDITABLE_FIELDS):
                continue
            mapped["status"] = normalize_status(mapped.get("status"))
            mapped["priority"] = normalize_priority(mapped.get("priority"))
            mapped["project_type"] = normalize_project_type(mapped.get("project_type"))
            self.normalize_imported_date_fields(mapped)
            clean_rows.append(mapped)
        return clean_rows

    def normalize_imported_date_fields(self, row: dict) -> None:
        row = expand_date_ranges(row)
        for field in DATE_FIELDS:
            value = safe_text(row.get(field)).strip()
            if not value:
                row[field] = ""
                continue
            if FUZZY_DATE_PATTERN.search(value):
                row[field] = ""
                continue
            try:
                row[field], _date_expression = normalize_date_field_value(value)
            except ValueError:
                row[field] = ""

    def read_backup_rows(self, source: str) -> list[dict]:
        fields = self.backup_fields()
        headers = self.backup_headers()
        reverse_headers = {label: field for field, label in headers.items()}
        reverse_headers.update({field: field for field in fields})
        if source.lower().endswith(".csv"):
            with open(source, "r", encoding="utf-8-sig", newline="") as fh:
                reader = csv.DictReader(fh)
                data_rows = []
                for row in reader:
                    mapped = {reverse_headers.get(key, key): value for key, value in row.items() if key}
                    if not any(mapped.get(field) for field in EDITABLE_FIELDS):
                        continue
                    mapped["status"] = normalize_status(mapped.get("status"))
                    mapped["priority"] = normalize_priority(mapped.get("priority"))
                    mapped["project_type"] = normalize_project_type(mapped.get("project_type"))
                    self.normalize_imported_date_fields(mapped)
                    data_rows.append(mapped)
                return data_rows
        rows = read_simple_xlsx(source)
        if not rows:
            return []
        header_values = [safe_text(value).strip() for value in rows[0]]
        mapped_fields = [reverse_headers.get(value, value) for value in header_values]
        data_rows: list[dict] = []
        for values in rows[1:]:
            row = {field: safe_text(value).strip() for field, value in zip(mapped_fields, values) if field}
            if any(row.get(field) for field in EDITABLE_FIELDS):
                row["status"] = normalize_status(row.get("status"))
                row["priority"] = normalize_priority(row.get("priority"))
                row["project_type"] = normalize_project_type(row.get("project_type"))
                self.normalize_imported_date_fields(row)
                data_rows.append(row)
        return data_rows

    def open_settings(self) -> None:
        SettingsDialog(self, self.settings, self.runtime_api_key, self.apply_settings)

    def apply_settings(self, settings: dict, runtime_key: str) -> None:
        self.settings = settings
        self.runtime_api_key = runtime_key
        save_settings(settings)
        self.update_status("AI 设置已保存")

    def fetch_ai_url_only(self) -> None:
        url = self.ai_url_var.get().strip()
        if not url:
            messagebox.showwarning("缺少链接", "请先填写通知链接。", parent=self)
            return

        def task(progress):
            def attempt(attempt_no: int, total: int):
                progress(f"第 {attempt_no}/{total} 次：正在抓取网页...")
                text, _page = fetch_url_text(url, int(self.settings.get("timeout_seconds") or 30), progress=progress)
                progress("网页已抓取，正在写入正文框...", text)
                return text

            text = self.retry_task(progress, attempt, task_name="网页抓取")
            return text

        def done(text: str):
            self.set_ai_text(text)
            self.update_status("网页已抓取")

        self.run_background("正在抓取网页...", task, done)

    def ai_from_url(self) -> None:
        url = self.ai_url_var.get().strip()
        if not url:
            messagebox.showwarning("缺少链接", "请先填写通知链接。", parent=self)
            return
        if not self.ensure_ai_ready():
            return

        def task(progress):
            def fetch_attempt(attempt_no: int, total: int):
                progress(f"第 {attempt_no}/{total} 次：正在抓取网页...")
                text, _page = fetch_url_text(url, int(self.settings.get("timeout_seconds") or 30), progress=progress)
                progress("网页已抓取，正在写入正文框...", text)
                return text

            text = self.retry_task(progress, fetch_attempt, task_name="网页抓取")

            def ai_attempt(attempt_no: int, total: int):
                progress(f"第 {attempt_no}/{total} 次：正在请求 AI 分析...")
                prompt = build_ai_prompt(text, url)
                raw = call_chat_completions(self.settings, self.runtime_api_key, prompt)
                progress(f"第 {attempt_no}/{total} 次：AI 已返回，正在清洗字段...")
                return sanitize_ai_data(raw, url, text)

            data = self.retry_task(progress, ai_attempt, task_name="AI 解析")
            return text, data

        def done(result):
            text, data = result
            self.set_ai_text(text)
            self.fill_form(data)
            self.update_status("AI 已识别并填入表单")

        self.run_background("正在抓取并调用 AI...", task, done)

    def ai_from_text(self) -> None:
        text = self.get_ai_text()
        url = self.ai_url_var.get().strip()
        if not text:
            messagebox.showwarning("缺少正文", "请先抓取网页或粘贴通知正文。", parent=self)
            return
        if not self.ensure_ai_ready():
            return

        def task(progress):
            def attempt(attempt_no: int, total: int):
                progress(f"第 {attempt_no}/{total} 次：正在请求 AI 分析正文...")
                prompt = build_ai_prompt(text, url)
                raw = call_chat_completions(self.settings, self.runtime_api_key, prompt)
                progress(f"第 {attempt_no}/{total} 次：AI 已返回，正在清洗字段...")
                return sanitize_ai_data(raw, url, text)

            return self.retry_task(progress, attempt, task_name="AI 解析")

        def done(data):
            self.fill_form(data)
            self.update_status("AI 已识别并填入表单")

        self.run_background("正在调用 AI...", task, done)

    def local_extract_from_text(self) -> None:
        if not self.set_ai_busy(True, "正在本地粗识别..."):
            return
        text = self.get_ai_text()
        url = self.ai_url_var.get().strip()
        if not text and not url:
            self.set_ai_busy(False)
            messagebox.showwarning("缺少内容", "请先填写链接或粘贴通知正文。", parent=self)
            return
        try:
            data = fallback_extract(text, url)
            self.fill_form(data)
            self.update_status("本地粗识别已填入表单")
        finally:
            self.set_ai_busy(False)

    def retry_task(self, progress, attempt_func, max_attempts: int = 3, task_name: str = "操作"):
        last_error: Exception | None = None
        for attempt_no in range(1, max_attempts + 1):
            try:
                return attempt_func(attempt_no, max_attempts)
            except Exception as exc:
                last_error = exc
                if attempt_no >= max_attempts:
                    break
                progress(f"{task_name}第 {attempt_no}/{max_attempts} 次失败：{exc}；准备重试...")
                time.sleep(1.2 * attempt_no)
        raise RuntimeError(f"{task_name}连续尝试 {max_attempts} 次仍失败：{last_error}") from last_error

    def fill_form(self, data: dict) -> None:
        self.clear_form()
        for field in EDITABLE_FIELDS:
            value = safe_text(data.get(field))
            if field == "notes":
                self.set_notes_text(value)
                continue
            if field == "status":
                value = normalize_status(value)
            elif field == "priority":
                value = normalize_priority(value)
            elif field == "project_type":
                value = normalize_project_type(value)
            elif field == "camp_format":
                value = normalize_camp_format(value)
            self.vars[field].set(value)
            if self.form_tab is not None:
                self.notebook.select(self.form_tab)

    def run_background(self, status: str, task, done) -> None:
        if not self.set_ai_busy(True, status):
            return

        def progress(message: str) -> None:
            self.after(0, lambda: self.update_status(message))

        def progress_with_text(message: str, text: str | None = None) -> None:
            def apply_progress():
                self.update_status(message)
                if text is not None:
                    self.set_ai_text(text)

            self.after(0, apply_progress)

        def runner():
            try:
                result = task(progress_with_text)
            except Exception as exc:
                detail = traceback.format_exc()
                error_message = str(exc)
                error_detail = detail[-1200:]

                def show_error():
                    self.set_ai_busy(False)
                    self.update_status("操作失败")
                    messagebox.showerror("操作失败", f"{error_message}\n\n{error_detail}", parent=self)

                self.after(0, show_error)
                return

            def finish_success():
                try:
                    done(result)
                finally:
                    self.set_ai_busy(False)

            self.after(0, finish_success)

        threading.Thread(target=runner, daemon=True).start()

    def on_close(self) -> None:
        self.db.close()
        self.destroy()


def run_self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = CampDatabase(Path(tmp) / "test.sqlite3")
        try:
            camp_id = db.save(
                {
                    "school": "四川大学",
                    "college": "计算机学院",
                    "notice_url": "https://example.com/notice",
                    "signup_start": normalize_date("2026年6月25日"),
                    "signup_end": normalize_date("7月3日", default_year=2026),
                    "signup_url": "https://example.com/apply",
                    "result_date": normalize_date("2026-07-03"),
                    "result_url": "https://example.com/result",
                    "camp_start": normalize_date("7.13", default_year=2026),
                    "camp_end": normalize_date("7.15", default_year=2026),
                    "camp_format": "线上或线下",
                    "camp_address": "四川大学计算机学院",
                    "status": "待确认",
                    "priority": "普通",
                    "notes": "测试",
                }
            )
            assert db.get(camp_id)["school"] == "四川大学"
            assert split_date_range("7.13-7.15", 2026) == ("2026-07-13", "2026-07-15")
            assert split_date_range("2026年7月13日至15日") == ("2026-07-13", "2026-07-15")
            assert normalize_date_field_value('6月30日左右”，result_date提取为2026-06-30', 2026)[0] == "2026-06-30"
            assert normalize_status("待报名") == "待确认"
            assert normalize_camp_format("线下活动") == "线下"
            assert normalize_camp_format("腾讯会议线上宣讲") == "线上"
            assert normalize_camp_format("形式另行通知") == "待定"
            assert status_sort_rank("放弃") > status_sort_rank("已报名")
            assert EVENT_SORT_RANK["pending_signup"] < EVENT_SORT_RANK["signup"]
            assert EVENT_SORT_RANK["signup_deadline"] < EVENT_SORT_RANK["result"]
            today = date.today()
            sanitized = sanitize_ai_data({"school": "测试大学", "signup_end": (today + timedelta(days=5)).isoformat()})
            assert sanitized["signup_start"] == today.isoformat()
            dummy_app = object.__new__(SummerCampPlanner)
            signup_items = SummerCampPlanner.upcoming_items(
                dummy_app,
                {
                    "id": 1,
                    "school": "测试大学",
                    "signup_start": today.isoformat(),
                    "signup_end": (today + timedelta(days=5)).isoformat(),
                    "status": "待确认",
                },
                today + timedelta(days=2),
            )
            assert signup_items[0][0] == today + timedelta(days=2)
            assert signup_items[0][5] == today
            camp_items = SummerCampPlanner.upcoming_items(
                dummy_app,
                {
                    "id": 2,
                    "school": "测试大学",
                    "camp_start": (today + timedelta(days=7)).isoformat(),
                    "camp_end": (today + timedelta(days=8)).isoformat(),
                    "status": "已入营",
                },
                today + timedelta(days=8),
            )
            assert camp_items[0][0] == today + timedelta(days=8)
            assert camp_items[0][5] == today + timedelta(days=7)
            parsed = extract_json_object('```json\n{"school":"四川大学","signup_end":"2026-07-03"}\n```')
            assert parsed["signup_end"] == "2026-07-03"
            text, links = html_to_text('<html><body><h1>通知</h1><a href="https://a.test">报名</a></body></html>')
            assert "通知" in text and links == ["https://a.test"]
            sysu_text = (
                "中山大学计算机学院位于广州校区东校园。"
                "1、预推免硕士生申请 仅填写问卷星https://www.wjx.cn/vm/Pw7Eq3n.aspx；仅参加7月11日线上宣讲活动；"
                "2、预推免直博生申请 需参加线下活动。1）报名时间：公布之日起至2026年6月28日24:00前；"
                "2）登录学院信息系统https://icse.sysu.edu.cn/fushi/进行网上报名；"
                "3）申请人需提交的材料内容 A、个人简历；B、成绩单。以上A-D为必须项。"
                "四、活动通知 7月3日左右，直博生申请者在报名系统查询或通过邮件方式告知；"
                "五、线下活动时间及地点 2026年7月11-12日在中山大学广州校区东校园（大学城）举行。"
            )
            sysu = fallback_extract(sysu_text, "https://cse.sysu.edu.cn/article/3565")
            assert sysu["school"] == "中山大学"
            assert sysu["college"] == "计算机学院"
            assert sysu["signup_start"] == "" and sysu["signup_end"] == "2026-06-28"
            assert sysu["signup_url"].startswith("https://www.wjx.cn")
            assert sysu["result_date"] == "2026-07-03"
            assert sysu["camp_start"] == "2026-07-11" and sysu["camp_end"] == "2026-07-11"
            assert sysu["camp_format"] == "线上" and sysu["camp_address"] == ""
            assert "暂采用直博生申请字段" in sysu["notes"]
            assert "需提交材料" not in sysu["notes"] and "直博报名时间" not in sysu["notes"]
            cleaned = sanitize_ai_data(
                {
                    "school": "测试大学",
                    "camp_format": "线下活动，到校参加",
                    "result_url": "https://cse.example.edu.cn",
                },
                "https://notice.example.edu.cn/a",
                "7月3日左右在学院官网公布入营名单，请关注后续通知。",
            )
            assert cleaned["camp_format"] == "线下"
            assert cleaned["result_url"] == "https://cse.example.edu.cn"
            promoted = promote_focus_notes("普通说明\n【重点】还需在问卷星填写")
            assert promoted.startswith("【重点】")
            formatted = normalize_notes_text("普通说明\n【重点】还需在问卷星填写\n1.\n申请材料：成绩单；2.\n审核确认：7月3日")
            assert formatted.startswith("【重点】") and "申请材料" in formatted and "2. 审核确认" in formatted
            long_note = normalize_notes_text(
                "硕士申请仅填写问卷星并参加7月11日线上宣讲活动，报名起止时间原文未明确，"
                "暂采用直博信息（公布之日起即2026-06-12至2026-06-28）。2. 入营通知时间原文为“7月3日左右”，"
                "故result_date填2026-07-03。3. 硕士"
            )
            assert "..." not in long_note and "\n  2. 入营通知" in long_note and "\n  3. 硕士" in long_note
            rich_sample = RICH_TEXT_PREFIX + json.dumps(
                {"text": "重要提醒", "spans": [{"tag": "rt_bold", "start": 0, "end": 4}]},
                ensure_ascii=False,
            )
            assert rich_plain_text(rich_sample) == "重要提醒"
            rich_size_sample = RICH_TEXT_PREFIX + json.dumps(
                {"text": "字号", "spans": [{"tag": "rt_size_20", "start": 0, "end": 2}]},
                ensure_ascii=False,
            )
            assert rich_plain_text(rich_size_sample) == "字号"
            assert compact_status_text("x" * 80).endswith("...")
        finally:
            db.close()
    print("self-test ok")


def prompt_license_renewal(parent: tk.Tk, expired_message: str) -> bool:
    dialog = tk.Toplevel(parent)
    dialog.title(APP_NAME)
    dialog.transient(parent)
    dialog.resizable(False, False)
    apply_app_icon(dialog)

    result = {"ok": False}
    key_var = tk.StringVar()
    status_var = tk.StringVar(value="请输入新的激活码以继续使用")

    container = ttk.Frame(dialog, padding=(22, 18, 22, 18))
    container.pack(fill="both", expand=True)

    ttk.Label(container, text="软件使用时间已到期", style="Title.TLabel").pack(anchor="w")
    ttk.Label(container, text=expired_message, foreground=MUTED_TEXT, wraplength=420).pack(
        anchor="w", pady=(8, 14)
    )
    entry = ttk.Entry(container, textvariable=key_var, width=58, show="")
    entry.pack(fill="x")
    ttk.Label(container, textvariable=status_var, foreground=MUTED_TEXT, wraplength=420).pack(
        anchor="w", pady=(8, 0)
    )

    button_row = ttk.Frame(container)
    button_row.pack(fill="x", pady=(16, 0))
    activate_btn = ttk.Button(button_row, text="激活并进入")
    cancel_btn = ttk.Button(button_row, text="退出")
    cancel_btn.pack(side="right")
    activate_btn.pack(side="right", padx=(0, 8))

    def close() -> None:
        dialog.destroy()

    def submit() -> None:
        key = key_var.get().strip()
        if not key:
            status_var.set("请先输入新的激活码")
            return
        activate_btn.configure(state="disabled")
        cancel_btn.configure(state="disabled")
        status_var.set("正在联网校验激活码...")
        dialog.update_idletasks()
        ok, message = activate_license(key, check_time=True)
        if ok:
            result["ok"] = True
            status_var.set(message)
            dialog.destroy()
            return
        status_var.set(message)
        activate_btn.configure(state="normal")
        cancel_btn.configure(state="normal")

    activate_btn.configure(command=submit)
    cancel_btn.configure(command=close)
    entry.bind("<Return>", lambda _event: submit())
    dialog.protocol("WM_DELETE_WINDOW", close)
    dialog.update_idletasks()
    width = max(500, dialog.winfo_reqwidth())
    height = dialog.winfo_reqheight()
    x = parent.winfo_screenwidth() // 2 - width // 2
    y = parent.winfo_screenheight() // 2 - height // 2
    dialog.geometry(f"{width}x{height}+{x}+{y}")
    entry.focus_set()
    parent.wait_window(dialog)
    return bool(result["ok"])


def main() -> None:
    if "--self-test" in sys.argv:
        run_self_test()
        return
    ok, message = validate_saved_license()
    if not ok:
        root = tk.Tk()
        root.withdraw()
        apply_app_icon(root)
        needs_activation = "密钥已过期" in message or (
            sys.platform == "darwin" and ("未找到" in message or "授权" in message)
        )
        if needs_activation:
            renewed = prompt_license_renewal(root, message)
            root.destroy()
            if not renewed:
                return
            ok, message = validate_saved_license()
            if ok:
                app = SummerCampPlanner()
                app.mainloop()
                return
            root = tk.Tk()
            root.withdraw()
            apply_app_icon(root)
        messagebox.showerror(
            APP_NAME,
            (
                "软件功能需要联网同步时间以使用网页读取与AI 服务，请联网后重新打开"
                if "无法联网同步时间" in message
                else message
            ),
            parent=root,
        )
        root.destroy()
        return
    app = SummerCampPlanner()
    app.mainloop()


if __name__ == "__main__":
    main()
