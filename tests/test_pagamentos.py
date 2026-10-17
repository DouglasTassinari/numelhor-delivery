"""Pagamento pelo site: modo simulação, webhook do Mercado Pago e código Pix.

O Mercado Pago nunca é chamado de verdade: as respostas dele são simuladas aqui.
"""
import json
import re

import pytest

from numelhor import payments
from numelhor.db import get_db
from numelhor.orders import apply_payment, get_order
from numelhor.pix import _crc16, brcode

from helpers import audit_events, csrf, mp_signature, order_payload, order_row, place_order

pytestmark = pytest.mark.usefixtures("store_open")

MP_TOKEN = "TEST-1234567890-abcdefghijklmnopqrstuvwxyz"
MP_SECRET = "segredoDoWebhookDeTeste123"


def online_order(client, menu, method="pix", qty=1):
    """Pedido de Refri Lata (R$ 6,00 cada) pago pelo site."""
    return place_order(client, order_payload([menu.item("Refri Lata", qty=qty, options=["Coca-Cola"])],
                                             when="online", method=method))


def sim(client, token, action):
    return client.post(f"/simulador/{token}/{action}", headers={"X-CSRF-Token": csrf(client)})


# ---------- Modo simulação ----------

def test_pedido_online_comeca_aguardando_pagamento(client, app, menu):
    data = online_order(client, menu)
    order = order_row(app, data["id"])
    assert (order["status"], order["payment_status"], order["payment_provider"]) == (
        "aguardando_pagamento", "pendente", "simulador")
    assert order["paid_at"] is None
    # Pix copia e cola com o valor do pedido (campo 54 = R$ 6.00)
    assert order["pix_copy_paste"].startswith("000201")
    assert "54046.00" in order["pix_copy_paste"]
    assert order["pix_qr_b64"]


def test_pedido_so_fica_pago_depois_da_confirmacao(client, app, menu):
    data = online_order(client, menu)
    state = client.get(f"/api/pedido/{data['token']}").get_json()
    assert (state["status"], state["payment_status"]) == ("aguardando_pagamento", "pendente")
    assert state["whatsapp_url"] is None  # a loja só recebe o pedido depois de pago

    resp = sim(client, data["token"], "aprovar")
    assert resp.get_json() == {"ok": True}
    order = order_row(app, data["id"])
    assert (order["status"], order["payment_status"]) == ("novo", "pago")
    assert order["paid_at"] is not None
    assert order["payment_ref"].startswith("SIM-")
    assert "pagamento_aprovado" in audit_events(app)


def test_pagamento_recusado_no_simulador(client, app, menu):
    data = online_order(client, menu)
    sim(client, data["token"], "recusar")
    order = order_row(app, data["id"])
    assert (order["status"], order["payment_status"]) == ("aguardando_pagamento", "recusado")


def test_simulador_sem_csrf_e_recusado(client, app, menu):
    data = online_order(client, menu)
    assert client.post(f"/simulador/{data['token']}/aprovar").status_code == 400
    assert order_row(app, data["id"])["payment_status"] == "pendente"


def test_acao_desconhecida_no_simulador(client, menu):
    data = online_order(client, menu)
    assert sim(client, data["token"], "pagar-de-graca").status_code == 404


def test_cartao_no_simulador(client, app, menu):
    data = online_order(client, menu, method="cartao")
    order = order_row(app, data["id"])
    assert order["checkout_url"] == f"/simulador/cartao/{data['token']}"
    assert client.get(order["checkout_url"]).status_code == 200
    sim(client, data["token"], "aprovar")
    assert order_row(app, data["id"])["payment_status"] == "pago"


def test_pedido_expirado_nao_pode_mais_ser_pago(client, app, menu):
    data = online_order(client, menu)
    with app.app_context():
        get_db().execute("UPDATE orders SET created_at = datetime('now', '-40 minutes') WHERE id = ?", (data["id"],))
        get_db().commit()
    sim(client, data["token"], "aprovar")
    order = order_row(app, data["id"])
    assert (order["status"], order["payment_status"]) == ("cancelado", "expirado")


def test_cliente_nao_cancela_pedido_ja_pago(client, app, menu):
    data = online_order(client, menu)
    sim(client, data["token"], "aprovar")
    client.post(f"/pedido/{data['token']}/cancelar", data={"csrf_token": csrf(client)})
    assert order_row(app, data["id"])["status"] == "novo"


