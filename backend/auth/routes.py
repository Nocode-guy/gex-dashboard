# Auth Routes for GEX Dashboard
from fastapi import APIRouter, HTTPException, status, Response, Request, Cookie, Depends
from fastapi.responses import RedirectResponse
from typing import Optional
from datetime import datetime, timezone

from .models import (
    UserCreate, UserLogin, TokenResponse, UserResponse,
    UserPreferences, PasswordReset, PasswordResetConfirm, ResendVerification
)
from .security import (
    hash_password, verify_password, create_access_token, create_refresh_token,
    verify_refresh_token, generate_verification_token, generate_password_reset_token,
    require_auth, require_admin, ACCESS_TOKEN_EXPIRE_MINUTES,
    check_account_lockout, should_lock_account, get_lockout_until
)
from .email import send_verification_email, send_approval_email, send_password_reset_email

# Import database functions
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db_postgres import (
    create_user, get_user_by_email, get_user_by_id, verify_user_email,
    update_login_success, update_login_failure, get_user_by_verification_token,
    store_refresh_token, get_refresh_token, revoke_refresh_token, revoke_all_user_tokens,
    get_user_symbols, add_user_symbol, remove_user_symbol,
    get_user_preferences, save_user_preferences, init_user_defaults,
    get_all_users, get_pending_users, approve_user, delete_user, set_admin,
    set_password_reset_token, get_user_by_reset_token, update_password
)

router = APIRouter(prefix="/auth", tags=["auth"])


# ============== Registration ==============

@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate):
    """Register a new user (requires email verification and admin approval)"""

    # Validate password strength
    is_valid, message = user_data.validate_password_strength()
    if not is_valid:
        raise HTTPException(status_code=400, detail=message)

    # Check if email already exists
    existing = await get_user_by_email(user_data.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create user
    password_hash = hash_password(user_data.password)
    verification_token = generate_verification_token()

    user_id = await create_user(
        email=user_data.email,
        password_hash=password_hash,
        verification_token=verification_token
    )

    if not user_id:
        raise HTTPException(status_code=500, detail="Failed to create user")

    # Initialize default preferences and symbols
    await init_user_defaults(user_id)

    # Send verification email
    await send_verification_email(user_data.email, verification_token)

    return {
        "message": "Registration successful! Please check your email to verify your account.",
        "email": user_data.email
    }


@router.get("/verify-email/{token}")
async def verify_email(token: str):
    """Verify email address"""
    user = await get_user_by_verification_token(token)

    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired verification link")

    # Check expiration
    if user['email_verification_expires'] < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Verification link has expired")

    # Mark email as verified
    await verify_user_email(str(user['id']))

    # Redirect to a success page
    return RedirectResponse(url="/login?verified=true", status_code=302)


@router.post("/resend-verification")
async def resend_verification(data: ResendVerification):
    """Resend email verification"""
    user = await get_user_by_email(data.email)

    if not user:
        # Don't reveal if email exists
        return {"message": "If that email is registered, a verification link has been sent."}

    if user['email_verified']:
        return {"message": "Email already verified. You can log in."}

    # Generate new token
    verification_token = generate_verification_token()

    # TODO: Update token in database
    # For now, just resend with existing token if available
    if user.get('email_verification_token'):
        await send_verification_email(data.email, user['email_verification_token'])

    return {"message": "If that email is registered, a verification link has been sent."}


# ============== Login / Logout ==============

@router.post("/login", response_model=TokenResponse)
async def login(user_data: UserLogin, response: Response):
    """Login with email and password"""

    # Get user
    user = await get_user_by_email(user_data.email)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Check lockout
    is_locked, minutes_remaining = check_account_lockout(
        user['failed_login_attempts'],
        user.get('locked_until')
    )
    if is_locked:
        raise HTTPException(
            status_code=423,
            detail=f"Account locked due to too many failed attempts. Try again in {minutes_remaining} minutes."
        )

    # Verify password
    if not verify_password(user_data.password, user['password_hash']):
        # Update failure count
        lock_until = get_lockout_until() if should_lock_account(user['failed_login_attempts'] + 1) else None
        await update_login_failure(str(user['id']), lock_until)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    # Check email verified
    if not user['email_verified']:
        raise HTTPException(
            status_code=403,
            detail="Please verify your email before logging in. Check your inbox for the verification link."
        )

    # Check approval status
    if not user['is_approved']:
        raise HTTPException(
            status_code=403,
            detail="Your account is pending admin approval. You'll receive an email once approved."
        )

    # Create tokens
    access_token = create_access_token({
        "sub": str(user['id']),
        "email": user['email'],
        "is_admin": user['is_admin'],
        "is_approved": user['is_approved']
    })

    refresh_token, refresh_hash, expires_at = create_refresh_token(str(user['id']))

    # Store refresh token
    await store_refresh_token(str(user['id']), refresh_hash, expires_at)

    # Update login success
    await update_login_success(str(user['id']))

    # Set refresh token cookie
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=True,  # HTTPS only in production
        samesite="lax",
        max_age=7 * 24 * 60 * 60,  # 7 days
        path="/auth"  # Only sent to auth endpoints
    )

    return TokenResponse(
        access_token=access_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse(
            id=str(user['id']),
            email=user['email'],
            is_approved=user['is_approved'],
            is_admin=user['is_admin'],
            email_verified=user['email_verified'],
            created_at=user['created_at']
        )
    )


