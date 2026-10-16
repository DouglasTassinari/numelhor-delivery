"""Site do cliente: cardápio, checkout, página do pedido e retorno dos pagamentos."""
import json
import re
import secrets
from datetime import datetime

from flask import (Blueprint, abort, current_app, jsonify, redirect, render_template, request,
                   send_from_directory, url_for)

from . import geo, hours
from .db import audit, get_db, settings
from .orders import (METHOD_LABELS, PAYMENT_LABELS, STATUS_LABELS, apply_payment, brl, cancel_order,
                     customer_can_cancel, expire_if_needed, get_items, get_order, mark_paid, refresh_payment,
                     store_message, wa_link)
from .payments import PaymentError, provider
from .pricing import CartError, price_cart, product_groups
from .security import json_error, rate_limited

bp = Blueprint("public", __name__)
STARTED_AT = datetime.now(hours.BRT).strftime("%d/%m/%Y %H:%M")

ONLINE_METHODS = {"pix", "cartao"}
OFFLINE_METHODS = {"dinheiro", "pix", "debito", "credito"}
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,120}\.[A-Za-z]{2,}$")


def menu_data():
    db = get_db()
    cats = db.execute("SELECT * FROM categories WHERE active = 1 ORDER BY sort, id").fetchall()
    out, featured = [], []
    for c in cats:
        products = []
        for p in db.execute("SELECT * FROM products WHERE category_id = ? AND active = 1 ORDER BY sort, id",
                            (c["id"],)):
            ings = db.execute("SELECT id, name, removable FROM ingredients WHERE product_id = ? ORDER BY sort, id",
                              (p["id"],)).fetchall()
            groups = product_groups(p["id"])
            # Preço "a partir de": soma a opção mais barata dos grupos obrigatórios
            base_min = p["price_cents"] + sum(min(o["price_cents"] for o in g["options"]) for g in groups if g["min"])
            varies = any(g["min"] and len({o["price_cents"] for o in g["options"]}) > 1 for g in groups)
            item = {
                "id": p["id"], "name": p["name"], "description": p["description"],
                "price_cents": p["price_cents"], "from_cents": base_min, "price_varies": varies,
                "old_price_cents": p["old_price_cents"] if (p["old_price_cents"] or 0) > base_min else None,
                "allows_sauces": bool(p["allows_sauces"]), "featured": bool(p["featured"]),
                "image": url_for("public.upload", name=p["image"]) if p["image"] else None,
                "ingredients": [{"id": i["id"], "name": i["name"], "removable": bool(i["removable"])} for i in ings],
                "groups": groups, "icon": c["icon"],
            }
            products.append(item)
            if item["featured"]:
                featured.append(item)
        if products:
            out.append({"id": c["id"], "name": c["name"], "icon": c["icon"], "products": products})
    sauces = [dict(r) for r in db.execute(
        "SELECT id, name, price_cents FROM sauces WHERE active = 1 ORDER BY sort, id")]
    return {"categories": out, "sauces": sauces, "featured": featured}


def store_context():
    cfg = settings()
    status = hours.store_status(cfg)
    return {
        "cfg": cfg,
        "store_open": status["open"],
        "store_status": status,
        "hours_week": hours.week(cfg),
        "hours_summary": hours.summary(cfg),
        "today_idx": datetime.now(hours.BRT).weekday(),
        "whatsapp_url": wa_link(cfg["store_whatsapp"], f"Oi, {cfg['store_name']}! Quero fazer um pedido 🍔"),
    }


