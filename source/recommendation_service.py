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
    "admission_open_time": "", "admission_close_time": "",
    "notify_before_open": True, "notify_before_unlock": True,
    "notify_admission": True, "is_initialized": False,
}
TIERS = ("冲刺", "稳妥", "保底")
STATUSES = ("idle", "locked", "admission", "admitted", "archived")
OFFER_FIELDS = ("offer_start_mode", "offer_start_time", "offer_duration_minutes")


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
    value = settings.get("admission_open_time") if fields["offer_start_mode"] == "system" else fields["offer_start_time"]
    start = parse_time(value) if value else None
    minutes = fields["offer_duration_minutes"]
    deadline = start + timedelta(minutes=minutes) if start and minutes else None
    if deadline is None and item.get("deadline"):
        deadline = parse_time(item["deadline"])
    if deadline and settings.get("admission_close_time"):
        deadline = min(deadline, parse_time(settings["admission_close_time"]))
    return start, deadline


def offer_sort_key(settings: dict, item: dict) -> tuple:
    start, deadline = offer_window(settings, item)
    minutes = item.get("offer_duration_minutes")
    return (0 if start or deadline else 1, deadline or start or datetime.max,
            minutes if minutes is not None else float("inf"), item.get("school", ""), item.get("id", ""))


def offer_time_labels(settings: dict, item: dict) -> tuple[str, str, str]:
    start, deadline = offer_window(settings, item)
    mode = item.get("offer_start_mode", "pending")
    start_text = start.strftime("%m-%d %H:%M") if start else ("系统开放时间待设置" if mode == "system" else "待通知")
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
            "contact": "", "notes": ""}


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
                         ("admission_open_time", "待录取确认开放"), ("admission_close_time", "待录取确认结束")):
        value = settings.get(field)
        if value:
            seconds = (parse_time(value) - now).total_seconds()
            if seconds > 0:
                return f"距{label}还剩：{minute_duration_text(seconds)}", seconds
    if settings.get("admission_close_time"):
        return "待录取确认时间段已结束，请核对最终录取结果。", 0
    if settings.get("admission_open_time"):
        return "待录取确认已开放；结束时间尚未设置，请关注高校确认时限。", 0
    return "志愿填报已开通；请在规则设置中补充待录取确认起止时间。", 0


