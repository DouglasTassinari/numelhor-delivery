"""Painel do dono: login, bloqueio por tentativas, 2FA, rotas protegidas e status dos pedidos."""
from types import SimpleNamespace

import pytest

from numelhor import security
from numelhor.db import get_db

from helpers import (ADMIN_PASSWORD, admin_post, audit_events, csrf, login, order_payload, order_row, place_order,
                     post_order)

PROTECTED_PAGES = ["/admin/", "/admin/pedidos", "/admin/pedidos/1", "/admin/pedidos/1/imprimir", "/admin/cardapio",
                   "/admin/produtos/novo", "/admin/produtos/1", "/admin/categorias-e-molhos", "/admin/caixa",
                   "/admin/caixa/1", "/admin/relatorios", "/admin/configuracoes", "/admin/pagamentos",
                   "/admin/seguranca", "/admin/senha"]


def logged_in(client):
    return client.get("/admin/pedidos").status_code == 200


# ---------- Login ----------

def test_usuario_do_painel_criado_pela_hospedagem(app):
    with app.app_context():
        user = get_db().execute("SELECT * FROM admin_users").fetchone()
    assert (user["username"], user["must_change_password"]) == ("dono", 0)
    assert "admin_criado_pela_hospedagem" in audit_events(app)


def test_tela_de_login_abre(client):
    resp = client.get("/admin/login")
    assert resp.status_code == 200
    assert 'name="csrf_token"' in resp.get_data(as_text=True)


def test_login_certo_entra_no_painel(client, app):
    resp = login(client)
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/pedidos"
    assert logged_in(client)
    assert "login_ok" in audit_events(app)


def test_usuario_com_letras_maiusculas_e_espacos(client):
    login(client, username="  DONO ")
    assert logged_in(client)


def test_senha_errada_nao_entra(client, app):
    resp = login(client, password="SenhaErrada123")
    assert resp.status_code == 200
    assert "Usuário ou senha incorretos." in resp.get_data(as_text=True)
    assert not logged_in(client)
    assert "login_falhou" in audit_events(app)


def test_usuario_inexistente_tem_a_mesma_mensagem(client):
    resp = login(client, username="hacker", password=ADMIN_PASSWORD)
    assert "Usuário ou senha incorretos." in resp.get_data(as_text=True)
    assert not logged_in(client)


def test_login_sem_csrf_e_recusado(client):
    client.get("/admin/login")
    resp = client.post("/admin/login", data={"username": "dono", "password": ADMIN_PASSWORD})
    assert resp.status_code == 400
    assert not logged_in(client)


def test_logout(client):
    login(client)
    admin_post(client, "/admin/logout")
    assert not logged_in(client)


@pytest.mark.parametrize("next_url, expected", [
    ("/admin/caixa", "/admin/caixa"),
    ("https://site-falso.example/admin", "/admin/pedidos"),
    ("//site-falso.example", "/admin/pedidos"),
])
def test_depois_do_login_so_volta_para_paginas_do_proprio_site(client, next_url, expected):
    assert login(client, next_url=next_url).headers["Location"] == expected


# ---------- Bloqueio por tentativas ----------

def test_bloqueia_o_aparelho_depois_de_5_senhas_erradas(client):
    for _ in range(5):
        login(client, password="SenhaErrada123", ip="10.0.0.1")
    resp = login(client, ip="10.0.0.1")  # agora até a senha certa é recusada
    assert "Muitas tentativas erradas" in resp.get_data(as_text=True)
    assert not logged_in(client)
    # Outro aparelho (outro IP) continua conseguindo entrar
    login(client, ip="10.0.0.2")
    assert logged_in(client)


def test_4_senhas_erradas_ainda_nao_bloqueiam(client):
    for _ in range(4):
        login(client, password="SenhaErrada123", ip="10.0.0.1")
    login(client, ip="10.0.0.1")
    assert logged_in(client)


def test_bloqueia_o_usuario_depois_de_10_senhas_erradas_de_varios_aparelhos(client, app):
    for i in range(10):
        login(client, password="SenhaErrada123", ip=f"10.0.1.{i}")
    resp = login(client, ip="10.0.2.1")
    assert "Usuário bloqueado por excesso de tentativas" in resp.get_data(as_text=True)
    assert not logged_in(client)