def structured_data(menu, cfg):
    """Dados para o Google (schema.org/Restaurant): endereço, horário, telefone e cardápio com preços."""
    base = current_app.config["PUBLIC_BASE_URL"]
    city_raw = cfg["store_city"]
    postal = re.search(r"\d{5}-?\d{3}", city_raw)
    region = re.search(r"\b([A-Z]{2})\b", city_raw)
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    opening = []
    for i, day in enumerate(days):
        slot = hours.parse(cfg[f"hours_{i}"])
        if slot:
            opening.append({"@type": "OpeningHoursSpecification", "dayOfWeek": f"https://schema.org/{day}",
                            "opens": cfg[f"hours_{i}"][:5], "closes": cfg[f"hours_{i}"][6:]})
    phone = re.sub(r"\D", "", cfg["store_whatsapp"])
    data = {
        "@context": "https://schema.org",
        "@type": "Restaurant",
        "name": cfg["store_name"],
        "url": base + "/",
        "image": [base + url_for("static", filename="img/og-image.jpg")],
        "logo": base + url_for("static", filename="img/icon-512.png"),
        "telephone": f"+{phone}" if phone else None,
        "priceRange": "$",
        "servesCuisine": ["Lanches", "Xis", "Hambúrguer", "Pastel"],
        "acceptsReservations": False,
        "address": {
            "@type": "PostalAddress",
            "streetAddress": cfg["store_address"],
            "addressLocality": city_raw.split(" - ")[0].split(",")[0].strip(),
            "addressRegion": region.group(1) if region else None,
            "postalCode": postal.group(0) if postal else None,
            "addressCountry": "BR",
        },
        "geo": {"@type": "GeoCoordinates", "latitude": float(cfg["store_lat"]), "longitude": float(cfg["store_lng"])},
        "openingHoursSpecification": opening,
        "sameAs": [f"https://www.instagram.com/{cfg['store_instagram']}/"] if cfg["store_instagram"] else [],
        "hasMenu": {
            "@type": "Menu",
            "hasMenuSection": [{
                "@type": "MenuSection",
                "name": c["name"],
                "hasMenuItem": [{
                    "@type": "MenuItem",
                    "name": p["name"],
                    "description": p["description"] or None,
                    "offers": {"@type": "Offer", "price": f"{p['from_cents'] / 100:.2f}", "priceCurrency": "BRL"},
                } for p in c["products"]],
            } for c in menu["categories"]],
        },
    }
    return data


@bp.get("/")
def index():
    menu = menu_data()
    ctx = store_context()
    return render_template("index.html", menu=menu, ld_json=structured_data(menu, ctx["cfg"]), **ctx)


@bp.get("/privacidade")
def privacy():
    return render_template("privacidade.html", **store_context())


@bp.get("/robots.txt")
def robots():
    base = current_app.config["PUBLIC_BASE_URL"]
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin",
        "Disallow: /api/",
        "Disallow: /pedido/",
        "Disallow: /checkout",
        "Disallow: /meus-pedidos",
        "Disallow: /simulador/",
        "Disallow: /webhooks/",
        f"Sitemap: {base}/sitemap.xml",
        "",
    ])
    return current_app.response_class(body, mimetype="text/plain")


@bp.get("/sitemap.xml")
def sitemap():
    base = current_app.config["PUBLIC_BASE_URL"]
    urls = "".join(f"<url><loc>{base}{path}</loc><changefreq>{freq}</changefreq><priority>{prio}</priority></url>"
                   for path, freq, prio in (("/", "daily", "1.0"), ("/privacidade", "yearly", "0.2")))
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return current_app.response_class(xml, mimetype="application/xml")


