"""Criação de pedido pela API pública (/api/pedidos)."""
import json
import re
from datetime import datetime

import pytest

from numelhor.db import get_db, set_settings
from numelhor.hours import BRT

from helpers import csrf, item_rows, order_payload, order_row, place_order, post_order

pytestmark = pytest.mark.usefixtures("store_open")


def error_of(resp):
    assert resp.status_code == 400, resp.get_json()
    return resp.get_json()["error"]


# ---------- O servidor calcula tudo sozinho ----------

def test_servidor_ignora_precos_e_total_vindos_do_navegador(client, app, menu):
    items = [menu.item("X-Burguer", qty=2, price_cents=1, unit_price_cents=1, line_total_cents=1),
             menu.item("Refri Lata", options=["Coca-Cola"], unit_price_cents=0)]
    payload = order_payload(items, total_cents=1, subtotal_cents=1, total="R$ 0,01", fee_cents=0)
    data = place_order(client, payload)
    assert data["total"] == "R$ 42,00"
    order = order_row(app, data["id"])
    assert (order["subtotal_cents"], order["delivery_fee_cents"], order["total_cents"]) == (4200, 0, 4200)
    assert [(i["name"], i["qty"], i["unit_price_cents"], i["line_total_cents"]) for i in item_rows(app, data["id"])] == [
        ("X-Burguer", 2, 1800, 3600), ("Refri Lata", 1, 600, 600)]


def test_taxa_de_entrega_e_calculada_no_servidor(client, app, menu, road_km):
    road_km(6.5)
    payload = order_payload([menu.item("Pastel de Carne", qty=3)], fulfillment="entrega")
    payload["delivery"]["fee_cents"] = 0
    payload["delivery"]["distance_km"] = 0.1
    data = place_order(client, payload)
    order = order_row(app, data["id"])
    assert (order["subtotal_cents"], order["delivery_fee_cents"], order["total_cents"]) == (2100, 1250, 3350)
    assert order["distance_km"] == 6.5
    assert order["address"] == "Rua Henrique Lage - Centro"
    assert order["address_number"] == "100"


def test_pedido_guarda_adicionais_retirados_e_molhos(client, app, menu):
    item = menu.item("X Salada", options=["Bacon"], removed=["Tomate"], sauces=["Ketchup"], notes="sem sal")
    data = place_order(client, order_payload([item]))
    [row] = item_rows(app, data["id"])
    assert row["unit_price_cents"] == 2500
    assert json.loads(row["removed"]) == ["Tomate"]
    assert json.loads(row["sauces"]) == [{"name": "Ketchup", "price_cents": 0}]
    assert json.loads(row["options"]) == [{"group": "Adicionais", "name": "Bacon", "price_cents": 500}]
    assert row["notes"] == "sem sal"


def test_pedido_na_entrega_comeca_como_novo(client, app, menu):
    data = place_order(client, order_payload([menu.item("X-Burguer")]))
    order = order_row(app, data["id"])
    assert (order["status"], order["payment_status"], order["pay_when"], order["pay_method"]) == (
        "novo", "na_entrega", "entrega", "dinheiro")
    assert order["customer_phone"] == "48999991234"
    assert re.fullmatch(r"[A-Za-z0-9_\-]{24}", data["token"])
    assert data["redirect"] == f"/pedido/{data['token']}"


def test_troco_em_dinheiro(client, app, menu):
    data = place_order(client, order_payload([menu.item("X-Burguer")], change_for="50,00"))
    assert order_row(app, data["id"])["change_for_cents"] == 5000


def test_troco_menor_que_o_total_e_recusado(client, menu):
    resp = post_order(client, order_payload([menu.item("X-Burguer")], change_for="10"))
    assert "troco precisa ser para um valor maior" in error_of(resp)


def test_carrinho_com_erro_nao_cria_pedido(client, app, menu):
    resp = post_order(client, order_payload([menu.item("Refri Lata")]))
    assert "Falta escolher sabor" in error_of(resp)
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_entrega_fora_da_area_e_recusada(client, menu, road_km):
    road_km(12)
    resp = post_order(client, order_payload([menu.item("X-Burguer")], fulfillment="entrega"))
    assert "entrega até 10 km" in error_of(resp)


# ---------- Loja fechada ----------

def test_recusa_pedido_com_a_loja_fechada_pelo_painel(client, app, menu):
    with app.app_context():
        set_settings({"open_mode": "fechado", "open_until": ""})
    resp = post_order(client, order_payload([menu.item("X-Burguer")]))
    assert error_of(resp).startswith("A loja está fechada agora")


def test_recusa_pedido_fora_do_horario(client, app, menu, freeze_time):
    with app.app_context():
        set_settings({"open_mode": "auto"})
    freeze_time(datetime(2026, 10, 12, 20, 0, tzinfo=BRT))  # segunda, dia de folga
    resp = post_order(client, order_payload([menu.item("X-Burguer")]))
    assert error_of(resp) == ("A loja está fechada agora (fechado · abre amanhã às 18h). "
                              "Horários: Ter a Qui, 18h às 23h30 · Sex e Sáb, 18h às 23h59 · Dom, 18h às 23h30.")


