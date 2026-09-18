"""学校状态及候补排名的共用格式，供列表、导入和持久化使用。"""
import re


def is_waitlisted(value) -> bool:
    return str(value or "").strip().startswith("候补")


def normalize_waitlist_rank(value) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if not re.fullmatch(r"[0-9]+", text) or int(text) <= 0:
        raise ValueError("候补排名应为正整数；排名未知时可留空。")
    return str(int(text))


def normalize_waitlist_fields(data: dict) -> None:
    status = str(data.get("status") or "").strip()
    if is_waitlisted(status):
        rank = data.get("waitlist_rank", "")
        if rank is None or not str(rank).strip():
            match = re.fullmatch(r"候补\s*[（(]?\s*(\d+)\s*名?\s*[）)]?", status)
            rank = match.group(1) if match else ""
        data["status"] = "候补"
        data["waitlist_rank"] = normalize_waitlist_rank(rank)
    else:
        data["waitlist_rank"] = ""


def camp_status_display(camp: dict) -> str:
    status = str(camp.get("status") or "").strip()
    if is_waitlisted(status):
        data = dict(camp)
        normalize_waitlist_fields(data)
        rank = data["waitlist_rank"]
        return f"候补{rank}名" if rank else "候补"
    return status


def camp_import_sort_key(camp: dict) -> tuple:
    status = str(camp.get("status") or "").strip()
    if status == "已入营":
        rank = 0
    elif is_waitlisted(status):
        rank = 1
    elif status in {"已中选", "优秀营员", "已入选", "入选", "拟录取"} or "优营" in status:
        rank = 2
    elif status in {"放弃/落选", "放弃", "落选", "未入营"}:
        rank = 5
    elif status == "已报名":
        rank = 3
    else:
        rank = 4
    return rank, str(camp.get("school") or ""), str(camp.get("college") or "")
