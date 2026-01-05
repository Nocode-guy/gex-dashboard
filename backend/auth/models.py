# Auth Pydantic Models for GEX Dashboard
from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from datetime import datetime
import re


class UserCreate(BaseModel):
    """Registration request"""
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)

    def validate_password_strength(self) -> tuple[bool, str]:
        """Validate password has uppercase, lowercase, and number"""
        if not re.search(r'[A-Z]', self.password):
            return False, "Password must contain at least one uppercase letter"
        if not re.search(r'[a-z]', self.password):
            return False, "Password must contain at least one lowercase letter"
        if not re.search(r'\d', self.password):
            return False, "Password must contain at least one number"
        return True, ""


class UserLogin(BaseModel):
    """Login request"""
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Login response with tokens"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until access token expires
    user: "UserResponse"


class UserResponse(BaseModel):
    """User info returned to frontend"""
    id: str
    email: str
    is_approved: bool
    is_admin: bool
    email_verified: bool
    created_at: datetime


class UserInDB(BaseModel):
    """Full user model from database"""
    id: str
    email: str
    password_hash: str
    email_verified: bool = False
    email_verification_token: Optional[str] = None
    email_verification_expires: Optional[datetime] = None
    is_approved: bool = False
    is_admin: bool = False
    created_at: datetime
    last_login: Optional[datetime] = None
    failed_login_attempts: int = 0
    locked_until: Optional[datetime] = None

    def to_response(self) -> UserResponse:
        return UserResponse(
            id=self.id,
            email=self.email,
            is_approved=self.is_approved,
            is_admin=self.is_admin,
            email_verified=self.email_verified,
            created_at=self.created_at
        )


class UserPreferences(BaseModel):
    """User preferences (synced from localStorage)"""
    theme: str = "dark"
    current_symbol: str = "SPX"
    refresh_interval: int = 1
    current_view: str = "gex"
    expiration_mode: str = "all"
    view_mode: str = "single"
    trinity_symbols: List[str] = ["SPY", "QQQ", "IWM"]
    trend_filter: str = "all"


class AdminUserList(BaseModel):
    """Admin view of users"""
    users: List[UserResponse]
    pending_count: int
    total_count: int


class PasswordReset(BaseModel):
    """Password reset request"""
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    """Password reset confirmation"""
    token: str
    new_password: str = Field(..., min_length=8, max_length=128)


class ResendVerification(BaseModel):
    """Resend email verification request"""
    email: EmailStr
