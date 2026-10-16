"""Painel do proprietário. Tudo aqui exige login (e 2FA, se ativado)."""
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import quote

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template, request, session,
                   url_for)
from PIL import Image, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash

from . import hours
from .db import DEFAULT_SETTINGS, WEEKDAYS, audit, get_db, set_settings, settings
from . import cash
from .orders import (ACTION_LABELS, METHOD_LABELS, PAYMENT_LABELS, STATUS_LABELS, allowed_actions, brl, cancel_order,
                     courier_message, customer_message, expire_if_needed, get_items, get_order, maps_link,
                     set_status, wa_link)
from . import secretbox
from .payments import PaymentError, check_account, mp_credentials, provider
from .secretbox import encrypt
from .pricing import product_groups
from .pix import brcode, qr_png_b64
from .security import (clear_login_failures, ip_locked_seconds, login_required, register_login_failure, safe_next,
                       start_admin_session, totp_match, totp_new_secret, totp_uri)

bp = Blueprint("admin", __name__, url_prefix="/admin")

DUMMY_HASH = generate_password_hash("senha-que-nao-existe")
PENDING_2FA_SECONDS = 300
CATEGORY_ICONS = {"xis": "Xis", "burger": "Hambúrguer", "pastel": "Pastel", "batata": "Batata",
                  "combo": "Combo", "refri": "Bebida", "doce": "Doce", "lanche": "Outro"}


@bp.context_processor
def inject_store_state():
    status = hours.store_status(settings())
    try:
        cash_open = cash.current_session() is not None
    except Exception:  # noqa: BLE001 - tela de login antes do banco existir
        cash_open = False
    return {"store_open": status["open"], "store_status": status, "cash_open": cash_open}


def password_problem(password, username):
    if len(password) < 10:
        return "A senha precisa ter pelo menos 10 caracteres."
    if len(password) > 200:
        return "Senha longa demais."
    if username.lower() in password.lower():
        return "A senha não pode conter o nome de usuário."
    kinds = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if kinds < 3:
        return "Use pelo menos 3 destes: letra minúscula, maiúscula, número e símbolo."
    return None


def parse_money(raw):
    raw = (raw or "").replace("R$", "").strip()
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    try:
        cents = round(float(raw) * 100)
    except ValueError:
        return None
    return cents if 0 <= cents <= 1_000_000 else None


def confirm_identity(form):
    """Senha atual (e código 2FA, se ativo) para ações sensíveis."""
    user = g.admin
    if not check_password_hash(user["password_hash"], form.get("current_password", "")):
        return "Senha atual incorreta."
    if user["totp_enabled"]:
        counter = totp_match(user["totp_secret"], form.get("totp_code"), user["totp_last_counter"])
        if counter is None:
            return "Código do autenticador inválido."
        get_db().execute("UPDATE admin_users SET totp_last_counter = ? WHERE id = ?", (counter, user["id"]))
        get_db().commit()
    return None


# ---------- Login ----------

def _finish_login(user, next_url):
    clear_login_failures(user["id"])
    start_admin_session(user)
    audit("login_ok", actor=user["username"])
    return redirect(safe_next(next_url, url_for("admin.orders")))


@bp.route("/login", methods=["GET", "POST"])
def login():
    next_url = request.values.get("next", "")
    error = None
    if request.method == "POST":
        locked = ip_locked_seconds()
        username = request.form.get("username", "").strip().lower()[:40]
        password = request.form.get("password", "")[:200]
        if locked:
            error = f"Muitas tentativas erradas. Tente de novo em {locked // 60 + 1} min."
        else:
            user = get_db().execute("SELECT * FROM admin_users WHERE username = ?", (username,)).fetchone()
            ok = check_password_hash(user["password_hash"] if user else DUMMY_HASH, password)
            if user and user["locked_until"] > time.time():
                ok = False
                error = "Usuário bloqueado por excesso de tentativas. Aguarde 30 minutos."
            if ok and user:
                if user["totp_enabled"]:
                    session.clear()
                    session["pending_2fa"] = user["id"]
                    session["pending_at"] = time.time()
                    session["pending_next"] = next_url
                    return redirect(url_for("admin.login_2fa"))
                return _finish_login(user, next_url)
            register_login_failure(user)
            audit("login_falhou", f"usuário informado: {username}", actor=username or "?")
            error = error or "Usuário ou senha incorretos."
    return render_template("admin/login.html", error=error, next_url=next_url)


