"""Brute-force protection of the bot login: the same rules as the Axom AI admin login (3 wrong tries = 24 hours locked, per IP and per
username), kept in the same table (superadmin_superadminloginattempt), so a lock set here also holds at admin.aiaxom.co.in and the other
way round. Every login event is written to the admin audit log (superadmin_auditlog).

The database is reached through a small `connect` function (psycopg2 in production, sqlite3 in tests); `ph` is its placeholder.
"""
import ipaddress
from datetime import datetime, timedelta, timezone

MAX_FAILS = 3
LOCK_HOURS = 24


def _now():
    return datetime.now(timezone.utc)


def _as_dt(v):
    """A datetime (aware, UTC) from what the database returns (a datetime, or text in the tests)."""
    if v is None:
        return None
    if isinstance(v, str):
        v = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v


def client_ip(headers, peer):
    """The visitor's address as nginx saw it (X-Real-IP, set by nginx from the connection; Cloudflare's address only from Cloudflare itself).
    X-Forwarded-For is not used: a visitor can send anything in it. Without X-Real-IP (local runs, tests) the connection address."""
    real = (headers.get("x-real-ip") or "").strip()
    if real:
        try:
            return str(ipaddress.ip_address(real))
        except ValueError:
            pass
    return (peer or "127.0.0.1").strip()


def sanitize(value, max_len=150):
    """Null bytes and control characters out, trimmed, cut to max_len (same as the admin login)."""
    if not value:
        return ""
    cleaned = "".join(ch for ch in str(value).replace("\x00", "") if ch >= " " and ch != "\x7f").strip()
    return cleaned[:max_len]


class LoginGuard:
    def __init__(self, connect, ph="%s", now=_now):
        self.connect = connect
        self.ph = ph
        self.now = now

    # ---- lock status
    def active_lockout(self, ip, username):
        """{'is_locked': False} or the running lock: hours and minutes left. An expired lock is cleared on the way."""
        ph, now = self.ph, self.now()
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT id, locked_until FROM superadmin_superadminloginattempt WHERE is_locked AND (ip_address = {ph} OR (username <> '' AND username = {ph}))",
                (ip, username or ""),
            )
            for rid, until in cur.fetchall():
                until = _as_dt(until)
                if until and until > now:
                    secs = max(0, int((until - now).total_seconds()))
                    return {"is_locked": True, "hours": secs // 3600, "mins": (secs % 3600) // 60, "seconds": secs}
                cur.execute(
                    f"UPDATE superadmin_superadminloginattempt SET is_locked = {ph}, failed_count = 0, locked_until = NULL, updated_at = {ph} WHERE id = {ph}",
                    (False, now, rid),
                )
            conn.commit()
            return {"is_locked": False}
        finally:
            conn.close()

    # ---- a wrong try
    def record_failed(self, ip, username, user_agent):
        ph, now = self.ph, self.now()
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT id, failed_count, locked_until, username FROM superadmin_superadminloginattempt WHERE ip_address = {ph}", (ip,))
            row = cur.fetchone()
            ua = (user_agent or "")[:500]
            if row is None:
                count, name = 1, username or ""
                cur.execute(
                    "INSERT INTO superadmin_superadminloginattempt (ip_address, username, failed_count, last_failed_at, is_locked, locked_until, user_agent, created_at, updated_at) "
                    f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})",
                    (ip, name, count, now, False, None, ua, now, now),
                )
                rid = None
            else:
                rid, count, until, old_name = row
                until = _as_dt(until)
                if until and until <= now:
                    count = 0
                count += 1
                name = username or old_name or ""
            if count >= MAX_FAILS:
                locked_until = now + timedelta(hours=LOCK_HOURS)
                result = {"is_locked": True, "failed_count": count, "remaining_attempts": 0, "hours": LOCK_HOURS, "mins": 0}
            else:
                locked_until = None
                result = {"is_locked": False, "failed_count": count, "remaining_attempts": MAX_FAILS - count}
            if rid is not None:
                cur.execute(
                    f"UPDATE superadmin_superadminloginattempt SET username = {ph}, failed_count = {ph}, last_failed_at = {ph}, is_locked = {ph}, locked_until = {ph}, user_agent = {ph}, updated_at = {ph} WHERE id = {ph}",
                    (name, count, now, result["is_locked"], locked_until, ua, now, rid),
                )
            elif result["is_locked"]:                                   # a new row can only be locked when MAX_FAILS is 1
                cur.execute(f"UPDATE superadmin_superadminloginattempt SET is_locked = {ph}, locked_until = {ph} WHERE ip_address = {ph}", (True, locked_until, ip))
            conn.commit()
            return result
        finally:
            conn.close()

    # ---- a good login
    def clear_failed(self, ip, username):
        ph, now = self.ph, self.now()
        conn = self.connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE superadmin_superadminloginattempt SET failed_count = 0, is_locked = {ph}, locked_until = NULL, updated_at = {ph} WHERE ip_address = {ph} OR (username <> '' AND username = {ph})",
                (False, now, ip, username or ""),
            )
            conn.commit()
        finally:
            conn.close()

    # ---- the admin audit log
    def audit(self, action, target="", details="", ip=None):
        ph = self.ph
        try:
            conn = self.connect()
        except Exception:
            return
        try:
            cur = conn.cursor()
            cur.execute(
                f"INSERT INTO superadmin_auditlog (admin_user_id, action, target, details, ip_address, created_at) VALUES (NULL, {ph}, {ph}, {ph}, {ph}, {ph})",
                (action[:64], str(target)[:255], str(details), ip or None, self.now()),
            )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
        finally:
            conn.close()
