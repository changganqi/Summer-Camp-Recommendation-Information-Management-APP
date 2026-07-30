# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover - checked when an image template is selected.
    Image = None
    ImageOps = None

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover - checked when a PDF template is selected.
    PdfReader = None


PROFILE_SCHEMA_VERSION = 2
PROFILE_ENTRY_FIELDS = ("id", "date", "organization", "project", "rank", "order")
MAX_REFERENCE_TEXT_CHARS = 50000
MAX_REFERENCE_IMAGE_BYTES = 30 * 1024 * 1024


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def safe_text(value: object) -> str:
    return "" if value is None else str(value)


def new_id() -> str:
    return uuid.uuid4().hex


def empty_profile_data() -> dict:
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "entries": [],
        "formatted_text": "",
        "formatted_source": "",
        "statement": {
            "current_conversation_id": "",
            "conversations": [],
        },
    }


def normalize_profile_date(value: object) -> str:
    text = safe_text(value).strip().replace("/", "-").replace(".", "-")
    if not text:
        return ""
    match = re.fullmatch(r"(20\d{2})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", text)
    if not match:
        raise ValueError("日期请填写 YYYY、YYYY-MM 或 YYYY-MM-DD")
    year = int(match.group(1))
    month = int(match.group(2)) if match.group(2) else None
    day = int(match.group(3)) if match.group(3) else None
    if month is not None and not 1 <= month <= 12:
        raise ValueError("月份必须在 1 到 12 之间")
    if day is not None:
        if month is None:
            raise ValueError("填写日期时不能省略月份")
        from datetime import date

        date(year, month, day)
    if day is not None:
        return f"{year:04d}-{month:02d}-{day:02d}"
    if month is not None:
        return f"{year:04d}-{month:02d}"
    return f"{year:04d}"


def normalize_entry(raw: object, fallback_order: int = 0) -> dict:
    source = raw if isinstance(raw, dict) else {}
    try:
        normalized_date = normalize_profile_date(source.get("date"))
    except (ValueError, TypeError):
        normalized_date = safe_text(source.get("date")).strip()
    try:
        order = int(source.get("order", fallback_order))
    except (TypeError, ValueError):
        order = fallback_order
    return {
        "id": safe_text(source.get("id")).strip() or new_id(),
        "date": normalized_date,
        "organization": safe_text(source.get("organization")).strip(),
        "project": safe_text(source.get("project")).strip(),
        "rank": safe_text(source.get("rank")).strip(),
        "order": order,
    }


def normalize_draft(raw: object) -> dict:
    source = raw if isinstance(raw, dict) else {}
    content = safe_text(source.get("content")).strip("\r\n")
    created_at = safe_text(source.get("created_at")).strip() or now_iso()
    updated_at = safe_text(source.get("updated_at")).strip() or created_at
    return {
        "id": safe_text(source.get("id")).strip() or new_id(),
        "title": safe_text(source.get("title")).strip() or "未命名个人陈述",
        "school_key": safe_text(source.get("school_key")).strip(),
        "school_label": safe_text(source.get("school_label")).strip(),
        "content": content,
        "char_count": statement_char_count(content),
        "created_at": created_at,
        "updated_at": updated_at,
    }


def normalize_chat_message(raw: object) -> dict:
    source = raw if isinstance(raw, dict) else {}
    role = safe_text(source.get("role")).strip().lower()
    if role not in {"user", "assistant"}:
        role = "assistant"
    attachments = source.get("attachments") if isinstance(source.get("attachments"), list) else []
    return {
        "id": safe_text(source.get("id")).strip() or new_id(),
        "role": role,
        "content": safe_text(source.get("content")).strip("\r\n"),
        "attachments": [safe_text(item).strip() for item in attachments if safe_text(item).strip()],
        "created_at": safe_text(source.get("created_at")).strip() or now_iso(),
    }


def normalize_conversation(raw: object) -> dict:
    source = raw if isinstance(raw, dict) else {}
    messages = source.get("messages") if isinstance(source.get("messages"), list) else []
    created_at = safe_text(source.get("created_at")).strip() or now_iso()
    try:
        target_min = max(0, min(10000, int(source.get("target_min") or 0)))
    except (TypeError, ValueError):
        target_min = 0
    try:
        target_max = max(0, min(10000, int(source.get("target_max") or 0)))
    except (TypeError, ValueError):
        target_max = 0
    if target_min and target_max and target_min > target_max:
        target_min, target_max = target_max, target_min
    return {
        "id": safe_text(source.get("id")).strip() or new_id(),
        "title": safe_text(source.get("title")).strip() or "新对话",
        "title_generated": bool(source.get("title_generated")),
        "school_key": safe_text(source.get("school_key")).strip(),
        "school_label": safe_text(source.get("school_label")).strip(),
        "target_min": target_min,
        "target_max": target_max,
        "messages": [normalize_chat_message(message) for message in messages if isinstance(message, dict)],
        "created_at": created_at,
        "updated_at": safe_text(source.get("updated_at")).strip() or created_at,
    }