@bp.route("/login/2fa", methods=["GET", "POST"])
def login_2fa():
    uid = session.get("pending_2fa")
    if not uid or time.time() - session.get("pending_at", 0) > PENDING_2FA_SECONDS:
        session.clear()
        return redirect(url_for("admin.login"))
    error = None
    if request.method == "POST":
        user = get_db().execute("SELECT * FROM admin_users WHERE id = ?", (uid,)).fetchone()
        if ip_locked_seconds():
            error = "Muitas tentativas erradas. Aguarde alguns minutos."
        else:
            counter = totp_match(user["totp_secret"], request.form.get("code"), user["totp_last_counter"])
            if counter is not None:
                get_db().execute("UPDATE admin_users SET totp_last_counter = ? WHERE id = ?", (counter, uid))
                get_db().commit()
                return _finish_login(user, session.get("pending_next", ""))
            register_login_failure(user)
            audit("2fa_falhou", actor=user["username"])
            error = "Código inválido ou expirado."
    return render_template("admin/login_2fa.html", error=error)


@bp.post("/logout")
def logout():
    if session.get("admin_user"):
        audit("logout")
    session.clear()
    return redirect(url_for("admin.login"))


@bp.route("/senha", methods=["GET", "POST"])
@login_required
def change_password():
    error = None
    if request.method == "POST":
        user = g.admin
        new = request.form.get("new_password", "")
        if not check_password_hash(user["password_hash"], request.form.get("current_password", "")):
            error = "Senha atual incorreta."
        elif new != request.form.get("confirm_password", ""):
            error = "A confirmação não bate com a nova senha."
        elif check_password_hash(user["password_hash"], new):
            error = "A nova senha precisa ser diferente da atual."
        else:
            error = password_problem(new, user["username"])
        if not error:
            db = get_db()
            db.execute("UPDATE admin_users SET password_hash = ?, must_change_password = 0, "
                       "session_version = session_version + 1 WHERE id = ?",
                       (generate_password_hash(new), user["id"]))
            db.commit()
            audit("senha_alterada")
            start_admin_session(db.execute("SELECT * FROM admin_users WHERE id = ?", (user["id"],)).fetchone())
            flash("Senha alterada. Outros aparelhos conectados foram desconectados.")
            return redirect(url_for("admin.orders"))
    return render_template("admin/senha.html", error=error, forced=g.admin["must_change_password"])


# ---------- Pedidos ----------

@bp.get("/")
@login_required
def home():
    return redirect(url_for("admin.orders"))


@bp.get("/pedidos")
@login_required
def orders():
    return render_template("admin/pedidos.html", active="pedidos")


def _local_time(created_at):
    # Banco guarda UTC; loja está em UTC-3.
    h, m = int(created_at[11:13]), created_at[14:16]
    return f"{(h - 3) % 24:02d}:{m}"


@bp.get("/api/pedidos")
@login_required
def api_orders():
    rows = get_db().execute(
        "SELECT * FROM orders WHERE status NOT IN ('concluido', 'cancelado') "
        "OR created_at >= datetime('now', '-12 hours') ORDER BY id DESC LIMIT 200").fetchall()
    out = []
    for o in rows:
        o = expire_if_needed(o)
        out.append({
            "id": o["id"], "status": o["status"], "status_label": STATUS_LABELS[o["status"]],
            "time": _local_time(o["created_at"]), "name": o["customer_name"],
            "total": brl(o["total_cents"]), "fulfillment": o["fulfillment"],
            "payment": f"{METHOD_LABELS[o['pay_method']]} · {PAYMENT_LABELS[o['payment_status']]}",
            "paid": o["payment_status"] == "pago",
            "cancelled_by": o["cancelled_by"],
            "print_url": url_for("admin.order_print", order_id=o["id"]),
            "url": url_for("admin.order_detail", order_id=o["id"]),
        })
    return jsonify({"ok": True, "orders": out, "store_open": hours.store_status(settings())["open"]})


@bp.get("/pedidos/<int:order_id>")
@login_required
def order_detail(order_id):
    order = get_order(order_id)
    if not order:
        abort(404)
    order = expire_if_needed(order)
    items = get_items(order_id)
    cfg = settings()
    pix_charge = None
    if order["pay_when"] == "entrega" and order["pay_method"] == "pix" and cfg["pix_key"]:
        code = brcode(cfg["pix_key"], cfg["pix_receiver_name"], cfg["pix_city"], order["total_cents"],
                      f"NM{order['id']}")
        pix_charge = {"code": code, "qr": qr_png_b64(code)}
    return render_template(
        "admin/pedido.html", active="pedidos", order=order, items=items,
        actions=[(a, ACTION_LABELS[a]) for a in allowed_actions(order)],
        status_label=STATUS_LABELS[order["status"]], payment_label=PAYMENT_LABELS[order["payment_status"]],
        method_label=METHOD_LABELS[order["pay_method"]], maps_url=maps_link(order),
        customer_wa=wa_link(order["customer_phone"], customer_message(order)),
        courier_wa=("https://wa.me/?text=" + quote(courier_message(order, items))
                    if order["fulfillment"] == "entrega" else None),
        pix_charge=pix_charge, cancel_reasons=cash.CANCEL_REASONS, created=cash.local_label(order["created_at"]),
        can_cancel="cancelado" in allowed_actions(order),
    )


