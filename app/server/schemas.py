from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

USERNAME_PATTERN = r"^[a-zA-Z0-9_.-]+$"
SAFE_TEXT_PATTERN = r"^[^\x00-\x1f\x7f<>]+$"


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


DEVICE_NAME_PATTERN = r"^[^\x00-\x1f\x7f<>]*$"


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=1, max_length=256)
    device_name: str = Field(default="", max_length=120, pattern=DEVICE_NAME_PATTERN)


class TokenOut(OrmModel):
    access_token: str
    must_change_password: bool
    refresh_token: str = ""


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=256)


class SessionOut(BaseModel):
    id: int
    device_name: str
    ip_address: str
    created_at: datetime
    last_used_at: datetime
    current: bool


class InviteCreateIn(BaseModel):
    space_id: int | None = None
    is_admin: bool = False
    note: str = Field(default="", max_length=120, pattern=DEVICE_NAME_PATTERN)


class InviteOut(BaseModel):
    id: int
    note: str
    is_admin: bool
    space_id: int | None
    created_at: datetime
    expires_at: datetime
    used: bool


class InviteCreatedOut(InviteOut):
    code: str


class RegisterIn(BaseModel):
    # Empty: open sign-up. Otherwise a one-time invite or a space's invite code.
    code: str = Field(default="", max_length=64, pattern=r"^[A-Za-z0-9_ -]*$")
    username: str = Field(min_length=3, max_length=64, pattern=USERNAME_PATTERN)
    display_name: str = Field(min_length=1, max_length=120, pattern=SAFE_TEXT_PATTERN)
    password: str = Field(min_length=8, max_length=256)
    device_name: str = Field(default="", max_length=120, pattern=DEVICE_NAME_PATTERN)


class ClientUpdateOut(BaseModel):
    latest_version: str
    update_available: bool
    required: bool = False
    download_url: str = ""
    sha256: str = ""
    signature: str = ""
    release_notes_url: str = ""


class MeOut(OrmModel):
    id: int
    username: str
    display_name: str
    must_change_password: bool
    permissions: list[str]
    is_owner: bool = False


class PasswordChangeIn(OrmModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserCreateIn(OrmModel):
    username: str = Field(min_length=3, max_length=64, pattern=USERNAME_PATTERN)
    display_name: str = Field(min_length=1, max_length=120, pattern=SAFE_TEXT_PATTERN)
    temporary_password: str | None = Field(default=None, min_length=8, max_length=256)
    is_admin: bool = False


class UserOut(OrmModel):
    id: int
    username: str
    display_name: str
    status: str
    must_change_password: bool


class UserCreatedOut(UserOut):
    temporary_password: str


class SpaceCreateIn(OrmModel):
    name: str = Field(min_length=2, max_length=120, pattern=SAFE_TEXT_PATTERN)


class SpaceOut(OrmModel):
    id: int
    name: str
    can_manage: bool = False
    is_mine: bool = False
    member_count: int = 0
    # Only for people who manage the space.
    invite_code: str | None = None


class SpaceJoinIn(BaseModel):
    code: str = Field(min_length=4, max_length=32, pattern=r"^[A-Za-z0-9 -]+$")


class ChannelCreateIn(OrmModel):
    space_id: int
    name: str = Field(min_length=2, max_length=120, pattern=SAFE_TEXT_PATTERN)
    type: str = Field(pattern=r"^(text|voice)$")
    is_private: bool = False
    user_limit: int = Field(default=0, ge=0, le=99)


class ChannelUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120, pattern=SAFE_TEXT_PATTERN)
    position: int | None = Field(default=None, ge=0, le=10_000)
    user_limit: int | None = Field(default=None, ge=0, le=99)
    is_private: bool | None = None


class ChannelOut(OrmModel):
    id: int
    space_id: int
    name: str
    type: str
    position: int = 0
    user_limit: int = 0
    is_private: bool = False


class DeviceKeyIn(BaseModel):
    public_key: str = Field(min_length=43, max_length=44, pattern=r"^[A-Za-z0-9_-]+={0,2}$")
    fingerprint: str = Field(min_length=16, max_length=32, pattern=r"^[A-F0-9:]+$")


class E2EEUserKeyOut(BaseModel):
    user_id: int
    public_key: str
    fingerprint: str


class E2EEKeySetOut(BaseModel):
    sender_id: int
    key_id: str
    envelope: str


class E2EEStateOut(BaseModel):
    users: list[E2EEUserKeyOut]
    key_sets: list[E2EEKeySetOut]


class SenderKeyIn(BaseModel):
    key_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    envelopes: dict[int, str] = Field(default_factory=dict)


class MemberIn(OrmModel):
    user_id: int


class MessageCreateIn(OrmModel):
    body: str = Field(min_length=1, max_length=4000)


class MessageOut(OrmModel):
    id: int
    channel_id: int
    author_id: int
    author_name: str
    body: str
    created_at: datetime
    edited_at: datetime | None
    deleted: bool = False
    mentions: list[int] = []


class ReadIn(BaseModel):
    message_id: int = Field(ge=0)


class UnreadOut(BaseModel):
    channel_id: int
    count: int


class VoiceJoinIn(OrmModel):
    muted: bool = False
    deafened: bool = False


class VoiceStateIn(OrmModel):
    muted: bool
    deafened: bool
    speaking: bool = False
    status: Literal["connected", "reconnecting"] = "connected"


class VoiceStateOut(OrmModel):
    user_id: int
    display_name: str
    channel_id: int
    muted: bool
    deafened: bool
    speaking: bool
    server_muted: bool = False
    status: str


class MoveIn(BaseModel):
    channel_id: int


class ServerMuteIn(BaseModel):
    muted: bool


class AuditOut(OrmModel):
    id: int
    actor_id: int | None
    action: str
    target_type: str
    target_id: str
    result: str
    created_at: datetime
