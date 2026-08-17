import json
from fastapi import Request
from sqlalchemy.orm import Session
from . import models


def write_audit(
    db: Session,
    *,
    actor_id: int | None,
    action: str,
    target_type: str,
    target_id: object,
    request: Request | None = None,
    result: str = "success",
    details: dict[str, object] | None = None,
) -> None:
    ip = request.client.host if request and request.client else ""
    db.add(
        models.AuditLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            ip_address=ip,
            result=result,
            details=json.dumps(details or {}, ensure_ascii=False),
        )
    )
