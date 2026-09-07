from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AuthModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegisterRequest(AuthModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_]{3,32}$")
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, min_length=1, max_length=50)

    @field_validator("display_name")
    @classmethod
    def display_name_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("display_name must not be blank")
        return stripped


class LoginRequest(AuthModel):
    username: str = Field(pattern=r"^[A-Za-z0-9_]{3,32}$")
    password: str = Field(min_length=1, max_length=128)


class AuthUser(AuthModel):
    id: UUID
    username: str | None
    display_name: str


class WorkspaceSummary(AuthModel):
    id: UUID
    name: str


class AuthResponse(AuthModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: AuthUser
    workspace_id: UUID


class MeResponse(AuthModel):
    user: AuthUser
    workspaces: list[WorkspaceSummary]