def test_webhook_e_ignorado_no_modo_simulacao(client, app, menu):
    data = online_order(client, menu)
    assert client.post("/webhooks/mercadopago?data.id=1&type=payment").status_code == 204
    assert order_row(app, data["id"])["payment_status"] == "pendente"


# ---------- Mercado Pago (respostas simuladas) ----------

class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.text = data, status, json.dumps(data)

    def json(self):
        return self.data


@pytest.fixture
def mp(app, monkeypatch):
    """Simula a API do Mercado Pago. mp.enable() liga o Mercado Pago no app com um token de teste."""

    class FakeMP:
        def __init__(self):
            self.calls = []
            self.payments = {}      # id -> pagamento devolvido por GET /v1/payments/<id>
            self.create_status = 201

        def enable(self):
            app.config.update(MP_ACCESS_TOKEN=MP_TOKEN, MP_WEBHOOK_SECRET=MP_SECRET)

        def request(self, method, url, headers=None, timeout=None, **kwargs):
            self.calls.append({"method": method, "url": url, "headers": headers, **kwargs})
            path = url.replace(payments.MercadoPago.API, "")
            if method == "POST" and path == "/v1/payments":
                if self.create_status >= 400:
                    return FakeResponse({"message": "erro"}, self.create_status)
                return FakeResponse({"id": 555, "point_of_interaction": {"transaction_data": {
                    "qr_code": "00020101PIXDOMERCADOPAGO", "qr_code_base64": "QUJD"}}})
            match = re.fullmatch(r"/v1/payments/(\d+)", path)
            if method == "GET" and match and int(match.group(1)) in self.payments:
                return FakeResponse(self.payments[int(match.group(1))])
            return FakeResponse({"message": "not found"}, 404)

    fake = FakeMP()
    monkeypatch.setattr(payments.requests, "request", fake.request)
    return fake


@pytest.fixture
def waiting(client, menu, mp):
    """Pedido de R$ 6,00 criado no modo simulação; depois liga o Mercado Pago para receber o aviso (webhook)."""
    data = online_order(client, menu)
    mp.enable()
    mp.payments[987] = {"id": 987, "status": "approved", "transaction_amount": 6.0,
                        "external_reference": data["token"]}
    return data


def webhook(client, data_id, signature=None, request_id="req-123"):
    headers = {"x-request-id": request_id}
    if signature is not None:
        headers["x-signature"] = signature
    return client.post(f"/webhooks/mercadopago?data.id={data_id}&type=payment", headers=headers,
                       json={"type": "payment", "data": {"id": str(data_id)}})


def test_mercado_pago_recebe_o_valor_calculado_pelo_servidor(client, app, menu, mp):
    mp.enable()
    data = online_order(client, menu, qty=3)  # 3 x R$ 6,00
    [call] = [c for c in mp.calls if c["method"] == "POST"]
    assert call["json"]["transaction_amount"] == 18.0
    assert call["json"]["external_reference"] == data["token"]
    assert call["headers"]["X-Idempotency-Key"] == f"pix-{data['token']}"
    assert "notification_url" not in call["json"]  # http local: o Mercado Pago não conseguiria avisar
    order = order_row(app, data["id"])
    assert (order["payment_provider"], order["payment_ref"], order["pix_copy_paste"]) == (
        "mercadopago", "555", "00020101PIXDOMERCADOPAGO")


def test_erro_do_mercado_pago_cancela_o_pedido(client, app, menu, mp):
    mp.enable()
    mp.create_status = 400
    resp = client.post("/api/pedidos", headers={"X-CSRF-Token": csrf(client)}, json=order_payload(
        [menu.item("Água")], when="online", method="pix"))
    assert resp.status_code == 502
    with app.app_context():
        order = dict(get_db().execute("SELECT * FROM orders").fetchone())
    assert (order["status"], order["payment_status"]) == ("cancelado", "recusado")


def test_webhook_valido_com_valor_certo_confirma_o_pagamento(client, app, waiting, mp):
    order = waiting
    resp = webhook(client, 987, mp_signature(MP_SECRET, "987", "req-123"))
    assert resp.status_code == 200
    row = order_row(app, order["id"])
    assert (row["status"], row["payment_status"], row["payment_ref"]) == ("novo", "pago", "987")


