"""Pagamentos online.

- Sem MP_ACCESS_TOKEN no .env: modo SIMULAÇÃO (nenhum dinheiro real, botões para aprovar/recusar).
- Com MP_ACCESS_TOKEN: Mercado Pago de verdade (teste ou produção, conforme a credencial).

O site nunca recebe nem guarda dados de cartão: o cartão é digitado na página do Mercado Pago.
O dinheiro cai na conta Mercado Pago dona do token, que fica só no servidor (.env), fora do painel.
"""
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import requests
from flask import current_app, url_for

from .db import settings
from .pix import brcode, qr_png_b64
from .secretbox import decrypt

PAYMENT_WINDOW_MINUTES = 30
BRT = timezone(timedelta(hours=-3))


class PaymentError(Exception):
    pass


def _expiry():
    return datetime.now(timezone.utc) + timedelta(minutes=PAYMENT_WINDOW_MINUTES)


def _iso_utc(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class Simulator:
    name = "simulador"
    label = "Simulação (nenhum dinheiro real é movimentado)"
    is_simulation = True

    def start_pix(self, order, cfg):
        key = cfg["pix_key"] or "numelhor-demo@exemplo.com"
        code = brcode(key, cfg["pix_receiver_name"], cfg["pix_city"], order["total_cents"], f"NM{order['id']}")
        return {"payment_ref": f"SIM-PIX-{order['id']}", "pix_copy_paste": code,
                "pix_qr_b64": qr_png_b64(code), "pix_expires_at": _iso_utc(_expiry())}

    def start_card(self, order, cfg):
        return {"payment_ref": f"SIM-CARD-{order['id']}",
                "checkout_url": url_for("public.sim_card", token=order["token"])}

    def fetch_payments(self, order):
        return []


class MercadoPago:
    name = "mercadopago"
    is_simulation = False
    API = "https://api.mercadopago.com"

    def __init__(self, token, webhook_secret, base_url, sandbox):
        self.token = token
        self.webhook_secret = webhook_secret
        self.base_url = base_url
        self.sandbox = sandbox

    @property
    def label(self):
        return "Mercado Pago — " + ("TESTE (sandbox)" if self.sandbox else "PRODUÇÃO (dinheiro real)")

    def _request(self, method, path, idempotency_key=None, **kwargs):
        headers = {"Authorization": f"Bearer {self.token}"}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        try:
            r = requests.request(method, self.API + path, headers=headers, timeout=12, **kwargs)
        except requests.RequestException as exc:
            raise PaymentError("Não foi possível falar com o Mercado Pago agora.") from exc
        if r.status_code >= 400:
            current_app.logger.warning("Mercado Pago %s %s -> %s %s", method, path, r.status_code, r.text[:300])
            raise PaymentError("O Mercado Pago recusou a criação do pagamento.")
        return r.json()

    def _notification_url(self):
        # O Mercado Pago só consegue avisar um endereço público com HTTPS.
        if self.base_url.startswith("https://"):
            return self.base_url + "/webhooks/mercadopago"
        return None

    def start_pix(self, order, cfg):
        expires = _expiry()
        body = {
            "transaction_amount": round(order["total_cents"] / 100, 2),
            "description": f"Pedido #{order['id']} - {cfg['store_name']}",
            "payment_method_id": "pix",
            "external_reference": order["token"],
            "date_of_expiration": expires.astimezone(BRT).strftime("%Y-%m-%dT%H:%M:%S.000-03:00"),
            "payer": {
                "email": order["customer_email"] or current_app.config["PIX_PAYER_FALLBACK_EMAIL"],
                "first_name": order["customer_name"].split(" ")[0][:40],
            },
        }
        if self._notification_url():
            body["notification_url"] = self._notification_url()
        data = self._request("POST", "/v1/payments", idempotency_key=f"pix-{order['token']}", json=body)
        tx = (data.get("point_of_interaction") or {}).get("transaction_data") or {}
        if not tx.get("qr_code"):
            raise PaymentError("O Mercado Pago não devolveu o QR Code do Pix.")
        return {"payment_ref": str(data["id"]), "pix_copy_paste": tx["qr_code"],
                "pix_qr_b64": tx.get("qr_code_base64") or qr_png_b64(tx["qr_code"]),
                "pix_expires_at": _iso_utc(expires)}

    def start_card(self, order, cfg):
        back = self.base_url + url_for("public.order_page", token=order["token"])
        expires = _expiry().astimezone(BRT).strftime("%Y-%m-%dT%H:%M:%S.000-03:00")
        body = {
            "items": [{
                "id": f"pedido-{order['id']}",
                "title": f"Pedido #{order['id']} - {cfg['store_name']}",
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": round(order["total_cents"] / 100, 2),
            }],
            "external_reference": order["token"],
            "back_urls": {"success": back, "failure": back, "pending": back},
            "payment_methods": {
                "excluded_payment_types": [{"id": "ticket"}, {"id": "atm"}],
                "installments": 1,
            },
            "statement_descriptor": "NUMELHOR",
            "expires": True,
            "expiration_date_to": expires,
        }
        if back.startswith("https://"):
            body["auto_return"] = "approved"
        if self._notification_url():
            body["notification_url"] = self._notification_url()
        data = self._request("POST", "/checkout/preferences", idempotency_key=f"card-{order['token']}", json=body)
        url = data.get("sandbox_init_point") if self.sandbox else data.get("init_point")
        if not url:
            raise PaymentError("O Mercado Pago não devolveu o link de pagamento.")
        return {"payment_ref": data["id"], "checkout_url": url}

    def get_payment(self, payment_id):
        return self._request("GET", f"/v1/payments/{int(payment_id)}")

    def fetch_payments(self, order):
        """Consulta o Mercado Pago (fonte da verdade) pelos pagamentos deste pedido."""
        if order["pay_method"] == "pix" and order["payment_ref"]:
            return [self.get_payment(order["payment_ref"])]
        data = self._request("GET", "/v1/payments/search", params={
            "external_reference": order["token"], "sort": "date_created", "criteria": "desc"})
        return data.get("results", [])

    def verify_webhook(self, headers, data_id):
        """Confere a assinatura x-signature enviada pelo Mercado Pago."""
        if not self.webhook_secret:
            return not current_app.config["PROD"]  # em produção, sem segredo = recusa
        sig = headers.get("x-signature", "")
        request_id = headers.get("x-request-id", "")
        parts = dict(p.strip().split("=", 1) for p in sig.split(",") if "=" in p)
        ts, v1 = parts.get("ts"), parts.get("v1")
        if not ts or not v1:
            return False
        manifest = f"id:{str(data_id).lower()};request-id:{request_id};ts:{ts};"
        expected = hmac.new(self.webhook_secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, v1)


def mp_credentials():
    """Credenciais do Mercado Pago: as do servidor (.env) têm prioridade; senão, as cadastradas no painel."""
    cfg = current_app.config
    if cfg["MP_ACCESS_TOKEN"]:
        return {"token": cfg["MP_ACCESS_TOKEN"], "secret": cfg["MP_WEBHOOK_SECRET"], "sandbox": cfg["MP_SANDBOX"],
                "source": "servidor"}
    s = settings()
    token = decrypt(s["mp_token_enc"])
    if not token:
        return None
    return {"token": token, "secret": decrypt(s["mp_webhook_secret_enc"]), "sandbox": s["mp_mode"] != "producao",
            "source": "painel"}


def provider():
    creds = mp_credentials()
    if creds:
        return MercadoPago(creds["token"], creds["secret"], current_app.config["PUBLIC_BASE_URL"], creds["sandbox"])
    return Simulator()


def check_account(token):
    """Confere o token direto no Mercado Pago e devolve os dados da conta que vai receber."""
    try:
        r = requests.get(MercadoPago.API + "/users/me", headers={"Authorization": f"Bearer {token}"}, timeout=12)
    except requests.RequestException as exc:
        raise PaymentError("Não foi possível falar com o Mercado Pago agora. Tente de novo.") from exc
    if r.status_code in (401, 403):
        raise PaymentError("O Mercado Pago recusou esse Access Token. Confira se copiou ele inteiro.")
    if r.status_code >= 400:
        raise PaymentError(f"O Mercado Pago respondeu com erro {r.status_code}.")
    data = r.json()
    if data.get("site_id") and data["site_id"] != "MLB":
        raise PaymentError("Essa conta não é do Mercado Pago Brasil.")
    return {"id": data.get("id"), "nickname": data.get("nickname") or "", "email": data.get("email") or "",
            "name": " ".join(p for p in [data.get("first_name"), data.get("last_name")] if p)}