@bp.post("/pedidos/<int:order_id>/status")
@login_required
def order_status(order_id):
    order = get_order(order_id)
    if not order:
        abort(404)
    new = request.form.get("status")
    if new not in allowed_actions(order):
        flash("Essa mudança de status não é permitida.")
        return redirect(url_for("admin.order_detail", order_id=order_id))
    if new == "cancelado":
        reason = request.form.get("cancel_reason", "")
        if reason not in cash.CANCEL_REASONS:
            flash("Escolha o motivo do cancelamento.")
            return redirect(url_for("admin.order_detail", order_id=order_id))
        note = request.form.get("cancel_note", "").strip()[:80]
        cancel_order(order_id, f"{reason}: {note}" if note else reason, "loja")
        audit("pedido_cancelado", f"pedido {order_id} ({brl(order['total_cents'])}): {reason} {note}")
        flash(f"Pedido #{order_id} cancelado. Ele não entra no caixa.")
        return redirect(url_for("admin.order_detail", order_id=order_id))
    payment = "recebido" if new == "concluido" and order["payment_status"] == "na_entrega" else None
    set_status(order_id, new, payment)
    audit("pedido_status", f"pedido {order_id}: {order['status']} -> {new}")
    flash(f"Pedido #{order_id}: {STATUS_LABELS[new]}.")
    target = url_for("admin.order_detail", order_id=order_id)
    if new == "confirmado":
        target += "?imprimir=1"  # a tela do pedido abre a impressão sozinha
    return redirect(target)


@bp.get("/pedidos/<int:order_id>/imprimir")
@login_required
def order_print(order_id):
    order = get_order(order_id)
    if not order:
        abort(404)
    items = get_items(order_id)
    maps_url = maps_link(order)
    return render_template(
        "admin/imprimir.html", order=order, items=items, cfg=settings(),
        status_label=STATUS_LABELS[order["status"]], method_label=METHOD_LABELS[order["pay_method"]],
        created=cash.local_label(order["created_at"]), maps_qr=qr_png_b64(maps_url) if maps_url else None,
        paper=request.args.get("papel", "80"))


@bp.post("/loja/modo")
@login_required
def store_mode():
    mode = request.form.get("mode")
    if mode not in ("auto", "aberto", "fechado"):
        abort(400)
    cfg = settings()
    now = datetime.now(hours.BRT)
    until = None
    if mode == "aberto":
        until = hours.next_closing_at(cfg, now)
    elif mode == "fechado":
        until = hours.next_opening_at(cfg, now)
    set_settings({"open_mode": mode, "open_until": str(int(until.timestamp())) if until else ""})
    audit("loja_modo", f"{mode} até {until:%d/%m %H:%M}" if until else mode)
    when = f"{until:%H:%M}" if until else ""
    flash({"auto": "Loja seguindo o horário normal.",
           "aberto": f"Loja ABERTA agora. Fecha sozinha às {when} e volta ao horário normal." if until else
                     "Loja ABERTA agora.",
           "fechado": f"Loja FECHADA agora. Abre sozinha no próximo horário ({until:%d/%m} às {when})." if until else
                      "Loja FECHADA agora. Os clientes não conseguem pedir."}[mode])
    return redirect(safe_next(request.form.get("back"), url_for("admin.orders")))


# ---------- Cardápio ----------

@bp.get("/cardapio")
@login_required
def products():
    db = get_db()
    cats = db.execute("SELECT * FROM categories ORDER BY sort, id").fetchall()
    rows = db.execute("SELECT p.*, (SELECT COUNT(*) FROM ingredients i WHERE i.product_id = p.id) AS n_ing "
                      "FROM products p ORDER BY p.sort, p.id").fetchall()
    by_cat = {c["id"]: [] for c in cats}
    for p in rows:
        by_cat.setdefault(p["category_id"], []).append(p)
    return render_template("admin/cardapio.html", active="cardapio", cats=cats, by_cat=by_cat)


def _save_image(file):
    try:
        img = Image.open(file.stream)
        img.verify()
        file.stream.seek(0)
        img = Image.open(file.stream)
        if img.format not in ("JPEG", "PNG", "WEBP"):
            return None, "Envie uma imagem JPG, PNG ou WEBP."
        if img.width * img.height > 40_000_000:
            return None, "Imagem grande demais. Use uma foto de até 40 megapixels."
        img = img.convert("RGBA")
        img.thumbnail((900, 900))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return None, "Não consegui ler essa imagem."
    name = secrets.token_hex(12) + ".webp"
    img.save(os.path.join(current_app.config["UPLOAD_DIR"], name), "WEBP", quality=85)
    return name, None