def test_webhook_sem_assinatura_e_recusado(client, app, waiting, mp):
    order = waiting
    assert webhook(client, 987).status_code == 401
    assert order_row(app, order["id"])["payment_status"] == "pendente"
    assert "webhook_assinatura_invalida" in audit_events(app)
    assert not [c for c in mp.calls if c["method"] == "GET"]  # nem chega a consultar o pagamento


@pytest.mark.parametrize("signature", [
    "ts=1760000000,v1=" + "0" * 64,                          # assinatura inventada
    mp_signature("outro-segredo", "987", "req-123"),         # segredo errado
    mp_signature(MP_SECRET, "988", "req-123"),               # assinatura de outro pagamento
    mp_signature(MP_SECRET, "987", "outra-requisicao"),      # outra requisição
    "v1=abc",                                                # faltando o ts
])
def test_webhook_com_assinatura_invalida_e_recusado(client, app, waiting, mp, signature):
    order = waiting
    assert webhook(client, 987, signature).status_code == 401
    assert order_row(app, order["id"])["payment_status"] == "pendente"


def test_webhook_com_valor_diferente_nao_confirma(client, app, waiting, mp):
    order = waiting
    mp.payments[987]["transaction_amount"] = 5.99
    assert webhook(client, 987, mp_signature(MP_SECRET, "987", "req-123")).status_code == 200
    row = order_row(app, order["id"])
    assert (row["status"], row["payment_status"]) == ("aguardando_pagamento", "pendente")
    assert "pagamento_valor_divergente" in audit_events(app)


def test_webhook_de_pagamento_recusado(client, app, waiting, mp):
    order = waiting
    mp.payments[987]["status"] = "rejected"
    webhook(client, 987, mp_signature(MP_SECRET, "987", "req-123"))
    row = order_row(app, order["id"])
    assert (row["status"], row["payment_status"]) == ("aguardando_pagamento", "recusado")


def test_pagamento_de_outro_pedido_nao_vale(app, client, menu):
    a = online_order(client, menu)
    b = online_order(client, menu)
    with app.app_context():
        order_a = get_order(a["id"])
        assert apply_payment(order_a, {"id": 1, "status": "approved", "transaction_amount": 6.0,
                                       "external_reference": b["token"]}) is False
    assert order_row(app, a["id"])["payment_status"] == "pendente"


def test_volta_do_mercado_pago_nao_confia_na_url(client, app, menu, mp):
    """O cliente volta com ?status=approved na URL, mas a API diz que ainda está pendente."""
    mp.enable()
    data = online_order(client, menu)  # payment_ref = 555
    mp.payments[555] = {"id": 555, "status": "pending", "transaction_amount": 6.0,
                        "external_reference": data["token"]}
    client.get(f"/pedido/{data['token']}?payment_id=555&status=approved&collection_status=approved")
    assert order_row(app, data["id"])["payment_status"] == "pendente"
    # Quando a API confirma, aí sim fica pago
    mp.payments[555]["status"] = "approved"
    client.get(f"/pedido/{data['token']}?payment_id=555")
    assert order_row(app, data["id"])["payment_status"] == "pago"


def test_simulador_some_quando_o_mercado_pago_esta_ligado(client, app, waiting, mp):
    assert client.get(f"/simulador/cartao/{waiting['token']}").status_code == 404
    assert sim(client, waiting["token"], "aprovar").status_code == 404


def test_webhook_sem_segredo_e_recusado_em_producao(app):
    mp_prov = payments.MercadoPago(MP_TOKEN, "", "https://numelhor.onrender.com", sandbox=True)
    with app.app_context():
        app.config["PROD"] = True
        assert mp_prov.verify_webhook({}, "987") is False
        app.config["PROD"] = False
        assert mp_prov.verify_webhook({}, "987") is True  # no computador de testes, sem segredo, aceita


# ---------- Código Pix (BR Code) ----------

def test_crc16_do_pix():
    # Valor de referência do CRC-16/CCITT-FALSE para o texto "123456789"
    assert _crc16("123456789") == "29B1"


def test_codigo_pix_copia_e_cola():
    code = brcode("numelhor@exemplo.com", "NuMelhor Lanches", "Criciúma", 4250, "NM-7")
    assert code[:-4] == ("000201"
                         "26420014br.gov.bcb.pix0120numelhor@exemplo.com"
                         "52040000"
                         "5303986"
                         "540542.50"
                         "5802BR"
                         "5916NUMELHOR LANCHES"
                         "6008CRICIUMA"
                         "62070503NM7"
                         "6304")
    assert code[-4:] == _crc16(code[:-4])