def test_login_certo_zera_as_tentativas(client, app):
    for _ in range(3):
        login(client, password="SenhaErrada123", ip="10.0.0.1")
    login(client, ip="10.0.0.1")
    with app.app_context():
        assert get_db().execute("SELECT failed_count FROM admin_users").fetchone()[0] == 0
        assert get_db().execute("SELECT COUNT(*) FROM login_throttle").fetchone()[0] == 0


# ---------- Verificação em duas etapas (2FA) ----------

# Segredo e códigos de referência da RFC 4226 (padrão usado pelo Google Authenticator)
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890" em base32
RFC_CODES = ["755224", "287082", "359152", "969429", "338314", "254676", "287922", "162583", "399871", "520489"]


def test_codigos_2fa_seguem_o_padrao():
    assert [security._hotp(RFC_SECRET, i) for i in range(10)] == RFC_CODES


def test_login_com_2fa(client, app, monkeypatch):
    with app.app_context():
        get_db().execute("UPDATE admin_users SET totp_secret = ?, totp_enabled = 1", (RFC_SECRET,))
        get_db().commit()
    monkeypatch.setattr(security, "time", SimpleNamespace(time=lambda: 5 * 30 + 10))  # relógio no código nº 5

    resp = login(client)
    assert resp.headers["Location"] == "/admin/login/2fa"
    with client.session_transaction() as sess:  # só a senha não basta: ainda não entrou
        assert "admin_id" not in sess and sess["pending_2fa"]

    resp = client.post("/admin/login/2fa", data={"csrf_token": csrf(client), "code": "000000"})
    assert "Código inválido" in resp.get_data(as_text=True)

    resp = client.post("/admin/login/2fa", data={"csrf_token": csrf(client), "code": RFC_CODES[5]})
    assert resp.headers["Location"] == "/admin/pedidos"
    assert logged_in(client)

    # O mesmo código não pode ser usado de novo
    admin_post(client, "/admin/logout")
    login(client)
    resp = client.post("/admin/login/2fa", data={"csrf_token": csrf(client), "code": RFC_CODES[5]})
    assert "Código inválido" in resp.get_data(as_text=True)
    assert not logged_in(client)


# ---------- Rotas protegidas ----------

@pytest.mark.parametrize("path", PROTECTED_PAGES)
def test_paginas_do_painel_exigem_login(client, path):
    resp = client.get(path)
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("/admin/login")


def test_api_do_painel_exige_login(client):
    resp = client.get("/admin/api/pedidos")
    assert resp.status_code == 401
    assert resp.get_json()["ok"] is False


@pytest.mark.usefixtures("store_open")
def test_acoes_do_painel_exigem_login(client, app, menu):
    data = place_order(client, order_payload([menu.item("X-Burguer")]))
    pid = menu.product("X-Burguer")
    for path, form in ((f"/admin/pedidos/{data['id']}/status", {"status": "confirmado"}),
                       ("/admin/loja/modo", {"mode": "fechado"}),
                       (f"/admin/produtos/{pid}/ativo", {}),
                       (f"/admin/produtos/{pid}/excluir", {}),
                       ("/admin/caixa", {"action": "abrir", "amount": "100"})):
        resp = admin_post(client, path, **form)
        assert resp.status_code == 302 and resp.headers["Location"].startswith("/admin/login"), path
    assert order_row(app, data["id"])["status"] == "novo"
    with app.app_context():
        assert get_db().execute("SELECT active FROM products WHERE id = ?", (pid,)).fetchone()[0] == 1
        assert get_db().execute("SELECT COUNT(*) FROM cash_sessions").fetchone()[0] == 0
        assert get_db().execute("SELECT value FROM settings WHERE key = 'open_mode'").fetchone()[0] == "aberto"


@pytest.mark.parametrize("path", PROTECTED_PAGES[:2] + ["/admin/cardapio", "/admin/categorias-e-molhos",
                                                        "/admin/caixa", "/admin/relatorios", "/admin/configuracoes",
                                                        "/admin/pagamentos", "/admin/seguranca", "/admin/senha",
                                                        "/admin/produtos/novo", "/admin/produtos/1"])