def _delete_image(name):
    if name and not name.startswith("seed-") and re.fullmatch(r"[a-f0-9]{24}\.webp", name):
        try:
            os.remove(os.path.join(current_app.config["UPLOAD_DIR"], name))
        except OSError:
            pass


def _parse_groups(raw):
    """Grupos de opções vindos do editor (JSON). Retorna (grupos, erros)."""
    import json
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return [], ["Não consegui ler as opções. Recarregue a página."]
    if not isinstance(data, list) or len(data) > 10:
        return [], ["No máximo 10 grupos de opções."]
    groups, errors = [], []
    for g in data:
        name = str(g.get("name", "")).strip()[:40]
        opts = []
        for o in (g.get("options") or [])[:30]:
            oname = str(o.get("name", "")).strip()[:50]
            if not oname:
                continue
            price = parse_money(str(o.get("price") or "0"))
            if price is None:
                errors.append(f"Preço inválido na opção “{oname}”.")
                price = 0
            opts.append((oname, price))
        if not name and not opts:
            continue
        if not name:
            errors.append("Dê um nome para cada grupo de opções (ex.: Tamanho).")
        if not opts:
            errors.append(f"O grupo “{name}” precisa de pelo menos 1 opção.")
        try:
            gmin = max(0, min(int(g.get("min", 0)), 20))
            gmax = max(1, min(int(g.get("max", 1)), 20))
        except (TypeError, ValueError):
            gmin, gmax = 0, 1
        gmax = max(gmax, gmin, 1)
        groups.append({"name": name, "min": gmin, "max": min(gmax, max(len(opts), 1)), "options": opts})
    return groups, errors