def effective_admission_deadline(settings: dict, slot: dict) -> datetime:
    _, deadline = offer_window(settings, slot)
    if deadline is None:
        raise ValueError("请先设置待录取确认截止时间。")
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
        offer_fields(slot)
        if slot["status"] in ("locked", "admission", "admitted") and not slot.get("school", "").strip():
            raise ValueError("非空闲志愿必须包含学校名称。")
        if slot["status"] == "locked":
            if parse_time(slot.get("unlock_at")) <= parse_time(slot.get("submit_time")):
                raise ValueError("解锁时间必须晚于提交时间。")
        if slot["status"] == "admission":
            parse_time(slot.get("deadline"))
        if slot.get("accepted_at"):
            parse_time(slot["accepted_at"])
        admitted += slot["status"] == "admitted"
        for flag in ("unlock_warned", "admission_notified", "deadline_notified", "start_notified"):
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
        offer_fields(item)
        if "start_notified" in item and type(item["start_notified"]) is not bool:
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
                          "notified_open_time": ""}
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
                    "offer_start_time": "", "offer_duration_minutes": None}
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

    def fill_slot_from_reservoir(self, slot_index: int, reservoir_item: dict) -> bool:
        data = self.export_data()
        slot = self._slot(data, slot_index, "idle")
        now = self.clock()
        if not data["settings"]["is_initialized"]:
            raise ValueError("请先完成推免规则与时间设置。")
        if now < parse_time(data["settings"]["choice_open_time"]):
            raise ValueError("尚未到志愿填报开放时间，可先整理储备池。")
        item = next((r for r in data["reservoir_items"] if r["id"] == reservoir_item.get("id")), None)
        if item is None:
            raise ValueError("该备选志愿已不存在。")
        if any(s["status"] in ("locked", "admission") and self._identity(s) == self._identity(item) for s in data["running_slots"]):
            raise ValueError("该学校、学院和专业已在其他槽位运行。")
        slot.update({k: item.get(k, "") for k in ("school", "college", "major", "contact", "notes")})
        slot.update(offer_fields(item))
        slot.update(status="locked", reservoir_id=item["id"], submission_id=uuid4().hex,
                    submit_time=now.isoformat(timespec="seconds"),
                    unlock_at=(now + timedelta(hours=data["settings"]["lock_hours"])).isoformat(timespec="seconds"))
        data["quick_contact_notes"] = self._note_for(slot)
        self._commit(data)
        return True

    def unlock_slot_manually(self, slot_index: int) -> None:
        data = self.export_data()
        self._slot(data, slot_index, "locked")
        data["running_slots"][slot_index] = empty_slot(slot_index + 1)
        self._commit(data)

    def receive_admission(self, slot_index: int, deadline: str, *, timing: dict | None = None) -> None:
        data = self.export_data()
        slot = self._slot(data, slot_index, "locked")
        now = self.clock()
        if parse_time(slot["unlock_at"]) <= now:
            raise ValueError("该志愿锁定已到期，请刷新槽位并核对研招网状态。")
        deadline_time = parse_time(deadline)
        if deadline_time <= now:
            raise ValueError("待录取确认时限必须晚于当前时间。")
        slot.update(offer_fields(timing or {"offer_start_mode": "fixed", "offer_start_time": now.isoformat(timespec="seconds")}))
        slot.update(status="admission", deadline=deadline_time.isoformat(timespec="seconds"),
                    received_time=now.isoformat(timespec="seconds"))
        self._sync_slot_offer(data, slot)
        self._commit(data)

    def receive_admission_from_rule(self, slot_index: int, timing: dict) -> None:
        data = self.export_data()
        slot = self._slot(data, slot_index, "locked")
        timing = offer_fields(timing)
        start, deadline = offer_window(data["settings"], {**slot, **timing})
        if start is None or deadline is None:
            raise ValueError("请填写收到通知后的确认开始时间和允许时长。")
        self.receive_admission(slot_index, deadline.isoformat(timespec="seconds"), timing=timing)

    @staticmethod
    def _sync_slot_offer(data: dict, slot: dict) -> None:
        for item in data["reservoir_items"]:
            if item["id"] == slot.get("reservoir_id"):
                item.update(offer_fields(slot))
                for field in ("deadline", "accepted_at"):
                    if slot.get(field):
                        item[field] = slot[field]
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
        if effective_admission_deadline(data["settings"], slot) <= accepted:
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
                "major": item.get("major", ""), "accepted_at": item.get("accepted_at", ""), **offer_fields(item)}

    def sync_to_right_notes(self, data_dict: dict) -> dict:
        data = self.export_data()
        data["quick_contact_notes"] = self._note_for(data_dict)
        self._commit(data)
        return deepcopy(data["quick_contact_notes"])

    def save_right_note(self, school: str, contact: str, content: str, details: dict | None = None) -> None:
        data = self.export_data()
        notes = data["quick_contact_notes"]
        target = notes.get("target") or {}
        # 改写高校名称后作为独立速记保存，避免写入原来的院校。
        if school != notes.get("current_school"):
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
                    if related.get("status") == "admission" and deadline is None:
                        raise ValueError("已收到待录取的志愿须保留明确的确认开始时间及允许时长。")
                    if deadline:
                        related["deadline"] = deadline.isoformat(timespec="seconds")
                    related["deadline_notified"] = False
            if details is not None:
                for other in data["reservoir_items"]:
                    if other["id"] != reservoir_id and self._identity(other) == self._identity(selected):
                        raise ValueError("相同学校、学院和专业已在储备池中，请勿重复设置。")
        notes.update(current_school=school, current_contact=contact, content=content)
        if details is not None:
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
        for key in ("reg_open_time", "choice_open_time", "admission_open_time", "admission_close_time"):
            if settings.get(key):
                boundaries.append(parse_time(settings[key]))
        if settings["notify_before_open"]:
            boundaries.append(parse_time(settings["choice_open_time"]) - timedelta(minutes=30))
        for slot in data["running_slots"]:
            if slot["status"] == "locked":
                unlock = parse_time(slot["unlock_at"])
                if unlock <= now:
                    return 100
                boundaries.append(unlock)
                if settings["notify_before_unlock"] and not slot.get("unlock_warned"):
                    if unlock - timedelta(hours=1) <= now:
                        return 100
                    boundaries.append(unlock - timedelta(hours=1))
            elif slot["status"] == "admission":
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
        remaining = (parse_time(settings["choice_open_time"]) - now).total_seconds()
        if settings["notify_before_open"] and 0 < remaining <= 1800 and data["notified_open_time"] != settings["choice_open_time"]:
            data["notified_open_time"] = settings["choice_open_time"]
            events.append({"kind": "open", "message": "志愿填报将在 30 分钟内开放，请提前核对报名信息。"})
        for index, slot in enumerate(data["running_slots"]):
            if slot["status"] == "locked":
                remaining = (parse_time(slot["unlock_at"]) - now).total_seconds()
                if remaining <= 0:
                    data["running_slots"][index] = empty_slot(index + 1)
                    events.append({"kind": "unlocked", "slot_id": index + 1,
                                   "message": f"平行志愿 {index + 1}（{slot['school']}）已满锁定时长，已自动解锁，可填报新高校。"})
                elif settings["notify_before_unlock"] and remaining <= 3600 and not slot.get("unlock_warned"):
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
