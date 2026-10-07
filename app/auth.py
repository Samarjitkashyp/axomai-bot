import os
import hashlib
import base64
import psycopg2
from functools import lru_cache
from typing import Optional, Dict
from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
SESSION_MAX_AGE = 86400 * 7  # 7 days
COOKIE_NAME = "axomai_bot_session"

serializer = URLSafeTimedSerializer(SECRET_KEY)

PUBLIC_PATHS = {"/login", "/logout", "/health"}


def get_db_conn():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME", "axom_ai"),
        user=os.getenv("DB_USER", "axom_user"),
        password=os.getenv("DB_PASSWORD", ""),
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
    )


def verify_django_password(plain: str, encoded: str) -> bool:
    parts = encoded.split("$")
    if len(parts) != 4:
        return False
    algorithm, iterations, salt, stored_hash = parts
    if algorithm != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac(
        "sha256", plain.encode(), salt.encode(), int(iterations)
    )
    return base64.b64encode(dk).decode() == stored_hash


def authenticate_user(username: str, password: str) -> Optional[Dict]:
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, username, password, is_active, is_superuser "
            "FROM auth_user WHERE username = %s",
            (username,),
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            conn.close()
            return None

        uid, uname, pw_hash, is_active, is_superuser = row
        if not is_active:
            cur.close()
            conn.close()
            return None
        if not verify_django_password(password, pw_hash):
            cur.close()
            conn.close()
            return None

        if is_superuser:
            cur.close()
            conn.close()
            return {"id": uid, "username": uname, "is_superuser": True}

        cur.execute(
            "SELECT bot_access FROM superadmin_subdomainpermission WHERE user_id = %s",
            (uid,),
        )
        perm = cur.fetchone()
        cur.close()
        conn.close()

        if not perm or not perm[0]:
            return None

        return {"id": uid, "username": uname, "is_superuser": False}
    except Exception:
        return None


def create_session_cookie(user: Dict) -> str:
    return serializer.dumps({"uid": user["id"], "username": user["username"]})


def get_current_user(request: Request) -> Optional[Dict]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = serializer.loads(token, max_age=SESSION_MAX_AGE)
        return data
    except (BadSignature, SignatureExpired):
        return None


LOGIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Login — Axom AI Crawler Bot</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#0a0a1a;font-family:'Segoe UI',system-ui,sans-serif;color:#e2e8f0}
.card{background:#111827;border:1px solid #1e293b;border-radius:20px;padding:40px 36px;
width:100%;max-width:400px;box-shadow:0 20px 60px rgba(0,0,0,0.5)}
.logo{text-align:center;margin-bottom:24px}
.logo h1{font-size:22px;font-weight:700;color:#fff}
.logo h1 span{color:#f59e0b}
.logo p{font-size:12px;color:#64748b;margin-top:4px}
.field{margin-bottom:16px}
.field label{display:block;font-size:12px;font-weight:600;color:#94a3b8;margin-bottom:6px}
.field input{width:100%;padding:10px 14px;background:#0f172a;border:1px solid #1e293b;
border-radius:10px;color:#fff;font-size:14px;outline:none;transition:border 0.2s}
.field input:focus{border-color:#f59e0b}
.btn{width:100%;padding:12px;background:linear-gradient(135deg,#f59e0b,#d97706);
color:#0f172a;font-weight:700;font-size:14px;border:none;border-radius:10px;
cursor:pointer;transition:opacity 0.2s;margin-top:8px}
.btn:hover{opacity:0.9}
.error{background:#7f1d1d;border:1px solid #991b1b;border-radius:10px;padding:10px 14px;
font-size:12px;color:#fca5a5;margin-bottom:16px;text-align:center}
</style>
</head>
<body>
<div class="card">
<div class="logo">
<h1>Axom AI <span>Crawler Bot</span></h1>
<p>Authorized Access Only</p>
</div>
{error}
<form method="POST" action="/login">
<div class="field">
<label>Username</label>
<input type="text" name="username" required autofocus>
</div>
<div class="field">
<label>Password</label>
<input type="password" name="password" required>
</div>
<button type="submit" class="btn">Sign In</button>
</form>
</div>
</body>
</html>"""