@bp.route("/produtos/novo", methods=["GET", "POST"])
@bp.route("/produtos/<int:product_id>", methods=["GET", "POST"])
@login_required
def product_form(product_id=None):
    db = get_db()
    product = None
    if product_id:
        product = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not product:
            abort(404)
    cats = db.execute("SELECT * FROM categories ORDER BY sort, id").fetchall()
    ingredients = db.execute("SELECT * FROM ingredients WHERE product_id = ? ORDER BY sort, id",
                             (product_id,)).fetchall() if product_id else []
    groups = [{"name": g["name"], "min": g["min"], "max": g["max"],
               "options": [{"name": o["name"], "price": f"{o['price_cents'] / 100:.2f}".replace(".", ",")}
                           for o in g["options"]]}
              for g in product_groups(product_id)] if product_id else []
    errors = []
    if request.method == "POST":
        f = request.form
        name = f.get("name", "").strip()
        description = f.get("description", "").strip()
        price = parse_money(f.get("price"))
        old_price = parse_money(f.get("old_price")) if f.get("old_price", "").strip() else None
        try:
            category_id = int(f.get("category_id", 0))
        except ValueError:
            category_id = 0
        if not 2 <= len(name) <= 60:
            errors.append("Nome deve ter entre 2 e 60 caracteres.")
        if len(description) > 200:
            errors.append("Descrição com no máximo 200 caracteres.")
        if price is None:
            errors.append("Preço inválido. Exemplo: 32,90")
        if f.get("old_price", "").strip() and (old_price is None or (price is not None and old_price <= price)):
            errors.append("O preço “de” (antigo) precisa ser maior que o preço atual.")
        if category_id not in {c["id"] for c in cats}:
            errors.append("Escolha uma categoria.")
        names = f.getlist("ing_name")
        removable = f.getlist("ing_removable")
        ing_rows = []
        for i, ing in enumerate(names):
            ing = ing.strip()[:60]
            if ing:
                ing_rows.append((ing, 1 if i < len(removable) and removable[i] == "1" else 0))
        if len(ing_rows) > 40:
            errors.append("No máximo 40 ingredientes.")
        new_groups, group_errors = _parse_groups(f.get("groups_json"))
        errors += group_errors

        image = product["image"] if product else None
        upload = request.files.get("image")
        new_image = None
        if upload and upload.filename and not errors:
            new_image, err = _save_image(upload)
            if err:
                errors.append(err)
        if not errors:
            if new_image or f.get("remove_image"):
                _delete_image(image)
                image = new_image
            values = (category_id, name, description, price, image, 1 if f.get("allows_sauces") else 0,
                      1 if f.get("featured") else 0, old_price, 1 if f.get("active") else 0)
            if product:
                db.execute("UPDATE products SET category_id = ?, name = ?, description = ?, price_cents = ?, "
                           "image = ?, allows_sauces = ?, featured = ?, old_price_cents = ?, active = ?, "
                           "updated_at = datetime('now') WHERE id = ?", values + (product_id,))
                db.execute("DELETE FROM ingredients WHERE product_id = ?", (product_id,))
                db.execute("DELETE FROM option_groups WHERE product_id = ?", (product_id,))
                audit("produto_editado", f"#{product_id} {name} {brl(price)}")
            else:
                sort = db.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM products").fetchone()[0]
                cur = db.execute("INSERT INTO products (category_id, name, description, price_cents, image, "
                                 "allows_sauces, featured, old_price_cents, active, sort) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values + (sort,))
                product_id = cur.lastrowid
                audit("produto_criado", f"#{product_id} {name} {brl(price)}")
            for i, (ing, rem) in enumerate(ing_rows):
                db.execute("INSERT INTO ingredients (product_id, name, removable, sort) VALUES (?, ?, ?, ?)",
                           (product_id, ing, rem, i))
            for gi, grp in enumerate(new_groups):
                gid = db.execute("INSERT INTO option_groups (product_id, name, min_choices, max_choices, sort) "
                                 "VALUES (?, ?, ?, ?, ?)", (product_id, grp["name"], grp["min"], grp["max"], gi)).lastrowid
                for oi, (oname, oprice) in enumerate(grp["options"]):
                    db.execute("INSERT INTO options (group_id, name, price_cents, sort) VALUES (?, ?, ?, ?)",
                               (gid, oname, oprice, oi))
            db.commit()
            flash(f"“{name}” salvo.")
            return redirect(url_for("admin.products"))
        ingredients = [{"name": n, "removable": r} for n, r in ing_rows]
        groups = [{"name": g["name"], "min": g["min"], "max": g["max"],
                   "options": [{"name": n, "price": f"{p / 100:.2f}".replace(".", ",")} for n, p in g["options"]]}
                  for g in new_groups]
    return render_template("admin/produto.html", active="cardapio", product=product, cats=cats,
                           ingredients=ingredients, groups=groups, errors=errors, form=request.form)


@bp.post("/produtos/<int:product_id>/ativo")
@login_required
def product_toggle(product_id):
    db = get_db()
    db.execute("UPDATE products SET active = 1 - active WHERE id = ?", (product_id,))
    db.commit()
    audit("produto_ativo_alterado", f"#{product_id}")
    return redirect(url_for("admin.products"))


@bp.post("/produtos/<int:product_id>/excluir")
@login_required
def product_delete(product_id):
    db = get_db()
    p = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if p:
        db.execute("DELETE FROM products WHERE id = ?", (product_id,))
        db.commit()
        _delete_image(p["image"])
        audit("produto_excluido", f"#{product_id} {p['name']}")
        flash(f"“{p['name']}” excluído.")
    return redirect(url_for("admin.products"))


@bp.route("/categorias-e-molhos", methods=["GET", "POST"])
@login_required
def extras():
    db = get_db()
    if request.method == "POST":
        f = request.form
        action = f.get("action")
        name = f.get("name", "").strip()[:40]
        try:
            row_id = int(f.get("id", 0))
            sort = int(f.get("sort", 0) or 0)
        except ValueError:
            abort(400)
        active = 1 if f.get("active") else 0
        if action in ("cat_add", "cat_save"):
            icon = f.get("icon") if f.get("icon") in CATEGORY_ICONS else "lanche"
            if len(name) < 2:
                flash("Nome da categoria muito curto.")
            elif action == "cat_add":
                db.execute("INSERT INTO categories (name, icon, sort) VALUES (?, ?, ?)", (name, icon, sort))
            else:
                db.execute("UPDATE categories SET name = ?, icon = ?, sort = ?, active = ? WHERE id = ?",
                           (name, icon, sort, active, row_id))
        elif action == "cat_delete":
            if db.execute("SELECT COUNT(*) FROM products WHERE category_id = ?", (row_id,)).fetchone()[0]:
                flash("Essa categoria ainda tem produtos. Mova ou exclua os produtos antes.")
            else:
                db.execute("DELETE FROM categories WHERE id = ?", (row_id,))
        elif action in ("sauce_add", "sauce_save"):
            price = parse_money(f.get("price") or "0")
            if len(name) < 2 or price is None:
                flash("Confira nome e preço do molho.")
            elif action == "sauce_add":
                db.execute("INSERT INTO sauces (name, price_cents, sort) VALUES (?, ?, ?)", (name, price, sort))
            else:
                db.execute("UPDATE sauces SET name = ?, price_cents = ?, sort = ?, active = ? WHERE id = ?",
                           (name, price, sort, active, row_id))
        elif action == "sauce_delete":
            db.execute("DELETE FROM sauces WHERE id = ?", (row_id,))
        else:
            abort(400)
        db.commit()
        audit("cardapio_extras", f"{action} {name}")
        return redirect(url_for("admin.extras"))
    return render_template("admin/extras.html", active="extras", icons=CATEGORY_ICONS,
                           cats=db.execute("SELECT * FROM categories ORDER BY sort, id").fetchall(),
                           sauces=db.execute("SELECT * FROM sauces ORDER BY sort, id").fetchall())


# ---------- Caixa ----------

@bp.route("/caixa", methods=["GET", "POST"])
@login_required
def cash_page():
    user = g.admin["username"]
    session_row = cash.current_session()
    if request.method == "POST":
        action = request.form.get("action")
        amount = parse_money(request.form.get("amount") or "0")
        if amount is None:
            flash("Valor inválido. Exemplo: 150,00")
            return redirect(url_for("admin.cash_page"))
        if action == "abrir":
            if session_row:
                flash("O caixa já está aberto.")
            else:
                cash.open_session(amount, user)
                flash(f"Caixa aberto com {brl(amount)} de troco na gaveta. Bom trabalho!")
        elif action in ("sangria", "suprimento") and session_row:
            reason = request.form.get("reason", "").strip()[:80]
            if amount <= 0:
                flash("Informe um valor maior que zero.")
            else:
                cash.add_move(session_row["id"], action, amount, reason, user)
                flash(("Retirada" if action == "sangria" else "Reforço") + f" de {brl(amount)} registrada.")
        elif action == "fechar" and session_row:
            notes = request.form.get("notes", "").strip()[:300]
            cash.close_session(session_row, amount, notes, user)
            flash("Caixa fechado. Confira o relatório abaixo e imprima se quiser.")
            return redirect(url_for("admin.cash_session_page", session_id=session_row["id"]))
        else:
            abort(400)
        return redirect(url_for("admin.cash_page"))
    report = cash.session_report(session_row) if session_row else None
    recent = get_db().execute("SELECT * FROM cash_sessions WHERE closed_at IS NOT NULL ORDER BY id DESC LIMIT 10").fetchall()
    return render_template("admin/caixa.html", active="caixa", session=session_row, report=report, recent=recent,
                           local=cash.local_label, methods=cash.METHODS)


@bp.get("/caixa/<int:session_id>")
@login_required
def cash_session_page(session_id):
    row = get_db().execute("SELECT * FROM cash_sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        abort(404)
    report = cash.session_report(row)
    return render_template("admin/caixa_fechamento.html", active="caixa", session=row, report=report,
                           local=cash.local_label, methods=cash.METHODS, cfg=settings())


# ---------- Relatórios ----------

@bp.get("/relatorios")
@login_required
def reports_page():
    key = request.args.get("periodo", "hoje")
    start, end, title = cash.period_range(key, request.args.get("de"), request.args.get("ate"))
    summary = cash.summarize(start, end, by_day=(end - start).days > 1)
    sessions = cash.sessions_between(start, end)
    return render_template("admin/relatorios.html", active="relatorios", periods=cash.PERIODS, key=key,
                           title=title, summary=summary, sessions=sessions, local=cash.local_label,
                           methods=cash.METHODS, de=request.args.get("de") or f"{start:%Y-%m-%d}",
                           ate=request.args.get("ate") or f"{end - timedelta(days=1):%Y-%m-%d}")


# ---------- Configurações ----------

STORE_FIELDS = ["store_name", "store_address", "store_city", "pickup_eta", "delivery_eta", "store_instagram"]


def _mask(value):
    value = value or ""
    return value[:3] + "•••" + value[-2:] if len(value) > 6 else "•••"


@bp.route("/configuracoes", methods=["GET", "POST"])
@login_required
def config():
    cfg = settings()
    errors = []
    if request.method == "POST":
        f = request.form
        form_id = f.get("form")
        if form_id == "loja":
            values = {k: f.get(k, "").strip()[:120] for k in STORE_FIELDS}
            if len(values["store_name"]) < 2:
                errors.append("Informe o nome da loja.")
            try:
                lat, lng = float(f.get("store_lat")), float(f.get("store_lng"))
                if not (-34 <= lat <= 5.5 and -74 <= lng <= -34):
                    raise ValueError
                values["store_lat"], values["store_lng"] = f"{lat:.6f}", f"{lng:.6f}"
            except (TypeError, ValueError):
                errors.append("Marque a localização da loja no mapa.")
            for key, label in (("delivery_base_cents", "taxa base"), ("delivery_per_km_cents", "valor por km")):
                cents = parse_money(f.get(key))
                if cents is None:
                    errors.append(f"Valor inválido em {label}.")
                values[key] = str(cents or 0)
            for key, label in (("delivery_included_km", "km incluídos"), ("delivery_max_km", "raio máximo")):
                try:
                    km = float((f.get(key) or "").replace(",", "."))
                    if not 0 <= km <= 100:
                        raise ValueError
                    values[key] = f"{km:g}"
                except ValueError:
                    errors.append(f"Valor inválido em {label}.")
            values["store_instagram"] = re.sub(r"[^A-Za-z0-9._]", "", values["store_instagram"].lstrip("@"))[:30]
            for i, day in enumerate(WEEKDAYS):
                if f.get(f"closed_{i}"):
                    values[f"hours_{i}"] = ""
                    continue
                slot = f"{f.get(f'open_{i}', '')}-{f.get(f'close_{i}', '')}"
                if not hours.parse(slot):
                    errors.append(f"Horário inválido na {day.lower()}. Use hora de abrir e de fechar.")
                values[f"hours_{i}"] = slot
            if not errors:
                set_settings(values)
                audit("config_loja")
                flash("Configurações da loja salvas.")
                return redirect(url_for("admin.config"))
        elif form_id == "whatsapp":
            err = confirm_identity(f)
            whatsapp = re.sub(r"\D", "", f.get("store_whatsapp", ""))
            if len(whatsapp) in (10, 11):
                whatsapp = "55" + whatsapp
            if err:
                errors.append(err)
            if not 12 <= len(whatsapp) <= 13:
                errors.append("WhatsApp da loja inválido. Use DDD + número.")
            if not errors:
                if cfg["store_whatsapp"] != whatsapp:
                    audit("whatsapp_loja_ALTERADO", f"{_mask(cfg['store_whatsapp'])} -> {_mask(whatsapp)}")
                set_settings({"store_whatsapp": whatsapp})
                flash("WhatsApp da loja atualizado.")
                return redirect(url_for("admin.config"))
        else:
            abort(400)
    cfg_now = settings()
    week = []
    for i, day in enumerate(WEEKDAYS):
        slot = hours.parse(cfg_now[f"hours_{i}"])
        week.append({"i": i, "day": day, "closed": not slot,
                     "open": cfg_now[f"hours_{i}"][:5] if slot else "18:00",
                     "close": cfg_now[f"hours_{i}"][6:] if slot else "23:30"})
    return render_template("admin/config.html", active="config", cfg=cfg_now, errors=errors, week=week)


# ---------- Pagamentos ----------

@bp.route("/pagamentos", methods=["GET", "POST"])
@login_required
def payments_page():
    cfg = settings()
    errors = []
    if request.method == "POST":
        f = request.form
        action = f.get("action")
        if action == "mp_save":
            mode = "producao" if f.get("mp_mode") == "producao" else "teste"
            token = re.sub(r"\s", "", f.get("mp_token", ""))
            secret = re.sub(r"\s", "", f.get("mp_webhook_secret", ""))
            if current_app.config["MP_ACCESS_TOKEN"]:
                errors.append("O Mercado Pago está travado pelo servidor (arquivo .env). Altere lá.")
            if mode == "producao" and not g.admin["totp_enabled"]:
                errors.append("Para receber pagamentos REAIS, ative antes a verificação em duas etapas "
                              "(menu Segurança). Isso protege o seu dinheiro.")
            if not re.fullmatch(r"(APP_USR|TEST)-[A-Za-z0-9\-]{20,200}", token):
                errors.append("Access Token inválido. Ele começa com APP_USR- (produção) ou TEST- (teste).")
            elif mode == "producao" and token.startswith("TEST-"):
                errors.append("Esse é um token de TESTE. Para produção use o que começa com APP_USR-.")
            if secret and not re.fullmatch(r"[A-Za-z0-9]{16,128}", secret):
                errors.append("Assinatura secreta do webhook inválida.")
            if not errors:
                err = confirm_identity(f)
                if err:
                    errors.append(err)
            account = None
            if not errors:
                try:
                    account = check_account(token)
                except PaymentError as exc:
                    errors.append(str(exc))
            if not errors:
                set_settings({
                    "mp_mode": mode,
                    "mp_token_enc": encrypt(token),
                    "mp_webhook_secret_enc": encrypt(secret) if secret else cfg["mp_webhook_secret_enc"],
                    "mp_account": json.dumps(account, ensure_ascii=False),
                })
                audit("mercadopago_conta_ALTERADA",
                      f"modo={mode} conta={account.get('nickname')} {_mask(account.get('email', ''))} "
                      f"token={secretbox.mask(token)}")
                flash(f"Mercado Pago conectado ({'PRODUÇÃO' if mode == 'producao' else 'TESTE'}): "
                      f"conta {account.get('nickname') or account.get('email')}.")
                return redirect(url_for("admin.payments_page"))
        elif action == "mp_disconnect":
            err = confirm_identity(f)
            if err:
                errors.append(err)
            else:
                set_settings({"mp_token_enc": "", "mp_webhook_secret_enc": "",  # nosec B105 - apaga as credenciais
                              "mp_account": "", "mp_mode": "teste"})
                audit("mercadopago_DESCONECTADO")
                flash("Mercado Pago desconectado. O site voltou para o modo simulação.")
                return redirect(url_for("admin.payments_page"))
        elif action == "mp_check":
            creds = mp_credentials()
            if not creds:
                errors.append("Nenhuma conta conectada.")
            else:
                try:
                    acc = check_account(creds["token"])
                    flash(f"Conexão OK ✅ Conta: {acc.get('nickname') or acc.get('email')}")
                    return redirect(url_for("admin.payments_page"))
                except PaymentError as exc:
                    errors.append(str(exc))
        elif action == "pix_save":
            err = confirm_identity(f)
            pix_key = f.get("pix_key", "").strip()[:77]
            if err:
                errors.append(err)
            if pix_key and not re.fullmatch(r"[\w.+\-@]{5,77}", pix_key):
                errors.append("Chave Pix inválida.")
            if not errors:
                new = {"pix_key": pix_key,
                       "pix_receiver_name": f.get("pix_receiver_name", "").strip()[:25] or "NUMELHOR",
                       "pix_city": f.get("pix_city", "").strip()[:15] or "BRASIL"}
                changes = [f"{k}: {_mask(cfg[k])} -> {_mask(v)}" for k, v in new.items() if cfg[k] != v]
                set_settings(new)
                audit("pix_loja_ALTERADO", "; ".join(changes) or "sem mudanças")
                flash("Chave Pix atualizada. A alteração ficou registrada em Segurança.")
                return redirect(url_for("admin.payments_page"))
        else:
            abort(400)

    cfg = settings()
    creds = mp_credentials()
    try:
        account = json.loads(cfg["mp_account"]) if cfg["mp_account"] else None
    except ValueError:
        account = None
    return render_template(
        "admin/pagamentos.html", active="pagamentos", cfg=cfg, errors=errors, creds=creds, account=account,
        token_mask=secretbox.mask(creds["token"]) if creds else "",
        locked=bool(current_app.config["MP_ACCESS_TOKEN"]),
        webhook_url=current_app.config["PUBLIC_BASE_URL"] + "/webhooks/mercadopago",
        https=current_app.config["PUBLIC_BASE_URL"].startswith("https://"),
        has_2fa=bool(g.admin["totp_enabled"]))


# ---------- Segurança ----------

@bp.route("/seguranca", methods=["GET", "POST"])
@login_required
def security_page():
    db = get_db()
    user = g.admin
    errors, new_secret = [], None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "2fa_start":
            new_secret = totp_new_secret()
            session["totp_setup"] = new_secret
        elif action == "2fa_confirm":
            secret = session.get("totp_setup")
            if not secret:
                errors.append("Comece a configuração de novo.")
            else:
                counter = totp_match(secret, request.form.get("code"))
                if counter is None:
                    errors.append("Código não confere. Veja se o relógio do celular está certo e tente de novo.")
                    new_secret = secret
                else:
                    db.execute("UPDATE admin_users SET totp_secret = ?, totp_enabled = 1, totp_last_counter = ?, "
                               "session_version = session_version + 1 WHERE id = ?", (secret, counter, user["id"]))
                    db.commit()
                    session.pop("totp_setup", None)
                    audit("2fa_ativado")
                    start_admin_session(db.execute("SELECT * FROM admin_users WHERE id = ?",
                                                   (user["id"],)).fetchone())
                    flash("Verificação em duas etapas ativada.")
                    return redirect(url_for("admin.security_page"))
        elif action == "2fa_disable":
            err = confirm_identity(request.form)
            if err:
                errors.append(err)
            else:
                db.execute("UPDATE admin_users SET totp_enabled = 0, totp_secret = NULL WHERE id = ?", (user["id"],))
                db.commit()
                audit("2fa_DESATIVADO")
                flash("Verificação em duas etapas desativada.")
                return redirect(url_for("admin.security_page"))
        elif action == "logout_all":
            db.execute("UPDATE admin_users SET session_version = session_version + 1 WHERE id = ?", (user["id"],))
            db.commit()
            audit("sessoes_encerradas")
            session.clear()
            return redirect(url_for("admin.login"))
        else:
            abort(400)
    setup = None
    if new_secret:
        uri = totp_uri(new_secret, user["username"])
        setup = {"secret": new_secret, "qr": qr_png_b64(uri)}
    logs = db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 150").fetchall()
    return render_template("admin/seguranca.html", active="seguranca", user=g.admin, setup=setup, logs=logs,
                           errors=errors)
