from sqlalchemy import select
from sqlalchemy.orm import Session
from . import models


PERMISSIONS = {
    "users.create": "Create users",
    "users.disable": "Disable users",
    "users.reset_password": "Reset user passwords",
    "spaces.create": "Create spaces",
    "spaces.update": "Update spaces",
    "spaces.delete": "Delete spaces",
    "spaces.members.manage": "Manage space members",
    "channels.create": "Create channels",
    "channels.update": "Update channels",
    "channels.delete": "Delete channels",
    "channels.members.manage": "Manage channel members",
    "voice.join": "Join voice channels",
    "voice.speak": "Speak in voice channels",
    "voice.mute_self": "Mute self",
    "voice.deafen_self": "Deafen self",
    "voice.moderate": "Moderate voice",
    "text.read": "Read text channels",
    "text.send": "Send messages",
    "text.edit_own": "Edit own messages",
    "text.delete_own": "Delete own messages",
    "text.delete_any": "Delete any message",
    "screen_share.start": "Start screen sharing",
    "screen_share.view": "View screen sharing",
    "audit.view": "View audit log",
    "settings.manage": "Manage settings",
}

ADMIN_PERMISSIONS = set(PERMISSIONS)
USER_PERMISSIONS = {
    "voice.join",
    "voice.speak",
    "voice.mute_self",
    "voice.deafen_self",
    "text.read",
    "text.send",
    "text.edit_own",
    "text.delete_own",
    "screen_share.view",
}


def seed_permissions(db: Session) -> None:
    for key, desc in PERMISSIONS.items():
        if not db.get(models.Permission, key):
            db.add(models.Permission(key=key, description=desc))
    for role_name, keys in {"Owner/System Admin": ADMIN_PERMISSIONS, "User": USER_PERMISSIONS}.items():
        role = db.scalar(select(models.Role).where(models.Role.name == role_name))
        if not role:
            role = models.Role(name=role_name)
            db.add(role)
            db.flush()
        existing = {rp.permission_key for rp in role.permissions}
        for key in keys - existing:
            db.add(models.RolePermission(role_id=role.id, permission_key=key))
    db.commit()


def user_permissions(db: Session, user_id: int) -> set[str]:
    rows = db.execute(
        select(models.RolePermission.permission_key)
        .join(models.UserRole, models.UserRole.role_id == models.RolePermission.role_id)
        .where(models.UserRole.user_id == user_id)
    )
    return {row[0] for row in rows}


def has_permission(db: Session, user_id: int, permission: str) -> bool:
    return permission in user_permissions(db, user_id)
