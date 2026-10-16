import os
import secrets
from datetime import timedelta
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash

from . import db, security
from .orders import brl, group_options


def _bootstrap_admin():
    """Na hospedagem: cria o primeiro usuário do painel a partir de ADMIN_USERNAME/ADMIN_PASSWORD (só se não existir
    nenhum). A troca de senha continua obrigatória no primeiro acesso."""
    username = os.environ.get("ADMIN_USERNAME", "").strip().lower()
    password = os.environ.get("ADMIN_PASSWORD", "")
    if not username or not password:
        return
    conn = db.get_db()
    if conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]:
        return
    if len(password) < 12:
        raise RuntimeError("ADMIN_PASSWORD precisa ter pelo menos 12 caracteres")
    conn.execute("INSERT INTO admin_users (username, password_hash, must_change_password) VALUES (?, ?, 1)",
                 (username, generate_password_hash(password)))
    conn.commit()
    db.audit("admin_criado_pela_hospedagem", username, actor="sistema")


def create_app():
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env")

    app = Flask(__name__, instance_path=str(root / "instance"))
    os.makedirs(app.instance_path, exist_ok=True)

    secret_key = os.environ.get("SECRET_KEY", "")
    if len(secret_key) < 32:
        raise RuntimeError("Defina SECRET_KEY (32+ caracteres aleatórios) no arquivo .env")
    prod = os.environ.get("APP_ENV", "dev") == "production"
    # PUBLIC_BASE_URL manual tem prioridade (ex.: domínio próprio); no Render usa o endereço que ele fornece.
    base_url = (os.environ.get("PUBLIC_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL")
                or "http://127.0.0.1:5000").rstrip("/")
    if prod and not base_url.startswith("https://"):
        raise RuntimeError("Em produção o PUBLIC_BASE_URL precisa ser https://")

    app.config.update(
        SECRET_KEY=secret_key,
        PROD=prod,
        DATABASE=os.environ.get("DATABASE_PATH") or os.path.join(app.instance_path, "numelhor.db"),
        UPLOAD_DIR=os.environ.get("UPLOAD_DIR") or os.path.join(app.instance_path, "uploads"),
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,
        SEND_FILE_MAX_AGE_DEFAULT=60 * 60 * 24 * 30,
        SESSION_COOKIE_NAME="__Host-nm" if prod else "nm_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=prod,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        PUBLIC_BASE_URL=base_url,
        CONTACT_EMAIL=os.environ.get("CONTACT_EMAIL", ""),
        MP_ACCESS_TOKEN=os.environ.get("MP_ACCESS_TOKEN", "").strip(),
        MP_WEBHOOK_SECRET=os.environ.get("MP_WEBHOOK_SECRET", "").strip(),
        MP_SANDBOX=os.environ.get("MP_SANDBOX", "1") == "1",
        PIX_PAYER_FALLBACK_EMAIL=os.environ.get("PIX_PAYER_FALLBACK_EMAIL", "cliente@numelhor.com.br"),
    )
    if os.environ.get("TRUST_PROXY") == "1":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)
    security.init_app(app)

    from .admin import bp as admin_bp
    from .public import bp as public_bp
    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp)

    @app.url_defaults
    def static_version(endpoint, values):
        # ?v=<data do arquivo> nos arquivos estáticos: o navegador guarda em cache e pega a versão nova quando muda
        if endpoint == "static" and "filename" in values and "v" not in values:
            try:
                values["v"] = int(os.stat(os.path.join(app.static_folder, values["filename"])).st_mtime)
            except OSError:
                pass

    with app.app_context():
        _bootstrap_admin()

    app.jinja_env.filters["brl"] = brl
    app.jinja_env.filters["group_options"] = group_options

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(500)
    def handle_error(err):
        code = getattr(err, "code", 500)
        messages = {404: "Essa página não existe.", 413: "Arquivo grande demais (máx. 6 MB).",
                    500: "Deu ruim aqui do nosso lado. Tente de novo em instantes."}
        msg = getattr(err, "description", None) if code == 400 else messages.get(code, "Erro.")
        if request.path.startswith(("/api/", "/admin/api/")) or request.is_json:
            return {"ok": False, "error": msg}, code
        return render_template("erro.html", code=code, message=msg), code

    @app.cli.command("recarregar-cardapio")
    def reload_menu():
        """Apaga o cardápio atual e cadastra o cardápio padrão da loja (pedidos antigos são mantidos)."""
        conn = db.get_db()
        db.load_menu(conn, replace=True)
        conn.commit()
        db.audit("cardapio_recarregado", actor="terminal")
        click.echo("Cardápio recarregado.")

    @app.cli.command("criar-admin")
    @click.argument("username")
    def create_admin(username):
        """Cria (ou redefine) o usuário do proprietário com uma senha temporária."""
        username = username.strip().lower()
        password = secrets.token_urlsafe(12)
        conn = db.get_db()
        conn.execute(
            "INSERT INTO admin_users (username, password_hash, must_change_password) VALUES (?, ?, 1) "
            "ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash, "
            "must_change_password = 1, session_version = session_version + 1, failed_count = 0, locked_until = 0",
            (username, generate_password_hash(password)),
        )
        conn.commit()
        db.audit("admin_criado_ou_redefinido", username, actor="terminal")
        click.echo(f"Usuário: {username}\nSenha temporária: {password}\n"
                   "Ela será trocada obrigatoriamente no primeiro acesso.")

    return app
