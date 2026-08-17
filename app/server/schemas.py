from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

USERNAME_PATTERN = r"^[a-zA-Z0-9_.-]+$"
SAFE_TEXT_PATTERN = r"^[^\x00-\x1f\x7f<>]+$"


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64, pattern=USERNAME_PATTERN)
    password: str = Field(min_length=1, max_length=256)


class TokenOut(OrmModel):
    access_token: str
    must_change_password: bool


class ClientUpdateOut(BaseModel):
    latest_version: str
    update_available: bool
    required: bool = False
    download_url: str = ""
    sha256: str = ""
    release_notes_url: str = ""


class MeOut(OrmModel):
    id: int
    username: str
    display_name: str
    must_change_password: bool
    permissions: list[str]


class PasswordChangeIn(OrmModel):
    old_password: str
    new_password: str


class UserCreateIn(OrmModel):
    username: str = Field(min_length=3, max_length=64, pattern=USERNAME_PATTERN)
    display_name: str = Field(min_length=1, max_length=120, pattern=SAFE_TEXT_PATTERN)
    temporary_password: str = Field(min_length=12, max_length=256)
    is_admin: bool = False


class UserOut(OrmModel):
    id: int
    username: str
    display_name: str
    status: str
    must_change_password: bool


class SpaceCreateIn(OrmModel):
    name: str = Field(min_length=2, max_length=120, pattern=SAFE_TEXT_PATTERN)


class SpaceOut(OrmModel):
    id: int
    name: str


class ChannelCreateIn(OrmModel):
    space_id: int
    name: str = Field(min_length=2, max_length=120, pattern=SAFE_TEXT_PATTERN)
    type: str = Field(pattern=r"^(text|voice)$")


class ChannelOut(OrmModel):
    id: int
    space_id: int
    name: str
    type: str


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


class VoiceJoinIn(OrmModel):
    muted: bool = False
    deafened: bool = False


class VoiceStateIn(OrmModel):
    muted: bool
    deafened: bool
    speaking: bool = False
    status: str = "connected"


class VoiceStateOut(OrmModel):
    user_id: int
    display_name: str
    channel_id: int
    muted: bool
    deafened: bool
    speaking: bool
    status: str


class AuditOut(OrmModel):
    id: int
    actor_id: int | None
    action: str
    target_type: str
    target_id: str
    result: str
    created_at: datetime