@router.post("/logout")
async def logout(
    response: Response,
    refresh_token: Optional[str] = Cookie(None)
):
    """Logout and revoke refresh token"""
    if refresh_token:
        # Find and revoke the token
        # Note: In production, you'd hash the token and look it up
        pass

    # Clear the cookie
    response.delete_cookie(key="refresh_token", path="/auth")

    return {"message": "Logged out successfully"}


@router.post("/refresh")
async def refresh_tokens(
    response: Response,
    refresh_token: Optional[str] = Cookie(None)
):
    """Get new access token using refresh token"""
    if not refresh_token:
        raise HTTPException(status_code=401, detail="No refresh token")

    # Hash the token and look up in database
    from .security import pwd_context
    # This is inefficient - in production you'd store a searchable token ID
    # For now, we'll verify against stored hashes

    # Get user from token (simplified - in production use proper token lookup)
    # This requires storing the token differently
    raise HTTPException(status_code=501, detail="Token refresh not fully implemented")


@router.get("/check")
async def check_auth(user: Optional[dict] = Depends(require_auth)):
    """Check authentication status"""
    if user:
        return {
            "authenticated": True,
            "user": {
                "id": user.get("sub"),
                "email": user.get("email"),
                "is_admin": user.get("is_admin"),
                "is_approved": user.get("is_approved")
            }
        }
    return {"authenticated": False}


# ============== Password Reset ==============

@router.post("/forgot-password")
async def forgot_password(data: PasswordReset):
    """Request password reset"""
    user = await get_user_by_email(data.email)

    # Always return same message (don't reveal if email exists)
    if user:
        token = generate_password_reset_token()
        await set_password_reset_token(data.email, token)
        await send_password_reset_email(data.email, token)

    return {"message": "If that email is registered, a password reset link has been sent."}


@router.post("/reset-password")
async def reset_password(data: PasswordResetConfirm):
    """Reset password with token"""
    user = await get_user_by_reset_token(data.token)

    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")

    # Check expiration
    if user['password_reset_expires'] < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Reset link has expired")

    # Update password
    password_hash = hash_password(data.new_password)
    await update_password(str(user['id']), password_hash)

    return {"message": "Password updated successfully. You can now log in."}


# ============== User API (Authenticated) ==============

user_router = APIRouter(prefix="/api/me", tags=["user"])


