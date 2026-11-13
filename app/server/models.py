from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="active")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    # The owner cannot be disabled or have the password reset by other administrators.
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Small PNG/JPEG picture set in the profile; the version changes with every new picture (cache key).
    avatar: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    avatar_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Which edition of the privacy policy, terms and data processing consent the person accepted, and when.
    consent_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    roles: Mapped[list["UserRole"]] = relationship(cascade="all, delete-orphan")


class TemporaryCredential(Base):
    __tablename__ = "temporary_credentials"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LoginSession(Base):
    """One signed-in device. Refresh tokens rotate on every use; reusing an old one revokes the user's sessions."""

    __tablename__ = "login_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    previous_token_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_fingerprint: Mapped[str] = mapped_column(String(64))
    device_name: Mapped[str] = mapped_column(String(120), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Invite(Base):
    """One-time sign-up code. Only its hash is stored."""

    __tablename__ = "invites"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    space_id: Mapped[int | None] = mapped_column(ForeignKey("spaces.id"), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(120), default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    permissions: Mapped[list["RolePermission"]] = relationship(cascade="all, delete-orphan")


class Permission(Base):
    __tablename__ = "permissions"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    description: Mapped[str] = mapped_column(String(255), default="")


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), primary_key=True)
    permission_key: Mapped[str] = mapped_column(ForeignKey("permissions.key"), primary_key=True)


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), primary_key=True)


class Space(Base):
    __tablename__ = "spaces"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    # The creator manages the space: channels, its invite code, moderation inside it.
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # Reusable code people type to join; the creator can replace it at any time.
    invite_code: Mapped[str | None] = mapped_column(String(16), unique=True, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Only the creator sets the picture.
    avatar: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    avatar_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Space settings: the best screen share quality members may pick, and whether cameras are allowed.
    max_share_quality: Mapped[str] = mapped_column(String(16), default="1440p60", server_default="1440p60")
    camera_allowed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")


class SpaceModerator(Base):
    """A moderator does everything the space's creator does, except creating and deleting channels,
    deleting the space, changing its picture and appointing moderators."""

    __tablename__ = "space_moderators"
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)


class SpaceMember(Base):
    __tablename__ = "space_members"
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)


class Channel(Base):
    __tablename__ = "channels"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    space_id: Mapped[int] = mapped_column(ForeignKey("spaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(24))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 0 means no limit on people in a voice channel.
    user_limit: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Private channels are visible only to explicitly added members; public ones to the whole space.
    is_private: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("space_id", "name", name="uq_channel_space_name"),)


class ChannelMember(Base):
    __tablename__ = "channel_members"
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # AES-256-GCM ciphertext (see app/server/chat.py); the database never holds message text in the clear.
    body: Mapped[str] = mapped_column(Text)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id"), nullable=True)


class Attachment(Base):
    """A file sent in a chat. The bytes live encrypted in files_dir/<id>.bin; the name is encrypted too."""

    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChannelRead(Base):
    """Last message a user has seen in a channel, for unread counters across devices."""

    __tablename__ = "channel_reads"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), primary_key=True)
    last_message_id: Mapped[int] = mapped_column(Integer, default=0)


class VoiceState(Base):
    __tablename__ = "voice_states"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    muted: Mapped[bool] = mapped_column(Boolean, default=False)
    deafened: Mapped[bool] = mapped_column(Boolean, default=False)
    speaking: Mapped[bool] = mapped_column(Boolean, default=False)
    server_muted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    status: Mapped[str] = mapped_column(String(24), default="connected")
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column(String(60))
    target_id: Mapped[str] = mapped_column(String(80))
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    result: Mapped[str] = mapped_column(String(32), default="success")
    details: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
