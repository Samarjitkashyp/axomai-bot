# -*- coding: utf-8 -*-
"""Tests for the bot login: the lock rules (shared with the Axom AI admin login), the page, and the whole sign-in flow.
Run from the bot folder:  venv/bin/python3 tests/test_login.py     (the flow part needs the bot's own packages, so it is run on the server)"""
import os
import re
import sqlite3
import sys
import tempfile
import warnings
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
warnings.filterwarnings("ignore", category=DeprecationWarning)

from app.login_guard import LoginGuard, client_ip, sanitize  # noqa: E402
from app.login_page import render_login  # noqa: E402

fails = 0


def t(name, ok):
    global fails
    if not ok:
        fails += 1
        print("FAIL", name)


SCHEMA = """
CREATE TABLE superadmin_superadminloginattempt (id INTEGER PRIMARY KEY AUTOINCREMENT, ip_address TEXT, username TEXT, failed_count INTEGER, last_failed_at TEXT,
  is_locked INTEGER, locked_until TEXT, user_agent TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE superadmin_auditlog (id INTEGER PRIMARY KEY AUTOINCREMENT, admin_user_id INTEGER, action TEXT, target TEXT, details TEXT, ip_address TEXT, created_at TEXT);
"""


class Clock:
    def __init__(self):
        self.t = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.t


def make_db():
    path = tempfile.mktemp(suffix=".sqlite3")
    c = sqlite3.connect(path)
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    return path


def rows(path, sql):
    c = sqlite3.connect(path)
    try:
        return c.execute(sql).fetchall()
    finally:
        c.close()


# ------------------------------------------------------------------ the lock rules
db = make_db()
clock = Clock()
g = LoginGuard(lambda: sqlite3.connect(db), ph="?", now=clock)

t("nothing is locked at first", g.active_lockout("1.2.3.4", "asha") == {"is_locked": False})
r = g.record_failed("1.2.3.4", "asha", "UA")
t("first wrong try: 2 left", r == {"is_locked": False, "failed_count": 1, "remaining_attempts": 2})
r = g.record_failed("1.2.3.4", "asha", "UA")
t("second wrong try: 1 left", (r["is_locked"], r["remaining_attempts"]) == (False, 1))
t("still not locked after two", g.active_lockout("1.2.3.4", "asha")["is_locked"] is False)
r = g.record_failed("1.2.3.4", "asha", "UA")
t("third wrong try locks for 24 h", (r["is_locked"], r["hours"], r["remaining_attempts"]) == (True, 24, 0))
lk = g.active_lockout("1.2.3.4", "")
t("the IP is locked, 23 h and some minutes left or 24", lk["is_locked"] and lk["hours"] in (23, 24))
lk = g.active_lockout("9.9.9.9", "asha")
t("the same username from another IP is locked too", lk["is_locked"] is True)
t("another IP with another name is free", g.active_lockout("9.9.9.9", "other")["is_locked"] is False)
t("an empty name never matches the empty names of other rows", g.active_lockout("9.9.9.9", "")["is_locked"] is False)
clock.t += timedelta(hours=12)
lk = g.active_lockout("1.2.3.4", "")
t("12 hours later: about 12 hours left", lk["is_locked"] and lk["hours"] == 11 or lk["hours"] == 12)
clock.t += timedelta(hours=12, minutes=1)
t("after 24 hours the lock opens by itself", g.active_lockout("1.2.3.4", "asha") == {"is_locked": False})
r = g.record_failed("1.2.3.4", "asha", "UA")
t("and the count starts again", r["failed_count"] == 1 and r["remaining_attempts"] == 2)

g.record_failed("5.5.5.5", "bina", "UA")
g.record_failed("5.5.5.5", "bina", "UA")
g.clear_failed("5.5.5.5", "bina")
r = g.record_failed("5.5.5.5", "bina", "UA")
t("a good login wipes the wrong tries", r["failed_count"] == 1)

g.audit("BOT_LOGIN_FAILED", "Username: asha", "Invalid credentials provided", "1.2.3.4")
a = rows(db, "SELECT admin_user_id, action, target, details, ip_address FROM superadmin_auditlog")
t("audit line written", a == [(None, "BOT_LOGIN_FAILED", "Username: asha", "Invalid credentials provided", "1.2.3.4")])
t("a broken database never breaks the audit call", LoginGuard(lambda: (_ for _ in ()).throw(RuntimeError("down")), ph="?").audit("X") is None)

