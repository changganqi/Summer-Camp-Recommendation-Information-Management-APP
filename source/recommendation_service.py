"""推免工作台的本地业务层；不向研招网提交或修改任何数据。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
import math
import sqlite3
from uuid import uuid4
from camp_status import camp_status_display


DEFAULT_RECOMMENDATION_SETTINGS = {
    "year": "2026", "reg_open_time": "2026-09-18 09:00",
    "choice_open_time": "2026-09-21 00:00", "slot_count": 3, "lock_hours": 48,
    "choice_close_time": "", "reexam_open_time": "", "reexam_close_time": "",
    "admission_open_time": "", "admission_close_time": "",
    "notify_before_open": True, "notify_before_unlock": True,
    "notify_admission": True, "is_initialized": False,
}
TIERS = ("冲刺", "稳妥", "保底")
# unlocked 表示锁定时长已结束，但该志愿仍保留在原槽位中；主流程按钮
# 仍可接受复试通知，若要更换学校则先移除该志愿，再从空闲槽位重新填报。
STATUSES = ("idle", "locked", "unlocked", "admission", "admitted", "archived")
OFFER_FIELDS = ("offer_start_mode", "offer_start_time", "offer_duration_minutes")
STAGE_TIMING_FIELDS = ("choice_start_time", "choice_duration_minutes",
                       "reexam_start_time", "reexam_duration_minutes")


def stage_timing_fields(item: dict) -> dict:
    """Validate and normalize school-level application/re-exam windows.

    An empty start means that the corresponding system-level opening time is
    used.  The duration is optional because some schools publish a fixed
    deadline instead of an elapsed window.
    """
    result = {}
    for field in ("choice_start_time", "reexam_start_time"):
        value = item.get(field, "")
        if not isinstance(value, str):
            raise ValueError("志愿填报和复试通知开始时间必须为文本。")
        result[field] = ""
        if value.strip():
            parsed = parse_time(value)
            result[field] = parsed.isoformat(sep=" ", timespec="seconds" if parsed.second else "minutes")
    for field in ("choice_duration_minutes", "reexam_duration_minutes"):
        value = item.get(field)
        if value in ("", None):
            result[field] = None
        elif type(value) is int and 1 <= value <= 525600:
            result[field] = value
        else:
            raise ValueError("志愿填报或复试通知允许时长须为 1～525600 分钟；未知时可留空。")
    return result


def choice_window(settings: dict, item: dict) -> tuple[datetime | None, datetime | None]:
    fields = stage_timing_fields(item)
    start = parse_time(fields["choice_start_time"] or settings.get("choice_open_time"))
    deadline = start + timedelta(minutes=fields["choice_duration_minutes"]) if fields["choice_duration_minutes"] else None
    if settings.get("choice_close_time"):
        system_deadline = parse_time(settings["choice_close_time"])
        deadline = min(deadline, system_deadline) if deadline else system_deadline
    return start, deadline


def reexam_window(settings: dict, item: dict) -> tuple[datetime | None, datetime | None]:
    fields = stage_timing_fields(item)
    value = fields["reexam_start_time"] or settings.get("reexam_open_time")
    start = parse_time(value) if value else None
    deadline = start + timedelta(minutes=fields["reexam_duration_minutes"]) if start and fields["reexam_duration_minutes"] else None
    if settings.get("reexam_close_time"):
        system_deadline = parse_time(settings["reexam_close_time"])
        deadline = min(deadline, system_deadline) if deadline else system_deadline
    return start, deadline


def stage_time_labels(settings: dict, item: dict, stage: str) -> tuple[str, str, str]:
    if stage == "choice":
        start, deadline = choice_window(settings, item)
        duration = item.get("choice_duration_minutes")
    elif stage == "reexam":
        start, deadline = reexam_window(settings, item)
        duration = item.get("reexam_duration_minutes")
    else:
        raise ValueError("未知的推免流程阶段。")
    start_text = start.strftime("%m-%d %H:%M") if start else "待通知"
    if duration is None:
        duration_text = "未设置"
    else:
        hours, minutes = divmod(duration, 60)
        duration_text = (f"{hours}小时" if hours else "") + (f"{minutes}分钟" if minutes else "")
    return start_text, duration_text, deadline.strftime("%m-%d %H:%M") if deadline else "待确定"


def offer_fields(item: dict) -> dict:
    mode = item.get("offer_start_mode", "pending")
    start = item.get("offer_start_time", "")
    minutes = item.get("offer_duration_minutes")
    if mode not in ("system", "fixed", "pending") or not isinstance(start, str):
        raise ValueError("请选择有效的录取确认开始方式。")
    if mode == "fixed":
        parsed = parse_time(start)
        start = parsed.isoformat(sep=" ", timespec="seconds" if parsed.second else "minutes")
    elif start:
        start = ""
    if minutes is not None and (type(minutes) is not int or not 1 <= minutes <= 525600):
        raise ValueError("允许确认时长须为 1～525600 分钟；未知时可留空。")
    return dict(offer_start_mode=mode, offer_start_time=start, offer_duration_minutes=minutes)


def offer_window(settings: dict, item: dict) -> tuple[datetime | None, datetime | None]:
    fields = offer_fields(item)
    if fields["offer_start_mode"] == "system":
        value = settings.get("admission_open_time")
    elif fields["offer_start_mode"] == "pending":
        # 候补通常没有可预先填写的开始时刻，只有“收到通知后 N 分钟内”
        # 的有效时长。收到待录取（或进入待录取确认阶段）的时间作为
        # 内部计算起点，但只有确实填写了时限时才使用这个内部起点。
        # 没有时限的候补记录仍然是“时间待通知”，不能把用户点击接受复试
        # 的当前时间误当成待录取确认开启时间，否则刚点击就会触发报警。
        minutes = fields["offer_duration_minutes"]
        value = ((item.get("received_time") or item.get("reexam_accepted_at") or "")
                 if minutes is not None else "")
    else:
        value = fields["offer_start_time"]
    start = parse_time(value) if value else None
    minutes = fields["offer_duration_minutes"]
    deadline = start + timedelta(minutes=minutes) if start and minutes else None
    if deadline is None and item.get("deadline"):
        deadline = parse_time(item["deadline"])
    if settings.get("admission_close_time"):
        system_deadline = parse_time(settings["admission_close_time"])
        deadline = min(deadline, system_deadline) if deadline else system_deadline
    return start, deadline


def offer_sort_key(settings: dict, item: dict) -> tuple:
    start, deadline = offer_window(settings, item)
    minutes = item.get("offer_duration_minutes")
    return (0 if start or deadline else 1, deadline or start or datetime.max,
            minutes if minutes is not None else float("inf"), item.get("school", ""), item.get("id", ""))


def offer_time_labels(settings: dict, item: dict) -> tuple[str, str, str]:
    start, deadline = offer_window(settings, item)
    mode = item.get("offer_start_mode", "pending")
    start_text = start.strftime("%m-%d %H:%M") if start else ("系统开放时间待设置" if mode == "system" else ("收到通知后起算" if mode == "pending" else "待通知"))
    if mode == "pending":
        # 候补的开始时间不对外展示具体时刻，避免把内部计算起点误认为
        # 高校已经公布的固定开始时间。
        start_text = "收到通知后起算"
    if mode == "system" and start:
        start_text += "（系统开放）"
    minutes = item.get("offer_duration_minutes")
    if minutes is None:
        duration = "未设置"
    else:
        hours, mins = divmod(minutes, 60)
        duration = (f"{hours}小时" if hours else "") + (f"{mins}分钟" if mins else "")
    return start_text, duration, deadline.strftime("%m-%d %H:%M") if deadline else "待确定"


def parse_time(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is not None:
            result = result.astimezone().replace(tzinfo=None)
        return result
    except (TypeError, ValueError):
        raise ValueError("时间格式应为 YYYY-MM-DD HH:MM（可包含秒）。") from None


def duration_text(seconds: float, days: bool = False) -> str:
    seconds = max(0, math.ceil(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        day_count, hours = divmod(hours, 24)
        return f"{day_count}天 {hours}小时 {minutes}分 {seconds}秒"
    return f"{hours}小时 {minutes:02d}分 {seconds:02d}秒"


def minute_duration_text(seconds: float) -> str:
    minutes = max(0, math.ceil(seconds / 60))
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    return f"{days}天 {hours}小时 {minutes}分"


def empty_slot(slot_id: int) -> dict:
    return {"slot_id": slot_id, "status": "idle", "school": "", "college": "",
            "major": "", "submit_time": "", "unlock_at": "", "deadline": "",
            "unlocked_at": "",
            "contact": "", "notes": "", "choice_start_time": "",
            "choice_duration_minutes": None, "reexam_start_time": "",
            "reexam_duration_minutes": None, "reexam_received_time": "",
            "reexam_accepted_at": ""}


def validate_settings(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("推免设置必须是对象。")
    settings = {**DEFAULT_RECOMMENDATION_SETTINGS, **value}
    year = str(settings["year"])
    if len(year) != 4 or not year.isdigit() or not 1900 <= int(year) <= 9998:
        raise ValueError("请输入有效的四位推免年份。")
    settings["year"] = year
    for field, minimum, maximum in (("slot_count", 1, 5), ("lock_hours", 1, 8760)):
        if type(settings[field]) is not int or not minimum <= settings[field] <= maximum:
            raise ValueError(f"{'平行志愿数' if field == 'slot_count' else '锁定小时数'}应为 {minimum}～{maximum} 的整数。")
    reg = parse_time(settings["reg_open_time"])
    choice = parse_time(settings["choice_open_time"])
    if reg > choice or reg.year != int(year) or choice.year != int(year):
        raise ValueError("注册和填报时间须属于推免年份，且注册时间不能晚于填报时间。")
    choice_close = settings.get("choice_close_time", "")
    reexam_open = settings.get("reexam_open_time", "")
    reexam_close = settings.get("reexam_close_time", "")
    for field, value in (("志愿填报结束时间", choice_close), ("接受复试通知开放时间", reexam_open),
                         ("接受复试通知结束时间", reexam_close)):
        if not isinstance(value, str):
            raise ValueError(f"{field}必须为文本。")
        if value:
            parsed = parse_time(value)
            if parsed.year != int(year):
                raise ValueError(f"{field}须属于推免年份。")
    if choice_close and parse_time(choice_close) <= choice:
        raise ValueError("志愿填报结束时间必须晚于开放时间。")
    if reexam_open and parse_time(reexam_open) < choice:
        raise ValueError("接受复试通知开放时间不能早于志愿填报开放时间。")
    if reexam_close:
        if not reexam_open:
            raise ValueError("请先填写接受复试通知开放时间，再填写结束时间。")
        if parse_time(reexam_close) <= parse_time(reexam_open):
            raise ValueError("接受复试通知结束时间必须晚于开放时间。")
    admission_open = settings["admission_open_time"]
    admission_close = settings["admission_close_time"]
    if not isinstance(admission_open, str) or not isinstance(admission_close, str):
        raise ValueError("待录取确认起止时间必须为文本。")
    if admission_close and not admission_open:
        raise ValueError("请先填写待录取确认开放时间，再填写结束时间。")
    if admission_open:
        start = parse_time(admission_open)
        if start < choice or start.year != int(year):
            raise ValueError("待录取确认开放时间须属于推免年份，且不能早于志愿填报开放时间。")
        if admission_close:
            end = parse_time(admission_close)
            if end <= start or end.year != int(year):
                raise ValueError("待录取确认结束时间须属于推免年份，且必须晚于开放时间。")
    for field in ("notify_before_open", "notify_before_unlock", "notify_admission", "is_initialized"):
        if type(settings[field]) is not bool:
            raise ValueError("提醒与初始化设置必须是布尔值。")
    return settings


def timeline_countdown(settings: dict, now: datetime) -> tuple[str, float]:
    """依次展示下一个时间节点；未公布的时间不臆造默认值。"""
    for field, label in (("reg_open_time", "系统注册开放"), ("choice_open_time", "志愿填报开通"),
                         ("choice_close_time", "志愿填报截止"), ("reexam_open_time", "接受复试通知开放"),
                         ("reexam_close_time", "接受复试通知截止"),
                         ("admission_open_time", "待录取确认开放"), ("admission_close_time", "待录取确认结束")):
        value = settings.get(field)
        if value:
            seconds = (parse_time(value) - now).total_seconds()
            if seconds > 0:
                return f"距{label}还剩：{minute_duration_text(seconds)}", seconds
    if settings.get("reexam_close_time") and now >= parse_time(settings["reexam_close_time"]):
        return "接受复试通知时间段已结束，请核对高校后续安排。", 0
    if settings.get("choice_close_time") and now >= parse_time(settings["choice_close_time"]):
        return "志愿填报时间段已结束，请关注复试通知。", 0
    if settings.get("admission_close_time"):
        return "待录取确认时间段已结束，请核对最终录取结果。", 0
    if settings.get("admission_open_time"):
        return "待录取确认已开放；结束时间尚未设置，请关注高校确认时限。", 0
    if settings.get("reexam_open_time"):
        return "志愿填报已开通；请关注接受复试通知时间段。", 0
    return "志愿填报已开通；请在规则设置中补充待录取确认起止时间。", 0


def effective_admission_deadline(settings: dict, slot: dict) -> datetime:
    _, deadline = offer_window(settings, slot)
    if deadline is None:
        raise ValueError("待录取确认截止时间尚未设置。")
    return deadline


def validate_payload(value: dict) -> dict:
    """先完整校验，再写库；损坏备份不能覆盖当前数据。"""
    if not isinstance(value, dict):
        raise ValueError("推免备份必须是对象。")
    data = deepcopy(value)
    for field in ("settings", "running_slots", "reservoir_items", "quick_contact_notes"):
        if field not in data:
            raise ValueError(f"推免数据缺少 {field}。")
    data["settings"] = validate_settings(data["settings"])
    slots = data["running_slots"]
    if not isinstance(slots, list) or len(slots) != data["settings"]["slot_count"]:
        raise ValueError("推免槽位数量与设置不一致。")
    admitted = 0
    for index, slot in enumerate(slots):
        if not isinstance(slot, dict) or slot.get("slot_id") != index + 1 or slot.get("status") not in STATUSES:
            raise ValueError("推免槽位编号或状态无效。")
        for key in ("school", "college", "major", "contact", "notes"):
            if not isinstance(slot.get(key, ""), str):
                raise ValueError("志愿内容必须是文本。")
        slot.update(stage_timing_fields(slot))
        for key in ("reexam_received_time", "reexam_accepted_at"):
            if not isinstance(slot.get(key, ""), str):
                raise ValueError("复试通知时间必须为文本。")
            if slot.get(key):
                parse_time(slot[key])
        offer_fields(slot)
        if slot["status"] in ("locked", "admission", "admitted") and not slot.get("school", "").strip():
            raise ValueError("非空闲志愿必须包含学校名称。")
        if slot["status"] == "locked":
            if parse_time(slot.get("unlock_at")) <= parse_time(slot.get("submit_time")):
                raise ValueError("解锁时间必须晚于提交时间。")
        if slot["status"] == "unlocked":
            if slot.get("submit_time") and slot.get("unlock_at"):
                if parse_time(slot["unlock_at"]) <= parse_time(slot["submit_time"]):
                    raise ValueError("已解锁志愿的解锁时间必须晚于提交时间。")
            if slot.get("unlocked_at"):
                parse_time(slot["unlocked_at"])
        if slot["status"] == "admission":
            if slot.get("unlock_at"):
                parse_time(slot["unlock_at"])
            if slot.get("deadline"):
                parse_time(slot["deadline"])
            if slot.get("unlocked_at"):
                parse_time(slot["unlocked_at"])
        if slot.get("accepted_at"):
            parse_time(slot["accepted_at"])
        admitted += slot["status"] == "admitted"
        for flag in ("unlock_warned", "admission_notified", "deadline_notified", "start_notified",
                     "reexam_notified", "reexam_deadline_notified"):
            if flag in slot and type(slot[flag]) is not bool:
                raise ValueError("槽位提醒标记无效。")
    if admitted > 1 or (admitted and any(s["status"] not in ("admitted", "archived") for s in slots)):
        raise ValueError("只能接受一份待录取，其他志愿必须归档。")
    if not admitted and any(s["status"] == "archived" for s in slots):
        raise ValueError("归档槽位必须对应已经确认的唯一录取。")
    items = data["reservoir_items"]
    if not isinstance(items, list):
        raise ValueError("储备池必须是列表。")
    ids = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or item["id"] in ids:
            raise ValueError("储备志愿 ID 缺失或重复。")
        ids.add(item["id"])
        if item.get("tier") not in TIERS or not isinstance(item.get("school"), str) or not item["school"].strip():
            raise ValueError("储备志愿的学校或梯队无效。")
        for key in ("college", "major", "camp_result", "contact", "notes"):
            if not isinstance(item.get(key, ""), str):
                raise ValueError("储备志愿内容必须是文本。")
        item.update(stage_timing_fields(item))
        for key in ("reexam_received_time", "reexam_accepted_at"):
            if not isinstance(item.get(key, ""), str):
                raise ValueError("复试通知时间必须为文本。")
            if item.get(key):
                parse_time(item[key])
        offer_fields(item)
        for flag in ("start_notified", "reexam_notified", "reexam_deadline_notified"):
            if flag in item and type(item[flag]) is not bool:
                raise ValueError("储备提醒标记无效。")
        if item.get("accepted_at"):
            parse_time(item["accepted_at"])
        source_id = item.get("source_camp_id")
        if source_id is not None and (type(source_id) is not int or source_id < 1):
            raise ValueError("夏令营关联 ID 无效。")
    notes = data["quick_contact_notes"]
    if not isinstance(notes, dict) or any(not isinstance(notes.get(k, ""), str) for k in ("current_school", "current_contact", "content")):
        raise ValueError("速记本格式无效。")
    offer_fields(notes)
    notes.update(stage_timing_fields(notes))
    for key in ("reexam_received_time", "reexam_accepted_at"):
        if not isinstance(notes.get(key, ""), str):
            raise ValueError("复试通知时间必须为文本。")
        if notes.get(key):
            parse_time(notes[key])
    if not isinstance(notes.get("major", ""), str):
        raise ValueError("拟报专业必须为文本。")
    target = notes.get("target")
    if target is not None and (not isinstance(target, dict) or target.get("kind") not in ("slot", "reservoir")):
        raise ValueError("速记关联对象无效。")
    # 兼容旧速记：补齐关联志愿的专业和时间，不覆盖已有的电话或草稿。
    source = None
    if target and target["kind"] == "reservoir":
        source = next((r for r in items if r["id"] == target.get("id")), None)
    elif target and target["kind"] == "slot":
        source = next((s for s in slots if s["slot_id"] == target.get("id") and s["status"] != "idle"
                       and s.get("submission_id", "") == target.get("submission_id", "")), None)
    if source:
        for key, value in {"major": source.get("major", ""), "accepted_at": source.get("accepted_at", ""), **offer_fields(source)}.items():
            notes.setdefault(key, value)
    notified = data.setdefault("notified_open_time", "")
    if not isinstance(notified, str):
        raise ValueError("开网提醒标记无效。")
    stage_notified = data.setdefault("notified_stage_times", {})
    if not isinstance(stage_notified, dict) or any(type(value) is not bool for value in stage_notified.values()):
        raise ValueError("阶段提醒标记无效。")
    return data


class RecommendationService:
    """与夏令营共用 SQLite 连接，所有业务操作一次事务保存完整快照。"""

    def __init__(self, connection: sqlite3.Connection, clock=None):
        self.conn = connection
        self.clock = clock or datetime.now
        with self.conn:
            self.conn.execute("CREATE TABLE IF NOT EXISTS recommendation_state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
        self.load_recommendation_data()

    def load_recommendation_data(self) -> tuple[dict, list, list, dict]:
        row = self.conn.execute("SELECT payload FROM recommendation_state WHERE id=1").fetchone()
        if row:
            self._data = validate_payload(json.loads(row[0]))
            migrated = self._detach_imported_contacts(self.export_data())
            if migrated != self._data:
                self._commit(migrated)
        else:
            self._data = {"settings": deepcopy(DEFAULT_RECOMMENDATION_SETTINGS),
                          "running_slots": [empty_slot(i) for i in range(1, 4)], "reservoir_items": [],
                          "quick_contact_notes": {"current_school": "", "current_contact": "", "content": ""},
                          "notified_open_time": "", "notified_stage_times": {}}
        data = self.export_data()
        return tuple(data[k] for k in ("settings", "running_slots", "reservoir_items", "quick_contact_notes"))

    def export_data(self) -> dict:
        return deepcopy(self._data)

    def _commit(self, data: dict) -> None:
        data = validate_payload(data)
        with self.conn:
            self.conn.execute("INSERT INTO recommendation_state (id, payload) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                              (json.dumps(data, ensure_ascii=False),))
        self._data = data

    def restore_data(self, data: dict) -> None:
        self._commit(self._detach_imported_contacts(validate_payload(data)))

    def _detach_imported_contacts(self, data: dict) -> dict:
        """仅隔离旧版可确认为照搬的学校字段；手工修改内容保留。"""
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='camps'").fetchone():
            return data
        for item in data["reservoir_items"]:
            if item.get("independent_contact") or item.get("source_camp_id") is None:
                continue
            cursor = self.conn.execute("SELECT * FROM camps WHERE id=?", (item["source_camp_id"],))
            row = cursor.fetchone()
            if row is None:
                continue
            camp = dict(zip((column[0] for column in cursor.description), row))
            if camp.get("school") != item.get("school") or camp.get("college", "") != item.get("college", ""):
                continue
            copied = {}
            source_contact = camp.get("contact") or camp.get("advisor") or ""
            source_notes = camp.get("notes") or f"从夏令营导入，原优先级：{camp.get('priority', '普通')}"
            for field, source in (("contact", source_contact), ("notes", source_notes)):
                if source and item.get(field) == source:
                    item["source_" + field + "_snapshot"] = source
                    item[field] = ""
                    copied[field] = source
            item["independent_contact"] = True
            linked_slots = [s for s in data["running_slots"] if s.get("reservoir_id") == item["id"]]
            for slot in linked_slots:
                for field, source in copied.items():
                    if slot.get(field) == source:
                        slot["source_" + field + "_snapshot"] = source
                        slot[field] = ""
            notes = data["quick_contact_notes"]
            target = notes.get("target") or {}
            linked = (target.get("kind") == "reservoir" and target.get("id") == item["id"]) or (
                target.get("kind") == "slot" and any(s["slot_id"] == target.get("id") and
                s.get("submission_id", "") == target.get("submission_id", "") for s in linked_slots))
            if linked:
                for field, note_field in (("contact", "current_contact"), ("notes", "content")):
                    if field in copied and notes.get(note_field) == copied[field]:
                        notes[note_field] = ""
        return data

    @property
    def completed(self) -> bool:
        return any(s["status"] == "admitted" for s in self._data["running_slots"])

    def save_recommendation_settings(self, new_settings: dict) -> None:
        data = self.export_data()
        settings = validate_settings({**data["settings"], **new_settings})
        slots = data["running_slots"]
        if settings["year"] != data["settings"]["year"] and any(s["status"] != "idle" for s in slots):
            raise ValueError("当前仍有在跑或已确认的志愿，不能直接切换推免年份。")
        count = settings["slot_count"]
        if any(s["status"] != "idle" for s in slots[count:]):
            raise ValueError("要移除的槽位仍有志愿记录，请先完成处理；已录取或归档槽位不能删除。")
        slots = slots[:count] + [empty_slot(i + 1) for i in range(len(slots), count)]
        if self.completed:
            for slot in slots:
                if slot["status"] == "idle":
                    slot["status"] = "archived"
        data["settings"], data["running_slots"] = settings, slots
        self._commit(data)

    def save_running_slots(self) -> None:
        self._commit(self.export_data())

    def save_reservoir_items(self) -> None:
        self._commit(self.export_data())

    def add_reservoir_item(self, item: dict) -> str:
        data = self.export_data()
        item = deepcopy(item)
        item["id"] = "res_" + uuid4().hex
        item.setdefault("tier", "稳妥")
        key = self._identity(item)
        if any(self._identity(r) == key for r in data["reservoir_items"]):
            raise ValueError("相同学校、学院和专业已在储备池中。")
        data["reservoir_items"].append(item)
        self._commit(data)
        return item["id"]

    @staticmethod
    def _identity(item: dict) -> tuple:
        return tuple(str(item.get(k, "")).strip() for k in ("school", "college", "major"))

    def delete_reservoir_item(self, item_id: str) -> None:
        data = self.export_data()
        data["reservoir_items"] = [r for r in data["reservoir_items"] if r["id"] != item_id]
        self._commit(data)

    def import_camps_to_reservoir(self, selected_indices: list, tier: str, camps: list) -> int:
        if tier not in TIERS:
            raise ValueError("请选择冲刺、稳妥或保底梯队。")
        data = self.export_data()
        added = 0
        for index in selected_indices:
            if type(index) is not int or not 0 <= index < len(camps):
                raise ValueError("选中的夏令营项目已失效，请重新打开导入窗口。")
            camp = camps[index]
            item = {"id": "res_" + uuid4().hex, "source_camp_id": camp.get("id"), "tier": tier,
                    "school": camp.get("school", ""), "college": camp.get("college", "") or camp.get("project_name", ""),
                    "major": camp.get("major", ""), "camp_result": camp_status_display(camp),
                    "contact": "", "independent_contact": True,
                    "source_project_type": camp.get("project_type", ""),
                    "priority": camp.get("priority", "普通"),
                    "notes": "",
                    "offer_start_mode": "system" if camp.get("status") in ("已中选", "优秀营员", "优营", "拟录取") else "pending",
                    "offer_start_time": "", "offer_duration_minutes": None,
                    "choice_start_time": "", "choice_duration_minutes": None,
                    "reexam_start_time": "", "reexam_duration_minutes": None,
                    "reexam_received_time": "", "reexam_accepted_at": ""}
            if any(self._identity(r) == self._identity(item) or (
                    item["source_camp_id"] is not None and r.get("source_camp_id") == item["source_camp_id"]
                    and r.get("school") == item["school"] and r.get("college") == item["college"])
                   for r in data["reservoir_items"]):
                continue
            data["reservoir_items"].append(item)
            added += 1
        if added:
            self._commit(data)
        return added

    def sync_reservoir_camp_results(self, camps: list[dict]) -> bool:
        """Refresh linked reservoir statuses from the current camp table."""
        camps_by_id = {camp.get("id"): camp for camp in camps if type(camp.get("id")) is int}
        data = self.export_data()
        changed = False
        for item in data["reservoir_items"]:
            camp = camps_by_id.get(item.get("source_camp_id"))
            if camp is None:
                continue
            current = camp_status_display(camp)
            if item.get("camp_result", "") != current:
                item["camp_result"] = current
                changed = True
        if changed:
            self._commit(data)
        return changed

    @staticmethod
    def remap_camp_links(data: dict, camps: list[dict]) -> dict:
        """夏令营整库恢复会重建主键，按学校/学院/项目类型重建关联。"""
        data = deepcopy(data)
        for item in data["reservoir_items"]:
            if item.get("source_camp_id") is None:
                continue
            matches = [c for c in camps if c.get("school") == item.get("school")
                       and c.get("college", "") == item.get("college", "")
                       and (not item.get("source_project_type") or c.get("project_type") == item["source_project_type"])]
            item["source_camp_id"] = matches[0]["id"] if len(matches) == 1 else None
        return data

    def _slot(self, data: dict, index: int, status: str) -> dict:
        if self.completed:
            raise ValueError("已确认唯一待录取，推免流程已锁定，不能继续操作志愿。")
        if type(index) is not int or not 0 <= index < len(data["running_slots"]):
            raise ValueError("志愿槽位不存在。")
        slot = data["running_slots"][index]
        if slot["status"] != status:
            raise ValueError("该志愿状态已变化，请刷新后重试。")
        return slot

    def _slot_in(self, data: dict, index: int, statuses: tuple[str, ...]) -> dict:
        """Return a live slot whose status is one of ``statuses``."""
        if self.completed:
            raise ValueError("已确认唯一待录取，推免流程已锁定，不能继续操作志愿。")
        if type(index) is not int or not 0 <= index < len(data["running_slots"]):
            raise ValueError("志愿槽位不存在。")
        slot = data["running_slots"][index]
        if slot["status"] not in statuses:
            raise ValueError("该志愿状态已变化，请刷新后重试。")
        return slot

    def fill_slot_from_reservoir(self, slot_index: int, reservoir_item: dict) -> bool:
        data = self.export_data()
        slot = data["running_slots"][slot_index] if type(slot_index) is int and 0 <= slot_index < len(data["running_slots"]) else None
        if slot is None or slot["status"] != "idle":
            raise ValueError("请先点击当前志愿右下角的“移除”，再在空闲槽位中重新填报。")
        now = self.clock()
        if not data["settings"]["is_initialized"]:
            raise ValueError("请先完成推免规则与时间设置。")
        if now < parse_time(data["settings"]["choice_open_time"]):
            raise ValueError("尚未到志愿填报开放时间，可先整理储备池。")
        item = next((r for r in data["reservoir_items"] if r["id"] == reservoir_item.get("id")), None)
        if item is None:
            raise ValueError("该备选志愿已不存在。")
        choice_start, choice_deadline = choice_window(data["settings"], item)
        if choice_start and now < choice_start:
            raise ValueError("该高校尚未到志愿填报开放时间，请按学校通知操作。")
        if choice_deadline and now >= choice_deadline:
            raise ValueError("该高校志愿填报时限已结束，请核对学校通知。")
        if any(s["status"] in ("locked", "admission") and self._identity(s) == self._identity(item) for s in data["running_slots"]):
            raise ValueError("该学校、学院和专业已在其他槽位运行。")
        slot.update({k: item.get(k, "") for k in ("school", "college", "major", "contact", "notes")})
        slot.update(stage_timing_fields(item))
        slot.update(offer_fields(item))
        slot.update(status="locked", reservoir_id=item["id"], submission_id=uuid4().hex,
                    submit_time=now.isoformat(timespec="seconds"),
                    unlock_at=(now + timedelta(hours=data["settings"]["lock_hours"])).isoformat(timespec="seconds"),
                    unlocked_at="", deadline="", accepted_at="", received_time="",
                    reexam_received_time="", reexam_accepted_at="",
                    unlock_warned=False, admission_notified=False,
                    deadline_notified=False, start_notified=False,
                    reexam_notified=False, reexam_deadline_notified=False)
        data["quick_contact_notes"] = self._note_for(slot)
        self._commit(data)
        return True

    def unlock_slot_manually(self, slot_index: int) -> None:
        """清空一个志愿槽位（用户主动拒绝/放弃）。

        这个历史方法名容易和“锁定时间结束后的自动解锁”混淆。自动解锁
        由 :meth:`_mark_slot_unlocked` 处理，会保留学校、专业和联络记录；
        这里只保留“拒绝后移除志愿”的旧行为，界面上的拒绝按钮应使用
        ``remove_slot`` 这个语义更明确的别名。
        """
        self.remove_slot(slot_index)

    def remove_slot(self, slot_index: int) -> None:
        """用户主动拒绝/放弃当前志愿并释放槽位。"""
        data = self.export_data()
        if type(slot_index) is not int or not 0 <= slot_index < len(data["running_slots"]):
            raise ValueError("志愿槽位不存在。")
        if data["running_slots"][slot_index]["status"] not in ("locked", "unlocked"):
            raise ValueError("该志愿当前不能清空。")
        data["running_slots"][slot_index] = empty_slot(slot_index + 1)
        self._commit(data)

    def _mark_slot_unlocked(self, slot: dict, now: datetime) -> None:
        """将到期志愿变为绿色状态，并保留原志愿内容。"""
        if slot.get("status") != "locked":
            return
        slot["status"] = "unlocked"
        slot["unlocked_at"] = now.isoformat(timespec="seconds")

    def receive_admission(self, slot_index: int, deadline: str | None, *, timing: dict | None = None) -> None:
        data = self.export_data()
        slot = self._slot(data, slot_index, "locked")
        # 配置了复试通知阶段时，必须先在研招网接受复试通知，才能登记待录取。
        has_reexam_stage = bool(data["settings"].get("reexam_open_time") or
                                data["settings"].get("reexam_close_time") or
                                slot.get("reexam_start_time") or
                                slot.get("reexam_duration_minutes"))
        if has_reexam_stage and not slot.get("reexam_accepted_at"):
            raise ValueError("请先接受复试通知，再登记待录取。")
        now = self.clock()
        if parse_time(slot["unlock_at"]) <= now:
            raise ValueError("该志愿锁定已到期，请刷新槽位并核对研招网状态。")
        deadline_time = parse_time(deadline) if deadline else None
        if deadline_time and deadline_time <= now:
            raise ValueError("待录取确认时限必须晚于当前时间。")
        slot.update(offer_fields(timing or {"offer_start_mode": "fixed", "offer_start_time": now.isoformat(timespec="seconds")}))
        slot.update(status="admission", deadline=deadline_time.isoformat(timespec="seconds") if deadline_time else "",
                    received_time=now.isoformat(timespec="seconds"))
        self._sync_slot_offer(data, slot)
        self._commit(data)

    def receive_admission_from_rule(self, slot_index: int, timing: dict) -> None:
        data = self.export_data()
        slot = self._slot(data, slot_index, "locked")
        timing = offer_fields(timing)
        now = self.clock()
        candidate = {**slot, **timing, "received_time": now.isoformat(timespec="seconds")}
        start, deadline = offer_window(data["settings"], candidate)
        self.receive_admission(slot_index, deadline.isoformat(timespec="seconds") if deadline else None, timing=timing)

    def receive_reexam_notice(self, slot_index: int, timing: dict | None = None) -> None:
        """记录高校发来的复试通知窗口。

        时间窗口是可选的：用户可以只点击“接受复试通知”，由系统记录
        当前时刻；只有在右侧主动填写了开始/截止信息时，才进行时间核验
        和后续提醒。
        """
        data = self.export_data()
        slot = self._slot_in(data, slot_index, ("locked", "unlocked"))
        timing = timing or {}
        start = timing.get("reexam_start_time", slot.get("reexam_start_time", ""))
        duration = timing.get("reexam_duration_minutes", slot.get("reexam_duration_minutes"))
        fields = stage_timing_fields({"reexam_start_time": start, "reexam_duration_minutes": duration})
        start_time, deadline = reexam_window(data["settings"], {**slot, **fields})
        if deadline and deadline <= self.clock():
            raise ValueError("接受复试通知截止时间必须晚于当前时间。")
        slot.update(fields, reexam_received_time=self.clock().isoformat(timespec="seconds"),
                    reexam_notified=False, reexam_deadline_notified=False)
        self._commit(data)

    def confirm_reexam(self, slot_index: int, confirmed_at: str | None = None) -> None:
        data = self.export_data()
        slot = self._slot_in(data, slot_index, ("locked", "unlocked"))
        confirmed = parse_time(confirmed_at) if confirmed_at else self.clock()
        start, deadline = reexam_window(data["settings"], slot)
        if start and confirmed < start:
            raise ValueError("尚未到接受复试通知开始时间。")
        if deadline and confirmed >= deadline:
            raise ValueError("当前不在接受复试通知时间段内。")
        slot["reexam_accepted_at"] = confirmed.isoformat(timespec="seconds")
        self._commit(data)

    def accept_reexam(self, slot_index: int, timing: dict | None = None,
                      accepted_at: str | None = None) -> None:
        """直接接受复试通知并进入待录取确认阶段。

        界面默认使用当前时间，不弹出时间录入窗口。右侧填写的复试窗口
        仍会参与核验和提醒；没有填写时只记录操作时间，不强制虚构截止
        时间。
        """
        now = parse_time(accepted_at) if accepted_at else self.clock()
        self.receive_reexam_notice(slot_index, timing)
        self.confirm_reexam(slot_index, now.isoformat(timespec="seconds"))
        data = self.export_data()
        slot = self._slot_in(data, slot_index, ("locked", "unlocked"))
        slot.update(status="admission", received_time=now.isoformat(timespec="seconds"))
        _, deadline = offer_window(data["settings"], slot)
        slot["deadline"] = deadline.isoformat(timespec="seconds") if deadline else ""
        slot["admission_notified"] = False
        slot["deadline_notified"] = False
        self._sync_slot_offer(data, slot)
        self._commit(data)

    @staticmethod
    def _sync_slot_offer(data: dict, slot: dict) -> None:
        for item in data["reservoir_items"]:
            if item["id"] == slot.get("reservoir_id"):
                item.update(offer_fields(slot))
                for field in ("deadline", "accepted_at"):
                    item[field] = slot.get(field, "")
        notes = data["quick_contact_notes"]
        target = notes.get("target") or {}
        if (target.get("kind") == "slot" and target.get("id") == slot["slot_id"]) or (
                target.get("kind") == "reservoir" and target.get("id") == slot.get("reservoir_id")):
            notes.update(offer_fields(slot))
            notes["accepted_at"] = slot.get("accepted_at", "")

    def accept_admission(self, slot_index: int, accepted_at: str | None = None) -> None:
        data = self.export_data()
        slot = self._slot(data, slot_index, "admission")
        accepted = parse_time(accepted_at) if accepted_at else self.clock()
        if accepted_at and accepted > self.clock():
            raise ValueError("实际接受时间不能晚于当前时间。")
        start, _ = offer_window(data["settings"], slot)
        system_start = data["settings"].get("admission_open_time")
        if (start and accepted < start) or (system_start and accepted < parse_time(system_start)):
            raise ValueError("尚未到待录取确认开放时间。")
        if accepted_at and slot.get("received_time") and accepted < parse_time(slot["received_time"]).replace(second=0, microsecond=0):
            raise ValueError("实际接受时间不能早于收到待录取的时间。")
        try:
            admission_deadline = effective_admission_deadline(data["settings"], slot)
        except ValueError:
            admission_deadline = None
        if admission_deadline and admission_deadline <= accepted:
            raise ValueError("待录取确认时限已过，请先联系高校核实或更新时限。")
        slot.update(status="admitted", accepted_at=accepted.isoformat(timespec="seconds"))
        for index, other in enumerate(data["running_slots"]):
            if index != slot_index:
                other.update(status="archived", archived_at=slot["accepted_at"], archive_reason="已确认其他高校唯一待录取")
        self._sync_slot_offer(data, slot)
        self._commit(data)

    def update_admission_deadline(self, slot_index: int, deadline: str) -> None:
        data = self.export_data()
        slot = self._slot(data, slot_index, "admission")
        parsed = parse_time(deadline)
        if parsed <= self.clock():
            raise ValueError("新的确认时限必须晚于当前时间。")
        slot.update(deadline=parsed.isoformat(timespec="seconds"), deadline_notified=False)
        slot["offer_duration_minutes"] = None
        self._sync_slot_offer(data, slot)
        self._commit(data)

    def reject_admission(self, slot_index: int) -> None:
        data = self.export_data()
        self._slot(data, slot_index, "admission")
        data["running_slots"][slot_index] = empty_slot(slot_index + 1)
        self._commit(data)

    @staticmethod
    def _note_for(item: dict) -> dict:
        if "slot_id" in item:
            target = {"kind": "slot", "id": item["slot_id"], "submission_id": item.get("submission_id", ""),
                      "submit_time": item.get("submit_time", "")}
        else:
            target = {"kind": "reservoir", "id": item["id"]}
        return {"current_school": f"{item.get('school', '')} ({item.get('college', '')})",
                "current_contact": item.get("contact", ""), "content": item.get("notes", ""), "target": target,
                "major": item.get("major", ""), "accepted_at": item.get("accepted_at", ""),
                **stage_timing_fields(item), **offer_fields(item)}

    def sync_to_right_notes(self, data_dict: dict) -> dict:
        data = self.export_data()
        data["quick_contact_notes"] = self._note_for(data_dict)
        self._commit(data)
        return deepcopy(data["quick_contact_notes"])

    def save_right_note(self, school: str, contact: str, content: str, details: dict | None = None,
                        target_override: dict | None = None) -> None:
        data = self.export_data()
        notes = data["quick_contact_notes"]
        target = deepcopy(target_override) if target_override else (notes.get("target") or {})
        # 改写高校名称后作为独立速记保存，避免写入原来的院校。
        if target_override is None and school != notes.get("current_school"):
            target = {}
            notes.pop("target", None)
        selected = None
        if target.get("kind") == "reservoir":
            selected = next((r for r in data["reservoir_items"] if r["id"] == target.get("id")), None)
        elif target.get("kind") == "slot":
            selected = next((s for s in data["running_slots"] if s["slot_id"] == target.get("id")
                             and s["status"] != "idle" and s.get("submission_id", "") == target.get("submission_id", "")
                             and s.get("submit_time", "") == target.get("submit_time", "")), None)
        changes = {"contact": contact, "notes": content, "independent_contact": True}
        if details is not None:
            if not isinstance(details.get("major", ""), str):
                raise ValueError("拟报专业必须为文本。")
            changes.update(stage_timing_fields(details))
            changes.update(offer_fields(details), major=details.get("major", "").strip())
        if selected is not None:
            reservoir_id = selected.get("reservoir_id") if target["kind"] == "slot" else selected["id"]
            records = [selected] + [r for r in data["reservoir_items"] + data["running_slots"]
                                   if r is not selected and reservoir_id and
                                   (r.get("id") == reservoir_id or r.get("reservoir_id") == reservoir_id)]
            for related in records:
                timing_changed = details is not None and offer_fields(related) != offer_fields(details)
                if related.get("status") in ("admitted", "archived") and (
                        timing_changed or (details is not None and changes["major"] != related.get("major", ""))):
                    raise ValueError("已完成或归档志愿的专业和确认时间不可更改，联络记录仍可保存。")
                related.update(changes)
                if timing_changed:
                    related.pop("deadline", None)
                    _, deadline = offer_window(data["settings"], related)
                    if deadline:
                        related["deadline"] = deadline.isoformat(timespec="seconds")
                    # 修改确认时间后应重新等待新的开始节点；这也能清理
                    # 旧版本把“收到复试通知时间”误判为开始时间时留下的
                    # admission_notified 标记。
                    related["admission_notified"] = False
                    related["start_notified"] = False
                    related["deadline_notified"] = False
            if details is not None:
                for other in data["reservoir_items"]:
                    if other["id"] != reservoir_id and self._identity(other) == self._identity(selected):
                        raise ValueError("相同学校、学院和专业已在储备池中，请勿重复设置。")
        notes.update(current_school=school, current_contact=contact, content=content)
        if details is not None:
            notes.update(stage_timing_fields(details))
            notes.update(offer_fields(details), major=changes["major"])
        self._commit(data)

    def next_tick_delay_ms(self, now: datetime | None = None) -> int:
        """平时每分钟唤醒；遇到开放、提醒或到期边界时精确唤醒一次。"""
        now = now or self.clock()
        data = self._data
        settings = data["settings"]
        delay = 60 - now.second - now.microsecond / 1_000_000
        if self.completed or not settings["is_initialized"]:
            return max(100, math.ceil(delay * 1000))
        boundaries = []
        for key in ("reg_open_time", "choice_open_time", "choice_close_time", "reexam_open_time",
                    "reexam_close_time", "admission_open_time", "admission_close_time"):
            if settings.get(key):
                boundaries.append(parse_time(settings[key]))
        if settings["notify_before_open"]:
            boundaries.append(parse_time(settings["choice_open_time"]) - timedelta(minutes=30))
        for slot in data["running_slots"]:
            # 48 小时锁定从填报开始计算，接受复试后变成红色 admission
            # 也不能停止这条计时线。红色卡片到期时保持红色显示，只记录
            # unlocked_at；黄色 locked 卡片才切换成绿色 unlocked。
            if slot["status"] in ("locked", "admission") and slot.get("unlock_at"):
                unlock = parse_time(slot["unlock_at"])
                if unlock <= now and not slot.get("unlocked_at"):
                    return 100
                if unlock > now:
                    boundaries.append(unlock)
                if slot["status"] == "locked" and settings["notify_before_unlock"] and not slot.get("unlock_warned"):
                    if unlock - timedelta(hours=1) <= now:
                        return 100
                    boundaries.append(unlock - timedelta(hours=1))
            if slot["status"] == "admission":
                start, deadline = offer_window(settings, slot)
                if settings["notify_admission"]:
                    if start:
                        if start <= now and (deadline is None or now < deadline) and not slot.get("admission_notified"):
                            return 100
                        if start > now:
                            boundaries.append(start)
                    if deadline:
                        warning_at = deadline - timedelta(minutes=5)
                        if now >= deadline and not slot.get("deadline_notified"):
                            return 100
                        if warning_at <= now < deadline and not slot.get("deadline_notified"):
                            return 100
                        if warning_at > now and not slot.get("deadline_notified"):
                            boundaries.append(warning_at)
                if deadline and deadline > now:
                    # 截止时间本身始终是计时器边界，即使用户关闭了弹窗提醒，
                    # 倒计时和可接受状态也必须在截止时刻更新。
                    boundaries.append(deadline)
        # 扫描各志愿与储备高校设定的特定确认录取开启时间（仅未来时间点纳入边界）
        for item in [s for s in data["running_slots"] if s["status"] == "locked"] + data["reservoir_items"]:
            for start, deadline in (choice_window(settings, item), reexam_window(settings, item)):
                for boundary in (start, deadline, deadline - timedelta(minutes=5) if deadline else None):
                    if boundary and boundary > now:
                        boundaries.append(boundary)
            if item.get("school") and not item.get("start_notified") and item.get("offer_start_mode") == "fixed" and item.get("offer_start_time"):
                start = parse_time(item["offer_start_time"])
                if start > now:
                    boundaries.append(start)
        for boundary in boundaries:
            seconds = (boundary - now).total_seconds()
            if seconds > 0:
                delay = min(delay, seconds)
        return max(100, math.ceil(delay * 1000))

    def tick(self, now: datetime | None = None) -> list[dict]:
        now = now or self.clock()
        data = self.export_data()
        settings = data["settings"]
        if not settings["is_initialized"] or self.completed:
            return []
        events = []
        stage_flags = data.setdefault("notified_stage_times", {})
        for key, label in (("choice_open_time", "志愿填报"), ("choice_close_time", "志愿填报截止"),
                           ("reexam_open_time", "接受复试通知"), ("reexam_close_time", "接受复试通知截止"),
                           ("admission_open_time", "待录取确认"), ("admission_close_time", "待录取确认截止")):
            value = settings.get(key)
            if not value:
                continue
            if key in ("admission_open_time", "admission_close_time"):
                continue
            point = parse_time(value)
            marker = f"{key}:{value}"
            if now >= point and not stage_flags.get(marker + ":start"):
                stage_flags[marker + ":start"] = True
                if key not in ("choice_open_time",):
                    events.append({"kind": "stage_start", "message": f"{label}时间已到，请按研招网和高校通知操作。"})
            if key.endswith("close_time"):
                warning = point - timedelta(minutes=5)
                if warning <= now < point and not stage_flags.get(marker + ":warning"):
                    stage_flags[marker + ":warning"] = True
                    events.append({"kind": "stage_deadline", "message": f"{label}将在 5 分钟后截止，请尽快完成操作。"})
        remaining = (parse_time(settings["choice_open_time"]) - now).total_seconds()
        if settings["notify_before_open"] and 0 < remaining <= 1800 and data["notified_open_time"] != settings["choice_open_time"]:
            data["notified_open_time"] = settings["choice_open_time"]
            events.append({"kind": "open", "message": "志愿填报将在 30 分钟内开放，请提前核对报名信息。"})
        for index, slot in enumerate(data["running_slots"]):
            # 志愿填报后的 48 小时锁定对黄色、红色卡片都有效。红色卡片
            # 到期后不改成绿色，继续保留待录取确认流程，只记录解锁时刻。
            if slot["status"] in ("locked", "admission") and slot.get("unlock_at"):
                unlock_remaining = (parse_time(slot["unlock_at"]) - now).total_seconds()
                if unlock_remaining <= 0 and not slot.get("unlocked_at"):
                    slot["unlocked_at"] = now.isoformat(timespec="seconds")
                    if slot["status"] == "locked":
                        self._mark_slot_unlocked(slot, now)
                        events.append({"kind": "unlocked", "slot_id": index + 1,
                                       "message": f"平行志愿 {index + 1}（{slot['school']}）已满锁定时长，已解锁并保留在原志愿位，可修改后重新提交。"})

            if slot["status"] == "locked":
                remaining = (parse_time(slot["unlock_at"]) - now).total_seconds()
                if remaining > 0 and settings["notify_before_unlock"] and remaining <= 3600 and not slot.get("unlock_warned"):
                    slot["unlock_warned"] = True
                    events.append({"kind": "unlock_soon", "message": f"平行志愿 {index + 1}（{slot['school']}）将在 1 小时内解锁，建议致电招生办。"})
            elif slot["status"] == "admission":
                start, deadline = offer_window(settings, slot)
                if settings["notify_admission"]:
                    common = {
                        "school": slot.get("school", ""),
                        "college": slot.get("college", ""),
                        "major": slot.get("major", ""),
                        "contact": slot.get("contact", ""),
                        "deadline": deadline.isoformat(timespec="seconds") if deadline else slot.get("deadline", ""),
                    }
                    # 录取确认开始前不报警；系统开放时间/高校指定时间到达后才报警。
                    if start and now >= start and (deadline is None or now < deadline) and not slot.get("admission_notified"):
                        slot["admission_notified"] = True
                        events.append({
                            **common,
                            "kind": "admission",
                            "message": f"{slot['school']} 的待录取确认已开始，请尽快前往研招网确认。只能接受一份待录取。",
                        })
                    if deadline and not slot.get("deadline_notified"):
                        warning_at = deadline - timedelta(minutes=5)
                        if warning_at <= now < deadline:
                            slot["deadline_notified"] = True
                            events.append({
                                **common,
                                "kind": "deadline",
                                "message": f"{slot['school']} 的待录取确认将在 5 分钟后截止，请立即前往研招网确认。",
                            })
                        elif now >= deadline:
                            # 软件未运行时可能错过五分钟提醒，此时补发一次过期提示。
                            slot["deadline_notified"] = True
                            events.append({
                                **common,
                                "kind": "deadline",
                                "message": f"{slot['school']} 的待录取确认时限已过，请联系高校核实。",
                            })
        # 扫描是否有指定了固定开启时间的高校到达了设定的确认录取时间（如14:00）
        if settings.get("notify_admission", True):
            running_res_ids = {s.get("reservoir_id") for s in data["running_slots"] if s.get("reservoir_id")}
            targets = [s for s in data["running_slots"] if s["status"] == "locked"] + [
                r for r in data["reservoir_items"] if r["id"] not in running_res_ids
            ]
            for item in targets:
                if not item.get("school") or item.get("start_notified") or item.get("offer_start_mode") != "fixed" or not item.get("offer_start_time"):
                    continue
                start = parse_time(item["offer_start_time"])
                if now >= start:
                    item["start_notified"] = True
                    events.append({
                        "kind": "offer_start",
                        "school": item.get("school", ""),
                        "college": item.get("college", ""),
                        "major": item.get("major", ""),
                        "contact": item.get("contact", ""),
                        "start_time": item["offer_start_time"],
                        "deadline": item.get("deadline", ""),
                        "message": f"【高校待录取开启】{item['school']} 已到达预定确认录取时间，通道现已开启！请尽快前往研招网推免系统确认！"
                    })
        if data != self._data:
            self._commit(data)
        return events
