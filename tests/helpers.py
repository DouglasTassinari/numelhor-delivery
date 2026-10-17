"""Funções de apoio usadas pelos testes."""
import hashlib
import hmac
import secrets

from numelhor.db import get_db

ADMIN_USER = "dono"
ADMIN_PASSWORD = "Lanche#Forte2026"
BASE_URL = "http://127.0.0.1:5077"

# Um ponto qualquer em Criciúma; a distância de verdade vem do road_km() de cada teste.
LAT, LNG = -28.6800, -49.3700


class Menu:
    """Acha no cardápio padrão o ID de produtos, opções, ingredientes e molhos pelo nome."""

    def __init__(self, app):
        self.app = app

    def _one(self, sql, args):
        with self.app.app_context():
            row = get_db().execute(sql, args).fetchone()
        assert row, f"Não achei no cardápio: {args}"
        return row[0]

    def product(self, name):
        return self._one("SELECT id FROM products WHERE name = ?", (name,))

    def option(self, product, name):
        return self._one("SELECT o.id FROM options o JOIN option_groups g ON g.id = o.group_id "
                         "JOIN products p ON p.id = g.product_id WHERE p.name = ? AND o.name = ?", (product, name))

    def ingredient(self, product, name):
        return self._one("SELECT i.id FROM ingredients i JOIN products p ON p.id = i.product_id "
                         "WHERE p.name = ? AND i.name = ?", (product, name))

    def sauce(self, name):
        return self._one("SELECT id FROM sauces WHERE name = ?", (name,))

    def item(self, product, qty=1, options=(), removed=(), sauces=(), **extra):
        """Uma linha da sacola do jeito que o navegador manda (só IDs e quantidade)."""
        return {"product_id": self.product(product), "qty": qty,
                "options": [self.option(product, o) for o in options],
                "removed": [self.ingredient(product, i) for i in removed],
                "sauces": [self.sauce(s) for s in sauces], **extra}


def csrf(client):
    """Garante um token CSRF na sessão do navegador de teste e devolve ele."""
    with client.session_transaction() as sess:
        sess.setdefault("_csrf", secrets.token_urlsafe(16))
        token = sess["_csrf"]
    return token


def order_payload(items, fulfillment="retirada", when="entrega", method="dinheiro", change_for="",
                  customer=None, delivery=None, **extra):
    payload = {
        "items": items,
        "customer": customer or {"name": "Maria da Silva", "phone": "(48) 99999-1234", "email": ""},
        "fulfillment": fulfillment,
        "payment": {"when": when, "method": method, "change_for": change_for},
        "notes": "",
        **extra,
    }
    if fulfillment == "entrega":
        payload["delivery"] = delivery or {"lat": LAT, "lng": LNG, "address": "Rua Henrique Lage - Centro",
                                           "number": "100", "complement": "", "reference": ""}
    return payload


def post_order(client, payload, ip=None):
    kwargs = {"environ_base": {"REMOTE_ADDR": ip}} if ip else {}
    return client.post("/api/pedidos", json=payload, headers={"X-CSRF-Token": csrf(client)}, **kwargs)


def place_order(client, payload, ip=None):
    """Faz o pedido e confere que deu certo. Devolve a resposta em JSON (id, token, total...)."""
    resp = post_order(client, payload, ip)
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["ok"] is True
    return data


def order_row(app, order_id):
    with app.app_context():
        return dict(get_db().execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())


def item_rows(app, order_id):
    with app.app_context():
        return [dict(r) for r in get_db().execute("SELECT * FROM order_items WHERE order_id = ? ORDER BY id",
                                                  (order_id,))]


def audit_events(app):
    with app.app_context():
        return [r["event"] for r in get_db().execute("SELECT event FROM audit_log ORDER BY id")]


def login(client, username=ADMIN_USER, password=ADMIN_PASSWORD, next_url=None, ip=None):
    url = "/admin/login" + (f"?next={next_url}" if next_url else "")
    kwargs = {"environ_base": {"REMOTE_ADDR": ip}} if ip else {}
    return client.post(url, data={"csrf_token": csrf(client), "username": username, "password": password},
                       **kwargs)


def admin_post(client, url, **data):
    return client.post(url, data={"csrf_token": csrf(client), **data})


def mp_signature(secret, data_id, request_id, ts="1760000000"):
    """Assinatura x-signature no formato documentado pelo Mercado Pago."""
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    v1 = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return f"ts={ts},v1={v1}"