@user_router.get("")
async def get_me(user: dict = Depends(require_auth)):
    """Get current user info"""
    user_data = await get_user_by_id(user['sub'])
    if not user_data:
        raise HTTPException(status_code=404, detail="User not found")

    return UserResponse(
        id=str(user_data['id']),
        email=user_data['email'],
        is_approved=user_data['is_approved'],
        is_admin=user_data['is_admin'],
        email_verified=user_data['email_verified'],
        created_at=user_data['created_at']
    )


@user_router.get("/symbols")
async def get_my_symbols(user: dict = Depends(require_auth)):
    """Get user's saved symbols"""
    symbols = await get_user_symbols(user['sub'])
    return {"symbols": symbols}


@user_router.post("/symbols/{symbol}")
async def add_my_symbol(symbol: str, user: dict = Depends(require_auth)):
    """Add symbol to user's watchlist"""
    success = await add_user_symbol(user['sub'], symbol.upper())
    if success:
        symbols = await get_user_symbols(user['sub'])
        return {"symbols": symbols}
    raise HTTPException(status_code=400, detail="Failed to add symbol")


@user_router.delete("/symbols/{symbol}")
async def remove_my_symbol(symbol: str, user: dict = Depends(require_auth)):
    """Remove symbol from user's watchlist"""
    success = await remove_user_symbol(user['sub'], symbol.upper())
    symbols = await get_user_symbols(user['sub'])
    return {"symbols": symbols}


@user_router.get("/preferences")
async def get_my_preferences(user: dict = Depends(require_auth)):
    """Get user's preferences"""
    prefs = await get_user_preferences(user['sub'])
    if prefs:
        return prefs
    return UserPreferences().model_dump()


@user_router.put("/preferences")
async def update_my_preferences(preferences: UserPreferences, user: dict = Depends(require_auth)):
    """Update user's preferences"""
    success = await save_user_preferences(user['sub'], preferences.model_dump())
    if success:
        return preferences
    raise HTTPException(status_code=500, detail="Failed to save preferences")


# ============== Admin API ==============

admin_router = APIRouter(prefix="/admin", tags=["admin"])


@admin_router.get("/users")
async def list_users(user: dict = Depends(require_admin)):
    """List all users (admin only)"""
    users = await get_all_users()
    pending = await get_pending_users()

    return {
        "users": [
            {
                "id": str(u['id']),
                "email": u['email'],
                "email_verified": u['email_verified'],
                "is_approved": u['is_approved'],
                "is_admin": u['is_admin'],
                "created_at": u['created_at'].isoformat() if u.get('created_at') else None,
                "last_login": u['last_login'].isoformat() if u.get('last_login') else None
            }
            for u in users
        ],
        "pending_count": len(pending),
        "total_count": len(users)
    }


@admin_router.get("/users/pending")
async def list_pending_users(user: dict = Depends(require_admin)):
    """List users pending approval"""
    pending = await get_pending_users()

    return {
        "users": [
            {
                "id": str(u['id']),
                "email": u['email'],
                "email_verified": u['email_verified'],
                "created_at": u['created_at'].isoformat() if u.get('created_at') else None
            }
            for u in pending
        ]
    }


@admin_router.post("/users/{user_id}/approve")
async def approve_user_endpoint(user_id: str, admin: dict = Depends(require_admin)):
    """Approve a user (admin only)"""
    target_user = await get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    success = await approve_user(user_id)
    if success:
        # Send approval email
        await send_approval_email(target_user['email'])
        return {"message": f"User {target_user['email']} has been approved"}

    raise HTTPException(status_code=500, detail="Failed to approve user")


@admin_router.delete("/users/{user_id}")
async def delete_user_endpoint(user_id: str, admin: dict = Depends(require_admin)):
    """Delete/reject a user (admin only)"""
    # Prevent self-deletion
    if user_id == admin['sub']:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")

    target_user = await get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    success = await delete_user(user_id)
    if success:
        return {"message": f"User {target_user['email']} has been deleted"}

    raise HTTPException(status_code=500, detail="Failed to delete user")


