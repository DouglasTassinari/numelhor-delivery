"""Caixa (gaveta) e relatórios de vendas. Os totais esperados foram somados à mão."""
import json
from datetime import datetime, timedelta

import pytest

from numelhor import cash
from numelhor.admin import parse_money
from numelhor.db import get_db
from numelhor.hours import BRT

from helpers import admin_post, csrf, login, order_payload, order_row, place_order


def summary_around_now(app):
    now = datetime.now(BRT).replace(tzinfo=None)
    with app.app_context():
        return cash.summarize(now - timedelta(days=1), now + timedelta(days=1))


@pytest.fixture
def day_of_sales(client, app, menu, road_km, store_open):
    """Abre o caixa com R$ 100,00 e faz 6 pedidos (cada um de um cliente/aparelho diferente):

    A  retirada, dinheiro na retirada     2 X-Burguer                       R$ 36,00
    B  entrega 6,5 km, Pix na entrega     3 Pastel de Carne + taxa R$ 12,50 R$ 33,50
    C  retirada, Pix pelo site (pago)     1 Refri Lata                      R$  6,00
    D  retirada, dinheiro -> CANCELADO    1 Misto Quente                    R$ 12,00
    E  retirada, cartão pelo site (não pago) 1 Suco                         R$  5,00
    F  entrega 1,5 km, crédito na entrega 1 X Salada + Bacon + taxa R$ 5,00 R$ 30,00
    """
    login(client)
    admin_post(client, "/admin/caixa", action="abrir", amount="100,00")
    orders = {}
    orders["A"] = place_order(client, order_payload([menu.item("X-Burguer", qty=2)]), ip="10.0.0.1")
    road_km(6.5)
    orders["B"] = place_order(client, order_payload([menu.item("Pastel de Carne", qty=3)], fulfillment="entrega",
                                                    method="pix"), ip="10.0.0.2")
    orders["C"] = place_order(client, order_payload([menu.item("Refri Lata", options=["Coca-Cola"])], when="online",
                                                    method="pix"), ip="10.0.0.3")
    client.post(f"/simulador/{orders['C']['token']}/aprovar", headers={"X-CSRF-Token": csrf(client)})
    orders["D"] = place_order(client, order_payload([menu.item("Misto Quente")]), ip="10.0.0.4")
    admin_post(client, f"/admin/pedidos/{orders['D']['id']}/status", status="cancelado", cancel_reason="Faltou produto")
    orders["E"] = place_order(client, order_payload([menu.item("Suco", options=["Uva"])], when="online",
                                                    method="cartao"), ip="10.0.0.5")
    road_km(1.5)
    orders["F"] = place_order(client, order_payload([menu.item("X Salada", options=["Bacon"])], fulfillment="entrega",
                                                    method="credito"), ip="10.0.0.6")
    return orders


def test_totais_dos_pedidos_do_dia(app, day_of_sales):
    totals = {k: order_row(app, v["id"])["total_cents"] for k, v in day_of_sales.items()}
    assert totals == {"A": 3600, "B": 3350, "C": 600, "D": 1200, "E": 500, "F": 3000}


def test_resumo_de_vendas(app, day_of_sales):
    s = summary_around_now(app)
    assert s["count"] == 4                      # A, B, C e F (D cancelado, E sem pagamento)
    assert s["gross"] == 3600 + 3350 + 600 + 3000 == 10550
    assert s["fees"] == 1250 + 500
    assert s["products"] == 8800
    assert s["avg"] == 2637                     # 10550 / 4, sem centavos quebrados
    assert (s["delivery_count"], s["delivery_total"]) == (2, 6350)
    assert (s["pickup_count"], s["pickup_total"]) == (2, 4200)
    assert s["waiting_count"] == 1
    assert (s["cancelled_count"], s["cancelled_total"]) == (1, 1200)
    assert s["cancel_reasons"] == {"Faltou produto": {"count": 1, "total": 1200}}


def test_vendas_por_forma_de_pagamento(app, day_of_sales):
    s = summary_around_now(app)
    assert {k: (v["count"], v["total"]) for k, v in s["by_method"].items()} == {
        "pix_site": (1, 600), "cartao_site": (0, 0), "dinheiro": (1, 3600),
        "pix_local": (1, 3350), "debito": (0, 0), "credito": (1, 3000)}
    assert s["cash_sales"] == 3600


def test_produtos_mais_vendidos(app, day_of_sales):
    assert summary_around_now(app)["top"] == [
        {"name": "Pastel de Carne", "qty": 3, "total": 2100},
        {"name": "X-Burguer", "qty": 2, "total": 3600},
        {"name": "X Salada", "qty": 1, "total": 2500},
        {"name": "Refri Lata", "qty": 1, "total": 600},
    ]


def test_caixa_do_dia_com_retirada_e_reforco(client, app, day_of_sales):
    admin_post(client, "/admin/caixa", action="sangria", amount="20,00", reason="troco do motoboy")
    admin_post(client, "/admin/caixa", action="suprimento", amount="5", reason="moedas")
    # Gaveta: 100,00 de troco + 36,00 em dinheiro (pedido A) + 5,00 - 20,00 = 121,00
    with app.app_context():
        report = cash.session_report(cash.current_session())
    assert (report["sangrias"], report["suprimentos"], report["expected"]) == (2000, 500, 12100)
    page = client.get("/admin/caixa").get_data(as_text=True)
    assert "O sistema espera R$ 121,00." in page

    resp = admin_post(client, "/admin/caixa", action="fechar", amount="120,00", notes="faltou 1 real")
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Faltou R$ 1,00 na gaveta" in page
    with app.app_context():
        row = get_db().execute("SELECT * FROM cash_sessions").fetchone()
    assert (row["opening_cents"], row["expected_cents"], row["counted_cents"]) == (10000, 12100, 12000)
    assert row["closed_at"] is not None
    assert json.loads(row["summary"])["gross"] == 10550