@bp.get("/manifest.webmanifest")
def manifest():
    cfg = settings()
    data = {
        "name": cfg["store_name"],
        "short_name": "NuMelhor",
        "description": "Peça xis, hambúrguer e pastel da NuMelhor.",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#fbf6ee",
        "theme_color": "#140d0b",
        "lang": "pt-BR",
        "icons": [
            {"src": url_for("static", filename="img/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": url_for("static", filename="img/icon-512.png"), "sizes": "512x512", "type": "image/png",
             "purpose": "any"},
        ],
    }
    return current_app.response_class(json.dumps(data, ensure_ascii=False), mimetype="application/manifest+json")


@bp.get("/saude")
def health():
    get_db().execute("SELECT 1").fetchone()
    return {"ok": True, "ligado_desde": STARTED_AT}


@bp.get("/checkout")
def checkout():
    cfg = settings()
    sim = provider().is_simulation
    return render_template("checkout.html", simulation=sim,
                           store={"lat": float(cfg["store_lat"]), "lng": float(cfg["store_lng"])},
                           **store_context())


@bp.get("/meus-pedidos")
def my_orders():
    return render_template("meus_pedidos.html", **store_context())


@bp.get("/uploads/<path:name>")
def upload(name):
    if not re.fullmatch(r"[A-Za-z0-9_\-]+\.(webp|png|jpg)", name):
        abort(404)
    resp = send_from_directory(current_app.config["UPLOAD_DIR"], name, max_age=86400)
    return resp


# ---------- Endereço ----------

@bp.get("/api/geo/search")
def geo_search():
    q = (request.args.get("q") or "").strip()
    if len(q) < 4 or len(q) > 120:
        return jsonify({"ok": True, "results": []})
    if rate_limited("geo", 40, 60):
        return json_error("Muitas buscas seguidas. Espere alguns segundos.", 429)
    cfg = settings()
    try:
        results = geo.search(q, float(cfg["store_lat"]), float(cfg["store_lng"]))
    except Exception:
        current_app.logger.exception("busca de endereço falhou")
        return json_error("A busca de endereço está fora do ar. Marque direto no mapa.", 502)
    return jsonify({"ok": True, "results": results})


@bp.get("/api/geo/reverse")
def geo_reverse():
    coords = geo.valid_coords(request.args.get("lat"), request.args.get("lng"))
    if not coords:
        return json_error("Coordenadas inválidas.")
    if rate_limited("geo", 40, 60):
        return json_error("Muitas buscas seguidas. Espere alguns segundos.", 429)
    try:
        return jsonify({"ok": True, **geo.reverse(*coords)})
    except Exception:
        current_app.logger.exception("endereço reverso falhou")
        return jsonify({"ok": True, "address": "", "number": ""})


# ---------- Pedido ----------

def _payload():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    return data


def _quote(data):
    lines, subtotal = price_cart(data.get("items"))
    fulfillment = data.get("fulfillment")
    fee, delivery = 0, None
    if fulfillment == "entrega":
        d = data.get("delivery") or {}
        delivery = geo.delivery_quote(settings(), d.get("lat"), d.get("lng"))
        if delivery["ok"]:
            fee = delivery["fee_cents"]
    return lines, subtotal, fee, delivery


@bp.post("/api/quote")
def api_quote():
    data = _payload()
    try:
        lines, subtotal, fee, delivery = _quote(data)
    except CartError as exc:
        return json_error(str(exc))
    return jsonify({"ok": True, "lines": lines, "subtotal_cents": subtotal, "fee_cents": fee,
                    "total_cents": subtotal + fee, "delivery": delivery})


def _text(value, field, min_len, max_len, required=True):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    if not value and not required:
        return None
    if not min_len <= len(value) <= max_len:
        raise CartError(f"Confira o campo: {field}.")
    return value


@bp.post("/api/pedidos")
def create_order():
    if rate_limited("order", 6, 600):
        return json_error("Muitos pedidos seguidos deste aparelho. Chame a loja no WhatsApp.", 429)
    cfg = settings()
    status = hours.store_status(cfg)
    if not status["open"]:
        return json_error(f"A loja está fechada agora ({status['label'].lower()}). Horários: {hours.summary(cfg)}.")
    data = _payload()
    try:
        customer = data.get("customer") or {}
        name = _text(customer.get("name"), "nome", 2, 60)
        phone = re.sub(r"\D", "", str(customer.get("phone") or ""))
        if not 10 <= len(phone) <= 13:
            raise CartError("Informe um WhatsApp válido com DDD.")
        email = _text(customer.get("email"), "e-mail", 5, 120, required=False)
        if email and not EMAIL_RE.match(email):
            raise CartError("E-mail inválido.")
        notes = _text(data.get("notes"), "observações", 0, 300, required=False)

        fulfillment = data.get("fulfillment")
        if fulfillment not in ("retirada", "entrega"):
            raise CartError("Escolha retirada ou entrega.")
        lines, subtotal, fee, delivery = _quote(data)

        addr = {"address": None, "address_number": None, "complement": None, "reference": None,
                "lat": None, "lng": None, "distance_km": None}
        if fulfillment == "entrega":
            if not delivery["ok"]:
                raise CartError(delivery["error"])
            d = data.get("delivery") or {}
            addr.update(
                address=_text(d.get("address"), "endereço", 3, 160),
                address_number=_text(d.get("number"), "número", 1, 12),
                complement=_text(d.get("complement"), "complemento", 0, 80, required=False),
                reference=_text(d.get("reference"), "ponto de referência", 0, 120, required=False),
                lat=delivery["lat"], lng=delivery["lng"], distance_km=delivery["distance_km"],
            )

        payment = data.get("payment") or {}
        when, method = payment.get("when"), payment.get("method")
        if when == "online":
            if method not in ONLINE_METHODS:
                raise CartError("Escolha Pix ou cartão para pagar pelo site.")
        elif when == "entrega":
            if method not in OFFLINE_METHODS:
                raise CartError("Escolha como vai pagar na entrega/retirada.")
        else:
            raise CartError("Escolha a forma de pagamento.")

        total = subtotal + fee
        change_for = None
        if when == "entrega" and method == "dinheiro" and payment.get("change_for"):
            raw = str(payment["change_for"]).replace("R$", "").replace(".", "").replace(",", ".").strip()
            try:
                change_for = round(float(raw) * 100)
            except ValueError:
                raise CartError("Valor do troco inválido.")
            if change_for < total or change_for > 200000:
                raise CartError("O troco precisa ser para um valor maior que o total do pedido.")
    except CartError as exc:
        return json_error(str(exc))

    token = secrets.token_urlsafe(18)
    db = get_db()
    cur = db.execute(
        """INSERT INTO orders (token, customer_name, customer_phone, customer_email, fulfillment, address,
           address_number, complement, reference, lat, lng, distance_km, notes, pay_when, pay_method,
           change_for_cents, subtotal_cents, delivery_fee_cents, total_cents, status, payment_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (token, name, phone, email, fulfillment, addr["address"], addr["address_number"], addr["complement"],
         addr["reference"], addr["lat"], addr["lng"], addr["distance_km"], notes, when, method, change_for,
         subtotal, fee, total,
         "aguardando_pagamento" if when == "online" else "novo",
         "pendente" if when == "online" else "na_entrega"),
    )
    order_id = cur.lastrowid
    for ln in lines:
        db.execute(
            """INSERT INTO order_items (order_id, product_id, name, unit_price_cents, qty, removed, sauces,
               options, notes, line_total_cents) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (order_id, ln["product_id"], ln["name"], ln["unit_price_cents"], ln["qty"],
             json.dumps(ln["removed"], ensure_ascii=False), json.dumps(ln["sauces"], ensure_ascii=False),
             json.dumps(ln["options"], ensure_ascii=False), ln["notes"] or None, ln["line_total_cents"]),
        )
    db.commit()

    if when == "online":
        order = get_order(order_id)
        prov = provider()
        try:
            started = prov.start_pix(order, cfg) if method == "pix" else prov.start_card(order, cfg)
        except PaymentError as exc:
            db.execute("UPDATE orders SET status = 'cancelado', payment_status = 'recusado' WHERE id = ?",
                       (order_id,))
            db.commit()
            return json_error(f"{exc} Tente de novo ou escolha pagar na entrega/retirada.", 502)
        db.execute(
            "UPDATE orders SET payment_provider = ?, payment_ref = ?, checkout_url = ?, pix_copy_paste = ?, "
            "pix_qr_b64 = ?, pix_expires_at = ? WHERE id = ?",
            (prov.name, started.get("payment_ref"), started.get("checkout_url"), started.get("pix_copy_paste"),
             started.get("pix_qr_b64"), started.get("pix_expires_at"), order_id),
        )
        db.commit()

    return jsonify({"ok": True, "id": order_id, "token": token, "total": brl(total),
                    "redirect": url_for("public.order_page", token=token)})


def order_state(order):
    cfg = settings()
    state = {
        "id": order["id"],
        "status": order["status"],
        "status_label": STATUS_LABELS[order["status"]],
        "payment_status": order["payment_status"],
        "payment_label": PAYMENT_LABELS[order["payment_status"]],
        "pay_when": order["pay_when"],
        "pay_method": order["pay_method"],
        "fulfillment": order["fulfillment"],
        "total": brl(order["total_cents"]),
        "created_at": order["created_at"],
        "whatsapp_url": None,
    }
    if order["status"] not in ("aguardando_pagamento", "cancelado"):
        state["whatsapp_url"] = wa_link(cfg["store_whatsapp"], store_message(order, get_items(order["id"])))
    return state


def _load(token):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{20,40}", token or ""):
        abort(404)
    order = get_order(token=token)
    if not order:
        abort(404)
    return expire_if_needed(order)


@bp.get("/pedido/<token>")
def order_page(token):
    order = _load(token)
    # Volta do Mercado Pago: nunca confia nos parâmetros da URL, só consulta a API.
    if request.args.get("payment_id") or request.args.get("collection_id"):
        order = refresh_payment(order, min_interval=0)
    return render_template("pedido.html", order=order, items=get_items(order["id"]),
                           can_cancel=customer_can_cancel(order),
                           state=order_state(order), method_labels=METHOD_LABELS,
                           simulation=provider().is_simulation, **store_context())


@bp.post("/pedido/<token>/cancelar")
def customer_cancel(token):
    order = _load(token)
    if customer_can_cancel(order):
        cancel_order(order["id"], "Cancelado pelo cliente", "cliente")
    return redirect(url_for("public.order_page", token=token))


@bp.get("/api/pedido/<token>")
def order_status(token):
    if rate_limited("status", 60, 60):
        return json_error("Calma! Tente de novo em instantes.", 429)
    order = refresh_payment(_load(token))
    return jsonify({"ok": True, **order_state(order)})


# ---------- Simulador (só existe quando o Mercado Pago não está configurado) ----------

def _sim_only():
    if not provider().is_simulation:
        abort(404)


@bp.get("/simulador/cartao/<token>")
def sim_card(token):
    _sim_only()
    order = _load(token)
    return render_template("sim_cartao.html", order=order, **store_context())


@bp.post("/simulador/<token>/<action>")
def sim_action(token, action):
    _sim_only()
    order = _load(token)
    if order["status"] != "aguardando_pagamento":
        return redirect(url_for("public.order_page", token=token))
    if action == "aprovar":
        mark_paid(order, f"SIM-{secrets.token_hex(4).upper()}")
    elif action == "recusar":
        get_db().execute("UPDATE orders SET payment_status = 'recusado' WHERE id = ?", (order["id"],))
        get_db().commit()
    else:
        abort(404)
    if request.headers.get("X-CSRF-Token"):
        return jsonify({"ok": True})
    return redirect(url_for("public.order_page", token=token))


# ---------- Webhook do Mercado Pago ----------

@bp.post("/webhooks/mercadopago")
def mp_webhook():
    prov = provider()
    if prov.is_simulation:
        return "", 204
    if rate_limited("webhook", 300, 60):
        return "", 429
    body = request.get_json(silent=True) or {}
    data_id = request.args.get("data.id") or str((body.get("data") or {}).get("id") or "")
    topic = request.args.get("type") or request.args.get("topic") or body.get("type")
    if not data_id.isdigit():
        return "", 200
    if not prov.verify_webhook(request.headers, data_id):
        audit("webhook_assinatura_invalida", f"data.id={data_id}", actor="mercadopago")
        return "", 401
    if topic and topic != "payment":
        return "", 200
    try:
        payment = prov.get_payment(data_id)
    except PaymentError:
        return "", 502  # o Mercado Pago tenta de novo depois
    ref = str(payment.get("external_reference") or "")
    order = get_order(token=ref) if ref else None
    if order:
        apply_payment(order, payment)
    return "", 200
