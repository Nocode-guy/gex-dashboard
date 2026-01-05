# Security utilities for GEX Dashboard Auth
import os
import secrets
import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Optional, Any
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status, Request, Cookie
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# JWT Configuration - MUST be set in production environment
_jwt_secret = os.environ.get("JWT_SECRET_KEY")
_jwt_refresh_secret = os.environ.get("JWT_REFRESH_SECRET_KEY")

# Validate secrets are set in production (when DATABASE_URL is set, we're on Render)
if os.environ.get("DATABASE_URL"):
    if not _jwt_secret:
        raise RuntimeError(
            "SECURITY ERROR: JWT_SECRET_KEY environment variable must be set in production! "
            "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    if not _jwt_refresh_secret:
        raise RuntimeError(
            "SECURITY ERROR: JWT_REFRESH_SECRET_KEY environment variable must be set in production! "
            "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    JWT_SECRET_KEY = _jwt_secret
    JWT_REFRESH_SECRET_KEY = _jwt_refresh_secret
    print("[Security] JWT secrets loaded from environment")
else:
    # Development mode - use random secrets (will change on restart)
    JWT_SECRET_KEY = _jwt_secret or secrets.token_hex(32)
    JWT_REFRESH_SECRET_KEY = _jwt_refresh_secret or secrets.token_hex(32)
    if not _jwt_secret:
        print("[Security] WARNING: Using random JWT_SECRET_KEY (dev mode only)")

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60  # Extended from 15 to 60 minutes
REFRESH_TOKEN_EXPIRE_DAYS = 7

# Rate limiting
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15

# HTTP Bearer scheme for access tokens
security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Hash a password using bcrypt"""
    password_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash"""
    try:
        password_bytes = plain_password.encode('utf-8')
        hashed_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(password_bytes, hashed_bytes)
    except Exception:
        return False


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "access"
    })
    return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str) -> tuple[str, str, datetime]:
    """
    Create a refresh token
    Returns: (token, token_hash, expires_at)
    """
    # Generate a random token
    token = secrets.token_urlsafe(32)
    # Hash it for storage
    token_hash = hash_password(token)
    # Expiration
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    return token, token_hash, expires_at


def verify_refresh_token(token: str, stored_hash: str) -> bool:
    """Verify a refresh token against its stored hash"""
    return verify_password(token, stored_hash)


def verify_token(token: str, token_type: str = "access") -> Optional[dict]:
    """
    Verify and decode a JWT token
    Returns the payload if valid, None otherwise
    """
    try:
        secret = JWT_SECRET_KEY if token_type == "access" else JWT_REFRESH_SECRET_KEY
        payload = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])

        # Verify token type
        if payload.get("type") != token_type:
            return None

        return payload
    except JWTError:
        return None


def generate_verification_token() -> str:
    """Generate a secure token for email verification"""
    return secrets.token_urlsafe(32)


def generate_password_reset_token() -> str:
    """Generate a secure token for password reset"""
    return secrets.token_urlsafe(32)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[dict]:
    """
    Dependency to get current user from JWT token
    Returns None if no valid token (for optional auth)
    """
    if not credentials:
        return None

    token = credentials.credentials
    payload = verify_token(token, "access")

    if not payload:
        return None

    return payload


async def require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> dict:
    """
    Dependency that requires valid authentication
    Raises 401 if no valid token
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = credentials.credentials
    payload = verify_token(token, "access")

    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"}
        )

    # Check if user is approved
    if not payload.get("is_approved"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account pending approval"
        )

    return payload


async def require_admin(user: dict = Depends(require_auth)) -> dict:
    """
    Dependency that requires admin privileges
    """
    if not user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required"
        )
    return user


def check_account_lockout(failed_attempts: int, locked_until: Optional[datetime]) -> tuple[bool, Optional[int]]:
    """
    Check if account is locked
    Returns: (is_locked, minutes_remaining)
    """
    if locked_until:
        now = datetime.now(timezone.utc)
        if locked_until > now:
            remaining = (locked_until - now).total_seconds() / 60
            return True, int(remaining) + 1

    return False, None


def should_lock_account(failed_attempts: int) -> bool:
    """Check if account should be locked after failed attempt"""
    return failed_attempts >= MAX_FAILED_ATTEMPTS


def get_lockout_until() -> datetime:
    """Get the datetime when lockout should expire"""
    return datetime.now(timezone.utc) + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
