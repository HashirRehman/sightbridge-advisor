from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller, derived only from a verified token, never from request data."""

    user_id: str  # Cognito `sub`
    email: str
    is_admin: bool