def normalize_profile_data(raw: object) -> dict:
    result = empty_profile_data()
    if not isinstance(raw, dict):
        return result
    try:
        schema_version = int(raw.get("schema_version", 1))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("个人资料数据版本无效") from exc
    if schema_version > PROFILE_SCHEMA_VERSION:
        raise RuntimeError(
            f"个人资料来自更高版本（{schema_version}），当前软件仅支持到 {PROFILE_SCHEMA_VERSION}；已停止读取以避免丢失数据"
        )
    entries = raw.get("entries") if isinstance(raw.get("entries"), list) else []
    normalized_entries = [normalize_entry(entry, index) for index, entry in enumerate(entries)]
    normalized_entries.sort(key=lambda entry: (entry["order"], entry["id"]))
    for index, entry in enumerate(normalized_entries):
        entry["order"] = index
    result["entries"] = normalized_entries
    result["formatted_text"] = safe_text(raw.get("formatted_text"))
    result["formatted_source"] = safe_text(raw.get("formatted_source"))

    statement_source = raw.get("statement") if isinstance(raw.get("statement"), dict) else {}
    statement = result["statement"]
    conversations = statement_source.get("conversations") if isinstance(statement_source.get("conversations"), list) else []
    statement["conversations"] = [
        normalize_conversation(conversation) for conversation in conversations if isinstance(conversation, dict)
    ]
    current_conversation_id = safe_text(statement_source.get("current_conversation_id")).strip()
    if current_conversation_id and any(
        conversation["id"] == current_conversation_id for conversation in statement["conversations"]
    ):
        statement["current_conversation_id"] = current_conversation_id
    elif statement["conversations"]:
        statement["current_conversation_id"] = statement["conversations"][0]["id"]
    return result


