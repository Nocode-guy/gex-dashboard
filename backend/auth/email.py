# Email service for GEX Dashboard Auth
import os
import httpx
from typing import Optional

# Resend API configuration
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
APP_URL = os.environ.get("APP_URL", "http://localhost:8000")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "GEX Dashboard <noreply@gexdashboard.com>")

# Email templates
VERIFICATION_SUBJECT = "Verify your GEX Dashboard account"
VERIFICATION_HTML = """
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 8px 8px; }}
        .button {{ display: inline-block; background: #667eea; color: white; padding: 12px 30px; text-decoration: none; border-radius: 6px; margin: 20px 0; }}
        .footer {{ text-align: center; color: #888; font-size: 12px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>GEX Dashboard</h1>
        </div>
        <div class="content">
            <h2>Verify Your Email</h2>
            <p>Thanks for signing up! Please click the button below to verify your email address:</p>
            <p style="text-align: center;">
                <a href="{verification_url}" class="button">Verify Email</a>
            </p>
            <p>Or copy and paste this link into your browser:</p>
            <p style="word-break: break-all; color: #667eea;">{verification_url}</p>
            <p>This link expires in 24 hours.</p>
            <p><strong>What happens next?</strong></p>
            <p>After verifying your email, your account will be reviewed by an administrator. You'll receive another email once your account is approved.</p>
        </div>
        <div class="footer">
            <p>If you didn't create this account, you can ignore this email.</p>
        </div>
    </div>
</body>
</html>
"""

APPROVAL_SUBJECT = "Your GEX Dashboard account has been approved!"
APPROVAL_HTML = """
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); color: white; padding: 30px; text-align: center; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 8px 8px; }}
        .button {{ display: inline-block; background: #11998e; color: white; padding: 12px 30px; text-decoration: none; border-radius: 6px; margin: 20px 0; }}
        .footer {{ text-align: center; color: #888; font-size: 12px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>You're Approved!</h1>
        </div>
        <div class="content">
            <h2>Welcome to GEX Dashboard</h2>
            <p>Great news! Your account has been approved by an administrator.</p>
            <p>You can now log in and start using GEX Dashboard to analyze gamma exposure data:</p>
            <p style="text-align: center;">
                <a href="{login_url}" class="button">Log In Now</a>
            </p>
            <p><strong>Quick Start:</strong></p>
            <ul>
                <li>Add tickers to your watchlist</li>
                <li>Monitor GEX, VEX, and DEX levels</li>
                <li>View options flow data</li>
                <li>Use the trading cheat sheet for guidance</li>
            </ul>
        </div>
        <div class="footer">
            <p>Happy trading!</p>
        </div>
    </div>
</body>
</html>
"""

PASSWORD_RESET_SUBJECT = "Reset your GEX Dashboard password"
PASSWORD_RESET_HTML = """
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); color: white; padding: 30px; text-align: center; border-radius: 8px 8px 0 0; }}
        .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 8px 8px; }}
        .button {{ display: inline-block; background: #f5576c; color: white; padding: 12px 30px; text-decoration: none; border-radius: 6px; margin: 20px 0; }}
        .footer {{ text-align: center; color: #888; font-size: 12px; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Password Reset</h1>
        </div>
        <div class="content">
            <h2>Reset Your Password</h2>
            <p>We received a request to reset your password. Click the button below to create a new password:</p>
            <p style="text-align: center;">
                <a href="{reset_url}" class="button">Reset Password</a>
            </p>
            <p>Or copy and paste this link into your browser:</p>
            <p style="word-break: break-all; color: #f5576c;">{reset_url}</p>
            <p>This link expires in 1 hour.</p>
        </div>
        <div class="footer">
            <p>If you didn't request this, you can ignore this email. Your password won't be changed.</p>
        </div>
    </div>
</body>
</html>
"""


async def send_email(to: str, subject: str, html: str) -> bool:
    """
    Send an email using Resend API
    Returns True if successful, False otherwise
    """
    if not RESEND_API_KEY:
        print(f"[Email] RESEND_API_KEY not set - would send to {to}: {subject}")
        return True  # Return True in dev mode

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "from": FROM_EMAIL,
                    "to": [to],
                    "subject": subject,
                    "html": html
                },
                timeout=10.0
            )

            if response.status_code == 200:
                print(f"[Email] Sent to {to}: {subject}")
                return True
            else:
                print(f"[Email] Failed to send to {to}: {response.text}")
                return False

    except Exception as e:
        print(f"[Email] Error sending to {to}: {e}")
        return False


async def send_verification_email(email: str, token: str) -> bool:
    """Send email verification link"""
    verification_url = f"{APP_URL}/auth/verify-email/{token}"
    html = VERIFICATION_HTML.format(verification_url=verification_url)
    return await send_email(email, VERIFICATION_SUBJECT, html)


async def send_approval_email(email: str) -> bool:
    """Send approval notification"""
    login_url = f"{APP_URL}/login"
    html = APPROVAL_HTML.format(login_url=login_url)
    return await send_email(email, APPROVAL_SUBJECT, html)


async def send_password_reset_email(email: str, token: str) -> bool:
    """Send password reset link"""
    reset_url = f"{APP_URL}/reset-password?token={token}"
    html = PASSWORD_RESET_HTML.format(reset_url=reset_url)
    return await send_email(email, PASSWORD_RESET_SUBJECT, html)
