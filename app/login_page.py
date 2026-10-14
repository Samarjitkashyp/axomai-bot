"""The bot's sign-in page: the same screen as the Axom AI admin login (app/login.html is that page with the bot's wording).
render_login() fills the few parts that change: the attempts pill, the error / lockout banner, the button, the form's state."""
import os
from html import escape

_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "login.html")
with open(_TEMPLATE_PATH, encoding="utf-8") as _f:
    _TEMPLATE = _f.read()


def _pill(is_locked, remaining):
    if is_locked:
        cls, icon, text = "bg-rose-500/20 border border-rose-500/40 text-rose-300", "fa-lock text-rose-400", "Bot Console Locked (24 Hours)"
    elif remaining == 1:
        cls, icon, text = "bg-amber-500/20 border border-amber-500/40 text-amber-300 animate-pulse", "fa-triangle-exclamation text-amber-400", "Final Attempt Remaining (1 of 3)"
    elif remaining == 2:
        cls, icon, text = "bg-slate-800/80 border border-slate-700 text-slate-300", "fa-shield-halved text-emerald-400", "2 Attempts Remaining (2 of 3)"
    else:
        cls, icon, text = "bg-slate-800/80 border border-slate-700 text-slate-300", "fa-shield-halved text-emerald-400", "3-Attempt Security Shield Active"
    return (f'<div class="mt-3 inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-[11px] font-semibold {cls}">'
            f'<i class="fa-solid {icon}"></i><span>{escape(text)}</span></div>')


def _alert(error, is_locked, lockout):
    if is_locked:
        left = ""
        if lockout:
            left = ('<div class="pt-2 border-t border-rose-500/20 flex items-center justify-between text-[11px] font-mono text-rose-300">'
                    '<span>Time Remaining:</span>'
                    '<span class="font-bold text-rose-100 bg-rose-950/60 px-2 py-0.5 rounded border border-rose-500/30">'
                    f'{int(lockout.get("hours", 0))}h {int(lockout.get("mins", 0))}m</span></div>')
        return ('<div class="mb-5 p-4 rounded-2xl bg-rose-500/15 border border-rose-500/40 text-rose-200 text-xs sm:text-sm space-y-2 animate-shake shadow-xl shadow-rose-950/40">'
                '<div class="flex items-start gap-3">'
                '<div class="w-7 h-7 rounded-xl bg-rose-500/30 border border-rose-500/50 text-rose-300 flex items-center justify-center shrink-0 mt-0.5"><i class="fa-solid fa-ban text-sm"></i></div>'
                '<div class="space-y-1"><div class="font-black text-rose-100 uppercase tracking-wide flex items-center gap-2"><span>24-Hour Security Lockout</span></div>'
                f'<div class="text-xs text-rose-200 leading-relaxed">{escape(error or "")}</div></div></div>{left}</div>')
    if error:
        return ('<div class="mb-5 p-3.5 sm:p-4 rounded-2xl bg-rose-500/10 border border-rose-500/30 text-rose-200 text-xs sm:text-sm flex items-start gap-3 animate-shake shadow-lg shadow-rose-950/20">'
                '<div class="w-6 h-6 rounded-lg bg-rose-500/20 border border-rose-500/30 text-rose-400 flex items-center justify-center shrink-0 mt-0.5"><i class="fa-solid fa-circle-exclamation text-xs"></i></div>'
                f'<div class="space-y-0.5"><div class="font-bold text-rose-100">Authentication Failed</div><div class="text-[11px] text-rose-300 leading-relaxed">{escape(error)}</div></div></div>')
    return ""


def _button(is_locked):
    if is_locked:
        return '<i class="fa-solid fa-lock text-sm"></i><span>Console Locked (24h Block)</span>'
    return '<span>Authenticate Bot Access</span><i class="fa-solid fa-arrow-right text-xs"></i>'


def render_login(error=None, is_locked=False, lockout=None, remaining=3, client_ip="", last_username="", csrf=""):
    """The sign-in page as HTML. Everything that comes from a visitor or from the network is escaped."""
    page = _TEMPLATE
    for key, value in (                                   # what a visitor can type (the error text, the username) is filled last
        ("@@CSRF@@", escape(csrf or "", quote=True)),
        ("@@PILL@@", _pill(is_locked, remaining)),
        ("@@BUTTON@@", _button(is_locked)),
        ("@@DISABLED@@", "disabled" if is_locked else ""),
        ("@@CLIENT_IP@@", escape(client_ip or "")),
        ("@@ALERT@@", _alert(error, is_locked, lockout)),
        ("@@USERNAME@@", escape(last_username or "", quote=True)),
    ):
        page = page.replace(key, value)
    return page