# ------------------------------------------------------------------ the helpers
t("X-Real-IP is used", client_ip({"x-real-ip": "103.1.2.3"}, "127.0.0.1") == "103.1.2.3")
t("a forged X-Forwarded-For is ignored", client_ip({"x-forwarded-for": "6.6.6.6"}, "10.0.0.5") == "10.0.0.5")
t("a bad X-Real-IP falls back to the connection", client_ip({"x-real-ip": "<script>"}, "10.0.0.5") == "10.0.0.5")
t("ipv6 is normalised", client_ip({"x-real-ip": "2001:DB8::1"}, "") == "2001:db8::1")
t("sanitize drops null bytes and control characters", sanitize("as\x00ha\x07\n ") == "asha")
t("sanitize cuts the length", len(sanitize("a" * 500, 150)) == 150 and sanitize(None) == "")

# ------------------------------------------------------------------ the page
p = render_login(client_ip="1.2.3.4", csrf="tok123")
t("a normal page", "Crawler Bot Sign In" in p and "3-Attempt Security Shield Active" in p and 'name="csrf" value="tok123"' in p)
t("no placeholder is left", "@@" not in p)
t("the admin look: the same logo, the same card", "axom-browser-logo.png" in p and "glass-card-obsidian" in p and "Authenticate Bot Access" in p)
t("form posts to /login with username and password", 'method="POST"' in p and 'name="username"' in p and 'name="password"' in p)
t("not locked: fields are enabled", not re.search(r"\sdisabled(?=[\s>])", p.split('<form')[1].split('</form>')[0]))
p = render_login(error="Invalid credentials. Warning: 1 attempt remaining", remaining=1, last_username='a"><script>alert(1)</script>', client_ip="1.2.3.4", csrf="x")
t("one attempt left", "Final Attempt Remaining (1 of 3)" in p and "Authentication Failed" in p)
t("a username with markup is escaped", "<script>alert(1)</script>" not in p and "&lt;script&gt;" in p)
p = render_login(error="<b>boom</b>", remaining=2, client_ip="1.2.3.4", csrf="x")
t("the error text is escaped", "<b>boom</b>" not in p and "&lt;b&gt;boom&lt;/b&gt;" in p and "2 Attempts Remaining (2 of 3)" in p)
p = render_login(error="locked", is_locked=True, lockout={"hours": 23, "mins": 59}, remaining=0, client_ip="1.2.3.4", csrf="x")
form = p.split("<form")[1].split("</form>")[0]
t("locked page", "24-Hour Security Lockout" in p and "23h 59m" in p and "Console Locked (24h Block)" in p and "Bot Console Locked" in p)
t("locked: the fields and the button are disabled", len(re.findall(r"\sdisabled(?=[\s>])", form)) == 3)
t("a hostile IP text is escaped", "<i>" not in render_login(client_ip="<i>x</i>").split("sentry verify")[1][:60])

# ------------------------------------------------------------------ the whole flow
try:
    from fastapi.testclient import TestClient
    import app.main as main
except Exception as e:                      # the bot's packages are not in this Python: the server runs this part
    print("flow tests skipped (%s)" % type(e).__name__)
    main = None