def test_relatorio_do_periodo_no_painel(client, day_of_sales):
    today = datetime.now(BRT).date()
    de, ate = today - timedelta(days=1), today + timedelta(days=1)
    resp = client.get(f"/admin/relatorios?periodo=custom&de={de}&ate={ate}")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert "R$ 105,50" in page
    assert "Faltou produto" in page
    for key in ("hoje", "ontem", "semana", "semana_passada", "mes", "mes_passado"):
        assert client.get(f"/admin/relatorios?periodo={key}").status_code == 200


def test_caixa_sem_vendas_confere(client, app, store_open):
    login(client)
    admin_post(client, "/admin/caixa", action="abrir", amount="50")
    admin_post(client, "/admin/caixa", action="abrir", amount="80")  # já aberto: não abre outro
    resp = admin_post(client, "/admin/caixa", action="fechar", amount="50,00")
    assert "Gaveta confere certinho: R$ 50,00." in client.get(resp.headers["Location"]).get_data(as_text=True)
    with app.app_context():
        rows = get_db().execute("SELECT opening_cents, expected_cents FROM cash_sessions").fetchall()
    assert [tuple(r) for r in rows] == [(5000, 5000)]


def test_caixa_com_valor_invalido(client, app):
    login(client)
    admin_post(client, "/admin/caixa", action="abrir", amount="cem reais")
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM cash_sessions").fetchone()[0] == 0


# ---------- Fuso horário (o banco guarda em UTC, a loja está em UTC-3) ----------

def insert_order(created_at_utc, total, status="concluido"):
    get_db().execute(
        "INSERT INTO orders (token, created_at, customer_name, customer_phone, fulfillment, pay_when, pay_method, "
        "subtotal_cents, delivery_fee_cents, total_cents, status, payment_status) "
        "VALUES (?, ?, 'Teste', '48999991234', 'retirada', 'entrega', 'dinheiro', ?, 0, ?, ?, 'na_entrega')",
        (f"tok-{created_at_utc}", created_at_utc, total, total, status))


def test_vendas_entram_no_dia_certo_pelo_horario_de_brasilia(ctx):
    insert_order("2026-10-16 02:30:00", 2000)            # 15/10 às 23:30 em Brasília
    insert_order("2026-10-16 03:00:00", 4000, "novo")    # 16/10 à meia-noite
    insert_order("2026-10-17 02:30:00", 1000)            # 16/10 às 23:30
    insert_order("2026-10-17 03:00:00", 8000)            # 17/10 à meia-noite
    day = cash.summarize(datetime(2026, 10, 16), datetime(2026, 10, 17))
    assert (day["count"], day["gross"]) == (2, 5000)
    assert day["hours"] == [{"hour": 0, "count": 1, "total": 4000}, {"hour": 23, "count": 1, "total": 1000}]

    days = cash.summarize(datetime(2026, 10, 15), datetime(2026, 10, 18), by_day=True)["days"]
    assert days == [{"label": "qui 15/10", "count": 1, "total": 2000},
                    {"label": "sex 16/10", "count": 2, "total": 5000},
                    {"label": "sáb 17/10", "count": 1, "total": 8000}]


# ---------- Períodos dos relatórios ----------

NOW = datetime(2026, 10, 16, 15, 0)  # sexta-feira


@pytest.mark.parametrize("key, start, end, title", [
    ("hoje", datetime(2026, 10, 16), datetime(2026, 10, 17), "Hoje"),
    ("ontem", datetime(2026, 10, 15), datetime(2026, 10, 16), "Ontem"),
    ("semana", datetime(2026, 10, 12), datetime(2026, 10, 19), "Esta semana"),
    ("semana_passada", datetime(2026, 10, 5), datetime(2026, 10, 12), "Semana passada"),
    ("mes", datetime(2026, 10, 1), datetime(2026, 11, 1), "Este mês"),
    ("mes_passado", datetime(2026, 9, 1), datetime(2026, 10, 1), "Mês passado"),
    ("qualquer-coisa", datetime(2026, 10, 16), datetime(2026, 10, 17), "Hoje"),
])
def test_periodos(key, start, end, title):
    assert cash.period_range(key, now=NOW) == (start, end, title)


def test_periodo_escolhido_inclui_o_ultimo_dia():
    assert cash.period_range("custom", "2026-10-01", "2026-10-10", now=NOW) == (
        datetime(2026, 10, 1), datetime(2026, 10, 11), "01/10/2026 a 10/10/2026")


@pytest.mark.parametrize("de, ate", [("2026-10-10", "2026-10-01"), ("2024-01-01", "2026-10-01"), ("ontem", None)])
def test_periodo_escolhido_invalido_volta_para_hoje(de, ate):
    assert cash.period_range("custom", de, ate, now=NOW)[2] == "Hoje"


def test_virada_de_ano():
    assert cash.period_range("mes", now=datetime(2026, 12, 20))[:2] == (datetime(2026, 12, 1), datetime(2027, 1, 1))
    assert cash.period_range("mes_passado", now=datetime(2027, 1, 10))[:2] == (
        datetime(2026, 12, 1), datetime(2027, 1, 1))


@pytest.mark.parametrize("text, cents", [
    ("150,00", 15000), ("1.234,56", 123456), ("R$ 32,90", 3290), ("150", 15000), ("0", 0),
    ("10.000,00", 1_000_000), ("10.000,01", None), ("abc", None), ("-5", None), ("", None),
])
def test_leitura_de_valores_em_reais(text, cents):
    assert parse_money(text) == cents