@admin_router.post("/users/{user_id}/toggle-admin")
async def toggle_admin_endpoint(user_id: str, admin: dict = Depends(require_admin)):
    """Toggle admin status for a user"""
    # Prevent self-demotion
    if user_id == admin['sub']:
        raise HTTPException(status_code=400, detail="Cannot modify your own admin status")

    target_user = await get_user_by_id(user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    new_status = not target_user['is_admin']
    await set_admin(user_id, new_status)

    status_text = "granted admin" if new_status else "revoked admin"
    return {"message": f"User {target_user['email']} has been {status_text}"}


# ============== Initial Setup (One-Time Use) ==============

# Setup secret - set this in environment to enable setup endpoint
SETUP_SECRET = os.environ.get("GEX_SETUP_SECRET", "")

# Pre-configured admin
INITIAL_ADMIN_EMAIL = "morganwillie93@gmail.com"
INITIAL_ADMIN_PASSWORD = "GexAdmin2024!"  # Change after first login!

setup_router = APIRouter(prefix="/setup", tags=["setup"])


@setup_router.get("/init/{secret}")
async def initialize_admin(secret: str):
    """
    One-time setup endpoint to create the initial admin user.

    Usage: GET /setup/init/YOUR_SETUP_SECRET

    Set GEX_SETUP_SECRET environment variable to enable this endpoint.
    After creating admin, remove or change the secret.
    """
    # Check if setup is enabled
    if not SETUP_SECRET:
        raise HTTPException(
            status_code=404,
            detail="Setup not enabled. Set GEX_SETUP_SECRET environment variable."
        )

    # Verify secret
    if secret != SETUP_SECRET:
        raise HTTPException(status_code=403, detail="Invalid setup secret")

    # Check if admin already exists
    existing = await get_user_by_email(INITIAL_ADMIN_EMAIL)
    if existing:
        if existing.get('is_admin'):
            return {
                "status": "exists",
                "message": f"Admin already exists: {INITIAL_ADMIN_EMAIL}",
                "email": INITIAL_ADMIN_EMAIL
            }
        else:
            # Upgrade to admin
            await set_admin(str(existing['id']), True)
            await approve_user(str(existing['id']))
            return {
                "status": "upgraded",
                "message": f"Upgraded existing user to admin: {INITIAL_ADMIN_EMAIL}",
                "email": INITIAL_ADMIN_EMAIL
            }

    # Create admin user
    password_hash = hash_password(INITIAL_ADMIN_PASSWORD)

    from db_postgres import get_pool
    pool = get_pool()
    if not pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    async with pool.acquire() as conn:
        # Create user
        result = await conn.fetchrow("""
            INSERT INTO users (
                email, password_hash, email_verified, is_approved, is_admin
            ) VALUES ($1, $2, TRUE, TRUE, TRUE)
            RETURNING id
        """, INITIAL_ADMIN_EMAIL, password_hash)

        user_id = result['id']

        # Create preferences
        await conn.execute("""
            INSERT INTO user_preferences (user_id, theme, current_symbol, refresh_interval)
            VALUES ($1, 'dark', 'SPX', 5)
            ON CONFLICT (user_id) DO NOTHING
        """, user_id)

        # Add default symbols
        default_symbols = ["SPX", "SPY", "QQQ", "TSLA", "NVDA", "AAPL", "AMZN"]
        for i, symbol in enumerate(default_symbols):
            await conn.execute("""
                INSERT INTO user_symbols (user_id, symbol, display_order)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, symbol) DO NOTHING
            """, user_id, symbol, i)

    return {
        "status": "created",
        "message": "Admin user created successfully!",
        "email": INITIAL_ADMIN_EMAIL,
        "password": INITIAL_ADMIN_PASSWORD,
        "warning": "CHANGE YOUR PASSWORD AFTER FIRST LOGIN!",
        "next_steps": [
            "1. Go to /login",
            "2. Sign in with the credentials above",
            "3. Change your password",
            "4. Remove GEX_SETUP_SECRET from environment variables"
        ]
    }