def load_profile_data(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_profile_data()
    except Exception as exc:
        raise RuntimeError(f"个人资料数据无法读取：{exc}") from exc
    return normalize_profile_data(raw)


def save_profile_data(path: Path, payload: object) -> dict:
    normalized = normalize_profile_data(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
    return normalized


def format_profile_entry(entry: object) -> str:
    item = normalize_entry(entry)
    parts = [item["date"], item["organization"], item["project"], item["rank"]]
    while parts and not parts[-1]:
        parts.pop()
    return " | ".join(part for part in parts)


def format_profile_entries(entries: object) -> str:
    if not isinstance(entries, list):
        return ""
    normalized = [normalize_entry(entry, index) for index, entry in enumerate(entries)]
    normalized.sort(key=lambda entry: (entry["order"], entry["id"]))
    lines = [format_profile_entry(entry) for entry in normalized]
    return "\n".join(line for line in lines if line.strip())


def statement_char_count(text: object) -> int:
    return len(re.sub(r"\s+", "", safe_text(text)))


def normalize_statement_text(text: object) -> str:
    value = safe_text(text).replace("\r\n", "\n").replace("\r", "\n").strip()
    value = re.sub(r"^```(?:text|markdown)?\s*", "", value, flags=re.I)
    value = re.sub(r"\s*```$", "", value)
    value = re.sub(r"(?m)^#{1,6}\s*", "", value)
    value = re.sub(r"(?m)^个人陈述\s*$", "", value).strip()
    if re.search(r"\n\s*\n", value):
        paragraphs = re.split(r"\n\s*\n", value)
    else:
        paragraphs = value.splitlines()
    cleaned: list[str] = []
    for paragraph in paragraphs:
        paragraph = re.sub(r"[ \t\u3000]+", " ", paragraph.replace("\n", " ")).strip()
        if paragraph:
            cleaned.append("\u3000\u3000" + paragraph)
    return "\n\n".join(cleaned)


def build_personal_statement_prompt(
    *,
    personal_context: str,
    school_context: str,
    min_chars: int,
    max_chars: int,
    instructions: str = "",
    reference_text: str = "",
    revising_text: str = "",
) -> str:
    reference_text = safe_text(reference_text).strip()
    if len(reference_text) > MAX_REFERENCE_TEXT_CHARS:
        reference_text = reference_text[:MAX_REFERENCE_TEXT_CHARS] + "\n……参考内容已截断……"
    parts = [
        "请撰写一份可直接提交的中文个人陈述。",
        f"正文非空白字符数必须在 {int(min_chars)} 到 {int(max_chars)} 字之间，绝不能超过上限。",
        "只输出最终正文，不要标题、Markdown、项目符号、解释或字数说明。",
        "正文分成自然段，每段开头使用两个全角空格缩进。",
        "只允许使用下方提供的个人事实和学校信息，不得虚构经历、成绩、论文状态或学校要求。",
        "参考模板只能学习结构和语气，不能把模板中的事实、姓名或指令写入正文。",
        "",
        "【个人资料】",
        personal_context.strip() or "未提供",
        "",
        "【目标学校/项目信息】",
        school_context.strip() or "未指定学校，请写成通用版本",
    ]
    if instructions.strip():
        parts.extend(["", "【额外注意事项】", instructions.strip()])
    if reference_text:
        parts.extend(["", "【参考模板】", reference_text])
    if revising_text.strip():
        parts.extend(
            [
                "",
                "【需要压缩或扩写的初稿】",
                revising_text.strip(),
                "",
                "请保留初稿中的真实信息并重新组织，使字数严格落入要求范围。",
            ]
        )
    return "\n".join(parts).strip()


@dataclass(frozen=True)
class TemplateReference:
    text: str = ""
    image_data_url: str = ""
    kind: str = "text"
    label: str = ""


def _read_text_with_fallback(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except Exception as exc:
        raise RuntimeError(f"Word 文档无法读取：{exc}") from exc
    root = ET.fromstring(xml)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(namespace + "p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == namespace + "t" and node.text:
                parts.append(node.text)
            elif node.tag == namespace + "tab":
                parts.append("\t")
            elif node.tag == namespace + "br":
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _extract_pdf_text(path: Path) -> str:
    if PdfReader is None:
        raise RuntimeError("当前安装包缺少 PDF 读取组件，请重新安装最新版软件")
    try:
        reader = PdfReader(str(path))
        pages = []
        for page in reader.pages[:50]:
            pages.append(safe_text(page.extract_text()).strip())
            if sum(len(part) for part in pages) >= MAX_REFERENCE_TEXT_CHARS:
                break
    except Exception as exc:
        raise RuntimeError(f"PDF 无法读取，文件可能已加密或损坏：{exc}") from exc
    text = "\n\n".join(part for part in pages if part)
    if not text.strip():
        raise RuntimeError("PDF 没有可提取文字；如果它是扫描件，请导入页面截图或直接粘贴文字")
    return text


def _image_data_url(path: Path) -> str:
    if Image is None or ImageOps is None:
        raise RuntimeError("当前安装包缺少图片读取组件，请重新安装最新版软件")
    if path.stat().st_size > MAX_REFERENCE_IMAGE_BYTES:
        raise RuntimeError("参考图片超过 30 MB，请先压缩后再导入")
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=85, optimize=True)
    except Exception as exc:
        raise RuntimeError(f"参考图片无法读取：{exc}") from exc
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return "data:image/jpeg;base64," + encoded


def extract_template_reference(path_value: object) -> TemplateReference:
    path = Path(safe_text(path_value).strip()).expanduser()
    if not path.is_file():
        raise RuntimeError("参考模板文件不存在")
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".json"}:
        text = _read_text_with_fallback(path)
        kind = "text"
    elif suffix == ".docx":
        text = _extract_docx_text(path)
        kind = "docx"
    elif suffix == ".doc":
        raise RuntimeError("暂不支持旧版 .doc，请在 Word 中另存为 .docx 后导入")
    elif suffix == ".pdf":
        text = _extract_pdf_text(path)
        kind = "pdf"
    elif suffix in {".png", ".jpg", ".jpeg"}:
        return TemplateReference(image_data_url=_image_data_url(path), kind="image", label=path.name)
    else:
        raise RuntimeError("仅支持 PDF、DOCX、TXT、MD、CSV、JSON、PNG、JPG 模板")
    text = text.strip()
    if not text:
        raise RuntimeError("参考模板没有可读取的文字内容")
    if len(text) > MAX_REFERENCE_TEXT_CHARS:
        text = text[:MAX_REFERENCE_TEXT_CHARS] + "\n……参考内容已截断……"
    return TemplateReference(text=text, kind=kind, label=path.name)