def test_aceita_pedido_dentro_do_horario(client, app, menu, freeze_time):
    with app.app_context():
        set_settings({"open_mode": "auto"})
    freeze_time(datetime(2026, 10, 16, 20, 0, tzinfo=BRT))  # sexta à noite
    place_order(client, order_payload([menu.item("X-Burguer")]))


# ---------- Campos obrigatórios ----------

@pytest.mark.parametrize("change, message", [
    ({"customer": {"name": "", "phone": "48999991234"}}, "Confira o campo: nome."),
    ({"customer": {"name": "M", "phone": "48999991234"}}, "Confira o campo: nome."),
    ({"customer": {"name": "Maria", "phone": "1234"}}, "Informe um WhatsApp válido com DDD."),
    ({"customer": {"name": "Maria", "phone": "48999991234", "email": "maria@"}}, "E-mail inválido."),
    ({"fulfillment": "drone"}, "Escolha retirada ou entrega."),
    ({"payment": {}}, "Escolha a forma de pagamento."),
    ({"payment": {"when": "online", "method": "dinheiro"}}, "Escolha Pix ou cartão para pagar pelo site."),
    ({"payment": {"when": "entrega", "method": "cheque"}}, "Escolha como vai pagar na entrega/retirada."),
    ({"items": []}, "Sua sacola está vazia."),
])
def test_campos_obrigatorios(client, menu, change, message):
    payload = {**order_payload([menu.item("X-Burguer")]), **change}
    assert error_of(post_order(client, payload)) == message


@pytest.mark.parametrize("field, message", [("address", "Confira o campo: endereço."),
                                            ("number", "Confira o campo: número.")])
def test_entrega_exige_endereco_e_numero(client, menu, road_km, field, message):
    road_km(1)
    payload = order_payload([menu.item("X-Burguer")], fulfillment="entrega")
    payload["delivery"][field] = " "
    assert error_of(post_order(client, payload)) == message


def test_entrega_exige_ponto_no_mapa(client, menu):
    payload = order_payload([menu.item("X-Burguer")], fulfillment="entrega")
    payload["delivery"]["lat"] = None
    assert error_of(post_order(client, payload)) == "Marque o local de entrega no mapa."


def test_corpo_que_nao_e_json(client):
    resp = client.post("/api/pedidos", data="oi", headers={"X-CSRF-Token": csrf(client)})
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


# ---------- Limite por aparelho ----------

def test_limite_de_6_pedidos_por_aparelho_a_cada_10_minutos(client, menu):
    payload = order_payload([menu.item("Água")])
    for _ in range(6):
        place_order(client, payload, ip="10.1.1.1")
    resp = post_order(client, payload, ip="10.1.1.1")
    assert resp.status_code == 429
    assert "Muitos pedidos seguidos" in resp.get_json()["error"]
    # Outro aparelho continua conseguindo pedir
    place_order(client, payload, ip="10.2.2.2")


# ---------- CSRF ----------

def test_pedido_sem_token_csrf_e_recusado(client, menu):
    csrf(client)
    resp = client.post("/api/pedidos", json=order_payload([menu.item("X-Burguer")]))
    assert resp.status_code == 400
    assert "Sua sessão expirou" in resp.get_json()["error"]


def test_pedido_com_token_csrf_errado_e_recusado(client, menu):
    csrf(client)
    resp = client.post("/api/pedidos", json=order_payload([menu.item("X-Burguer")]),
                       headers={"X-CSRF-Token": "token-de-outra-pessoa"})
    assert resp.status_code == 400


def test_navegador_novo_sem_sessao_e_recusado(client, menu):
    resp = client.post("/api/pedidos", json=order_payload([menu.item("X-Burguer")]), headers={"X-CSRF-Token": ""})
    assert resp.status_code == 400


def test_token_csrf_da_pagina_funciona(client, menu):
    """Fluxo real: abre o site, pega o token da página e faz o pedido com ele."""
    html = client.get("/").get_data(as_text=True)
    token = re.search(r'<meta name="csrf-token" content="([^"]+)"', html).group(1)
    resp = client.post("/api/pedidos", json=order_payload([menu.item("X-Burguer")]), headers={"X-CSRF-Token": token})
    assert resp.status_code == 200


# ---------- Depois do pedido ----------

def test_pagina_do_pedido(client, menu):
    data = place_order(client, order_payload([menu.item("X-Burguer")]))
    page = client.get(f"/pedido/{data['token']}")
    assert page.status_code == 200
    assert "X-Burguer" in page.get_data(as_text=True)
    state = client.get(f"/api/pedido/{data['token']}").get_json()
    assert (state["status"], state["payment_status"], state["total"]) == ("novo", "na_entrega", "R$ 18,00")
    assert state["whatsapp_url"].startswith("https://wa.me/5548991527882?text=")


def test_pedido_inexistente_da_404(client):
    assert client.get("/pedido/" + "a" * 24).status_code == 404
    assert client.get("/pedido/curto").status_code == 404


def test_cliente_cancela_pedido_ainda_nao_confirmado(client, app, menu):
    data = place_order(client, order_payload([menu.item("X-Burguer")]))
    client.post(f"/pedido/{data['token']}/cancelar", data={"csrf_token": csrf(client)})
    order = order_row(app, data["id"])
    assert (order["status"], order["cancelled_by"]) == ("cancelado", "cliente")
