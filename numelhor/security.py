"""Proteções gerais: CSRF, cabeçalhos HTTP, limite de tentativas, 2FA (TOTP) e login do painel."""
import base64
import hashlib
import hmac
import secrets
import struct
import threading
import time
from collections import defaultdict, deque
from functools import wraps
from urllib.parse import quote, urlsplit

from flask import abort, current_app, g, jsonify, redirect, request, session, url_for

from .db import client_ip, get_db

ADMIN_IDLE_SECONDS = 2 * 60 * 60
IP_MAX_FAILS = 5
IP_WINDOW_SECONDS = 15 * 60
USER_MAX_FAILS = 10
USER_LOCK_SECONDS = 30 * 60

CSRF_EXEMPT = {"public.mp_webhook"}


# ---------- CSRF ----------

def csrf_token():
    tok = session.get("_csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["_csrf"] = tok
    return tok


def _check_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS") or request.endpoint in CSRF_EXEMPT:
        return
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    expected = session.get("_csrf") or ""
    if not expected or not hmac.compare_digest(expected, sent):
        abort(400, "Sua sessão expirou. Recarregue a página e tente de novo.")


# ---------- Cabeçalhos ----------

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data: blob: https://*.tile.openstreetmap.org",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


def _security_headers(resp):
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=(), payment=()"
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    if current_app.config["PROD"]:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.path.startswith(("/admin", "/api", "/pedido")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- Limite de requisições (memória do processo) ----------

_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def rate_limited(bucket, limit, per_seconds):
    """True quando o IP passou de `limit` chamadas em `per_seconds` para esse bucket."""
    key = (bucket, client_ip())
    now = time.time()
    with _hits_lock:
        q = _hits[key]
        while q and q[0] <= now - per_seconds:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        return False


def json_error(message, status=400):
    return jsonify({"ok": False, "error": message}), status


# ---------- Tentativas de login ----------

def ip_locked_seconds():
    row = get_db().execute("SELECT locked_until FROM login_throttle WHERE ip = ?", (client_ip(),)).fetchone()
    if row and row["locked_until"] > time.time():
        return int(row["locked_until"] - time.time())
    return 0


def register_login_failure(user=None):
    db = get_db()
    now = time.time()
    ip = client_ip()
    row = db.execute("SELECT * FROM login_throttle WHERE ip = ?", (ip,)).fetchone()
    if not row or now - row["window_start"] > IP_WINDOW_SECONDS:
        fails, start = 1, now
    else:
        fails, start = row["fails"] + 1, row["window_start"]
    locked = now + IP_WINDOW_SECONDS if fails >= IP_MAX_FAILS else 0
    db.execute(
        "INSERT INTO login_throttle (ip, fails, window_start, locked_until) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(ip) DO UPDATE SET fails = excluded.fails, window_start = excluded.window_start, "
        "locked_until = excluded.locked_until",
        (ip, fails, start, locked),
    )
    if user is not None:
        user_fails = user["failed_count"] + 1
        user_lock = now + USER_LOCK_SECONDS if user_fails >= USER_MAX_FAILS else user["locked_until"]
        db.execute("UPDATE admin_users SET failed_count = ?, locked_until = ? WHERE id = ?",
                   (0 if user_lock > now else user_fails, user_lock, user["id"]))
    db.commit()


def clear_login_failures(user_id):
    db = get_db()
    db.execute("DELETE FROM login_throttle WHERE ip = ?", (client_ip(),))
    db.execute("UPDATE admin_users SET failed_count = 0, locked_until = 0, last_login_at = datetime('now') "
               "WHERE id = ?", (user_id,))
    db.commit()


# ---------- 2FA (TOTP, compatível com Google Authenticator / Authy) ----------

def totp_new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _hotp(secret, counter):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_match(secret, code, last_counter=0):
    """Retorna o contador usado se o código bate (janela de ±30s), senão None. Impede reuso."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if len(code) != 6:
        return None
    now = int(time.time() // 30)
    for counter in (now - 1, now, now + 1):
        if counter > last_counter and hmac.compare_digest(_hotp(secret, counter), code):
            return counter
    return None


def totp_uri(secret, username, issuer="NuMelhor"):
    return (f"otpauth://totp/{quote(issuer)}:{quote(username)}"
            f"?secret={secret}&issuer={quote(issuer)}&digits=6&period=30")


# ---------- Sessão do painel ----------

def safe_next(target, fallback):
    """Só aceita caminhos internos, para não virar redirecionamento aberto."""
    if not target:
        return fallback
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return fallback
    return target


def start_admin_session(user):
    session.clear()
    session.permanent = True
    session["admin_id"] = user["id"]
    session["admin_user"] = user["username"]
    session["sv"] = user["session_version"]
    session["last_seen"] = time.time()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        uid = session.get("admin_id")
        user = None
        if uid:
            user = get_db().execute("SELECT * FROM admin_users WHERE id = ?", (uid,)).fetchone()
        expired = time.time() - session.get("last_seen", 0) > ADMIN_IDLE_SECONDS
        if not user or user["session_version"] != session.get("sv") or expired:
            session.clear()
            if request.path.startswith("/admin/api/"):
                return json_error("Sessão encerrada. Entre de novo.", 401)
            return redirect(url_for("admin.login", next=request.full_path.rstrip("?")))
        session["last_seen"] = time.time()
        g.admin = user
        if user["must_change_password"] and request.endpoint not in ("admin.change_password", "admin.logout"):
            return redirect(url_for("admin.change_password"))
        return view(*args, **kwargs)
    return wrapped


def init_app(app):
    app.before_request(_check_csrf)
    app.after_request(_security_headers)
    app.jinja_env.globals["csrf_token"] = csrf_token
