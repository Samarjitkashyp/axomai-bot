import os
import hashlib
import hmac
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
    return hmac.compare_digest(base64.b64encode(dk).decode(), stored_hash)


def authenticate_user(username: str, password: str):
    """(status, user): "ok" (the user), "bad" (unknown user, inactive, or wrong password: one answer, so nothing is given away),
    "denied" (the password is right but the account has no bot access), or "error" (the database could not be read: not the visitor's fault)."""
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
            _burn_time(password)
            return "bad", None

        uid, uname, pw_hash, is_active, is_superuser = row
        if not is_active or not verify_django_password(password, pw_hash):
            cur.close()
            conn.close()
            return "bad", None

        if is_superuser:
            cur.close()
            conn.close()
            return "ok", {"id": uid, "username": uname, "is_superuser": True}

        cur.execute(
            "SELECT bot_access FROM superadmin_subdomainpermission WHERE user_id = %s",
            (uid,),
        )
        perm = cur.fetchone()
        cur.close()
        conn.close()

        if not perm or not perm[0]:
            return "denied", None

        return "ok", {"id": uid, "username": uname, "is_superuser": False}
    except Exception:
        return "error", None


def _burn_time(password: str) -> None:
    """An unknown username takes as long to answer as a known one with a wrong password (no guessing of usernames by timing)."""
    try:
        verify_django_password(password, "pbkdf2_sha256$870000$timing$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    except Exception:
        pass


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