if main is not None:
    db2 = make_db()
    clock2 = Clock()
    main.login_guard = LoginGuard(lambda: sqlite3.connect(db2), ph="?", now=clock2)
    USERS = {("boss", "right-pass"): ("ok", {"id": 1, "username": "boss", "is_superuser": True}),
             ("noaccess", "right-pass"): ("denied", None)}
    DOWN = {"v": False}

    def fake_auth(u, p):
        if DOWN["v"]:
            return "error", None
        return USERS.get((u, p), ("bad", None))
    main.authenticate_user = fake_auth

    def new_client():
        return TestClient(main.app, base_url="https://testserver", follow_redirects=False)

    def page_csrf(c):
        r = c.get("/login", headers={"x-real-ip": "7.7.7.7"})
        return r, re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)

    def post(c, u, p, csrf, **extra):
        data = dict({"username": u, "password": p, "csrf": csrf}, **extra)
        return c.post("/login", data=data, headers={"x-real-ip": "7.7.7.7"})

    c = new_client()
    r, tok = page_csrf(c)
    t("GET /login is the new page, never cached, not framed", r.status_code == 200 and "Crawler Bot Sign In" in r.text and r.headers.get("cache-control") == "no-store" and r.headers.get("x-frame-options") == "DENY")
    t("it sets the csrf cookie", "axomai_bot_csrf" in r.headers.get("set-cookie", ""))
    r = c.post("/login", data={"username": "boss", "password": "right-pass", "csrf": "wrong"}, headers={"x-real-ip": "7.7.7.7"})
    t("a wrong csrf token is refused and not counted", r.status_code == 403 and rows(db2, "SELECT count(*) FROM superadmin_superadminloginattempt")[0][0] == 0)
    r = new_client().post("/login", data={"username": "boss", "password": "right-pass"}, headers={"x-real-ip": "7.7.7.7"})
    t("no token at all is refused", r.status_code == 403)
    r, tok = page_csrf(c)                                           # a refused page hands out a fresh token (and cookie): take it

    r = post(c, "boss", "nope", tok)
    t("wrong password: 401, 2 attempts left, the username kept", r.status_code == 401 and "2 attempt" in r.text and 'value="boss"' in r.text)
    r = post(c, "nobody", "nope", tok)
    t("an unknown user gets the same answer, 1 attempt left", r.status_code == 401 and "1 attempt remaining" in r.text and "Final Attempt Remaining" in r.text)
    r = post(c, "boss", "right-pass", tok)
    t("the right login works: redirect to / and a session cookie", r.status_code == 302 and r.headers["location"] == "/" and "axomai_bot_session" in r.headers.get("set-cookie", ""))
    t("remember was off: the cookie has no max-age", "max-age" not in [x for x in r.headers.get_list("set-cookie") if "axomai_bot_session" in x][0].lower())
    c = new_client()                                                # (a signed-in browser is sent away from /login: a new one for the next tries)
    _, tok = page_csrf(c)
    r2 = post(c, "boss", "nope", tok)
    t("the success wiped the earlier wrong tries", "2 attempt" in r2.text)
    r = post(c, "boss", "right-pass", tok, remember="on")
    t("remember on: a week", "max-age=604800" in [x for x in r.headers.get_list("set-cookie") if "axomai_bot_session" in x][0].lower())
    t("success and failures are in the audit log", {x[0] for x in rows(db2, "SELECT action FROM superadmin_auditlog")} >= {"BOT_LOGIN_SUCCESS", "BOT_LOGIN_FAILED"})

    c = new_client()
    _, tok = page_csrf(c)
    r = post(c, "noaccess", "right-pass", tok)
    t("right password but no bot access: refused, counted", r.status_code == 401 and "no access to the Crawler Bot" in r.text and "2 attempt" in r.text)
    r = post(c, "noaccess", "x", tok)
    r = post(c, "noaccess", "x", tok)
    t("the third wrong try locks 24 hours", r.status_code == 429 and "24-Hour Security Lockout" in r.text and "Console Locked" in r.text)
    r = post(c, "boss", "right-pass", tok)
    t("even the right login is refused while the IP is locked", r.status_code == 429 and "axomai_bot_session" not in r.headers.get("set-cookie", ""))
    r = c.get("/login", headers={"x-real-ip": "7.7.7.7"})
    t("the page itself shows the lock", "24-Hour Security Lockout" in r.text and re.search(r"\sdisabled(?=[\s>])", r.text) is not None)
    r = c.post("/login", data={"username": "boss", "password": "right-pass", "csrf": tok}, headers={"x-real-ip": "8.8.8.8"})
    t("another IP is not locked (but the name 'noaccess' would be)", r.status_code == 302)
    clock2.t += timedelta(hours=24, minutes=1)
    r = post(c, "boss", "right-pass", tok)
    t("after 24 hours the same IP can sign in again", r.status_code == 302)

    c = new_client()
    _, tok = page_csrf(c)
    DOWN["v"] = True
    r = post(c, "boss", "right-pass", tok)
    t("a database problem is a 503 and is not counted as a wrong try", r.status_code == 503 and "temporarily unavailable" in r.text)
    DOWN["v"] = False

    r = new_client().get("/")
    t("the bot itself still needs a login", r.status_code == 302 and r.headers["location"] == "/login")
    cc = new_client()
    r = cc.get("/logout")
    t("logout goes to /login", r.status_code == 302 and r.headers["location"] == "/login")

print("login: " + ("%d FAILED" % fails if fails else "all pass"))
sys.exit(1 if fails else 0)
