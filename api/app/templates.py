"""Built-in/private templates and flat output field validation."""

from datetime import date
from uuid import UUID, uuid5, NAMESPACE_URL

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from .models import TaskTemplate, TemplateVersion

PRESETS = [
    ("名言摘录", "提取每条名言的 text 和 author，返回 JSON 数组。", [{"name": "text", "type": "string", "required": True}, {"name": "author", "type": "string", "required": True}]),
    ("生成解读文章", "根据网页正文生成中文解读文章，使用 Markdown，包含标题、摘要、核心观点和结论。只使用原文信息，不要编造。", []),
    ("提取评论", "提取用户评论，返回 JSON 数组，包含 author、content 和可用的 rating。", []),
    ("提取价格表", "提取名称和价格，返回 JSON 数组，每项包含 name（字符串）和 price（数值）。", [{"name": "name", "type": "string", "required": True}, {"name": "price", "type": "number", "required": True}]),
    ("总结要点", "用中文总结页面，返回 JSON 对象，包含 title、summary 和 key_points（数组）。", []),
]


def seed_templates(db):
    try:
        for name, prompt, fields in PRESETS:
            ident = uuid5(NAMESPACE_URL, "crawler-platform/template/" + name)
            row = db.get(TaskTemplate, ident)
            if not row:
                row = TaskTemplate(id=ident, owner_id="system", name=name, description="内置采集模板", prompt=prompt, fields=fields, is_system=True)
                db.add(row)
                db.flush()
            if not db.query(TemplateVersion).filter(TemplateVersion.template_id == ident, TemplateVersion.version == row.version).first():
                db.add(TemplateVersion(template_id=ident, version=row.version, prompt=row.prompt, fields=row.fields))
        db.commit()
    except IntegrityError:
        db.rollback()  # Another process initialized the same deterministic templates.


def bind_template(db, task):
    config = dict(task.config or {})
    ident = config.get("template_id")
    if ident:
        template = db.get(TaskTemplate, UUID(str(ident)))
        if not template or (not template.is_system and template.owner_id != task.owner_id):
            raise HTTPException(404, "模板不存在")
        version = config.get("template_version") or template.version
        if not db.query(TemplateVersion).filter(TemplateVersion.template_id == template.id, TemplateVersion.version == version).first():
            raise HTTPException(404, "模板版本不存在")
        config["template_version"] = version
    task.config = config


def validate_output(data, fields):
    if not fields:
        return []
    records = data if isinstance(data, list) else [data]
    errors = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append(f"第 {index + 1} 条结果不是对象")
            continue
        for field in fields:
            name, kind = field["name"], field["type"]
            value = record.get(name)
            if value is None or value == "":
                if field.get("required", True):
                    errors.append(f"第 {index + 1} 条缺少必填字段 {name}")
                continue
            valid = {"string": isinstance(value, str), "number": isinstance(value, (int, float)) and not isinstance(value, bool),
                     "boolean": isinstance(value, bool), "array": isinstance(value, list), "date": False}.get(kind, False)
            if kind == "date" and isinstance(value, str):
                try:
                    date.fromisoformat(value)
                    valid = True
                except ValueError:
                    pass
            if not valid:
                errors.append(f"第 {index + 1} 条字段 {name} 应为 {kind}")
    return errors[:30]