def test_paginas_do_painel_abrem_depois_do_login(client, path):
    login(client)
    resp = client.get(path, follow_redirects=True)
    assert resp.status_code == 200
    assert 'content="noindex, nofollow"' in resp.get_data(as_text=True)


# ---------- Status do pedido ----------

def change_status(client, order_id, status, **extra):
    return admin_post(client, f"/admin/pedidos/{order_id}/status", status=status, **extra)


@pytest.fixture
def order(client, menu, store_open):
    data = place_order(client, order_payload([menu.item("X-Burguer")]))
    login(client)
    return data


def test_lista_de_pedidos_do_painel(client, order):
    data = client.get("/admin/api/pedidos").get_json()
    [row] = data["orders"]
    assert (row["id"], row["status"], row["name"], row["total"], row["paid"]) == (
        order["id"], "novo", "Maria da Silva", "R$ 18,00", False)
    assert row["payment"] == "Dinheiro · Pagar na entrega/retirada"


def test_fluxo_completo_de_retirada(client, app, order):
    resp = change_status(client, order["id"], "confirmado")
    assert resp.headers["Location"] == f"/admin/pedidos/{order['id']}?imprimir=1"
    for status in ("em_preparo", "pronto_retirada", "concluido"):
        change_status(client, order["id"], status)
        assert order_row(app, order["id"])["status"] == status
    # Concluído na retirada = dinheiro recebido
    assert order_row(app, order["id"])["payment_status"] == "recebido"


@pytest.mark.usefixtures("store_open")
def test_fluxo_completo_de_entrega(client, app, menu, road_km):
    road_km(1)
    data = place_order(client, order_payload([menu.item("X-Burguer")], fulfillment="entrega"))
    login(client)
    for status in ("confirmado", "em_preparo", "saiu_entrega", "concluido"):
        change_status(client, data["id"], status)
        assert order_row(app, data["id"])["status"] == status


@pytest.mark.parametrize("path", [["concluido"], ["confirmado", "novo"], ["confirmado", "em_preparo", "saiu_entrega"]])
def test_mudancas_de_status_nao_permitidas(client, app, order, path):
    """Pular etapas, voltar etapas ou mandar 'saiu para entrega' num pedido de retirada não pode."""
    for status in path[:-1]:
        change_status(client, order["id"], status)
    before = order_row(app, order["id"])["status"]
    change_status(client, order["id"], path[-1])
    assert order_row(app, order["id"])["status"] == before


def test_cancelar_exige_motivo(client, app, order):
    change_status(client, order["id"], "cancelado")
    assert order_row(app, order["id"])["status"] == "novo"
    change_status(client, order["id"], "cancelado", cancel_reason="Motivo inventado")
    assert order_row(app, order["id"])["status"] == "novo"

    change_status(client, order["id"], "cancelado", cancel_reason="Faltou produto", cancel_note="acabou o pão")
    row = order_row(app, order["id"])
    assert (row["status"], row["cancel_reason"], row["cancelled_by"]) == (
        "cancelado", "Faltou produto: acabou o pão", "loja")
    # Pedido cancelado não volta
    change_status(client, order["id"], "confirmado")
    assert order_row(app, order["id"])["status"] == "cancelado"


def test_tela_do_pedido_e_impressao(client, order):
    page = client.get(f"/admin/pedidos/{order['id']}").get_data(as_text=True)
    assert "X-Burguer" in page and "R$ 18,00" in page
    assert client.get(f"/admin/pedidos/{order['id']}/imprimir").status_code == 200
    assert client.get("/admin/pedidos/9999").status_code == 404


def test_fechar_e_abrir_a_loja_pelo_painel(client, menu, order):
    admin_post(client, "/admin/loja/modo", mode="fechado")
    resp = post_order(client, order_payload([menu.item("X-Burguer")]))
    assert resp.status_code == 400
    assert "A loja está fechada agora" in resp.get_json()["error"]
    admin_post(client, "/admin/loja/modo", mode="aberto")
    assert post_order(client, order_payload([menu.item("X-Burguer")])).status_code == 200


def test_senha_fraca_na_hospedagem_impede_o_app_de_subir(make_app):
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD fraca"):
        make_app(ADMIN_PASSWORD="dono12345")
