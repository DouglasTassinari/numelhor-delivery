"""Preparação comum dos testes.

- Cada teste ganha um app novo, com banco e pasta de fotos novos numa pasta temporária.
- A internet fica bloqueada: nenhum teste fala com OpenStreetMap ou Mercado Pago de verdade.
- Os contadores que ficam na memória (limite de pedidos, cache de endereços) são zerados entre os testes.
"""
import secrets
import socket
from datetime import datetime

import pytest

import numelhor
from numelhor import db, geo, hours, security

from helpers import ADMIN_PASSWORD, ADMIN_USER, BASE_URL, Menu

# Variáveis que, se estiverem no computador de quem roda os testes, mudariam o comportamento do app
ENV_VARS = ("SECRET_KEY", "APP_ENV", "DATABASE_PATH", "UPLOAD_DIR", "PUBLIC_BASE_URL", "RENDER_EXTERNAL_URL",
            "TRUST_PROXY", "CONTACT_EMAIL", "ADMIN_USERNAME", "ADMIN_PASSWORD", "MP_ACCESS_TOKEN", "MP_SANDBOX",
            "MP_WEBHOOK_SECRET", "PIX_PAYER_FALLBACK_EMAIL")


@pytest.fixture(autouse=True)
def no_internet(monkeypatch):
    """Qualquer tentativa de abrir conexão de rede faz o teste falhar na hora."""
    def blocked(*args, **kwargs):
        raise RuntimeError("Um teste tentou acessar a internet. Simule (mock) a chamada.")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture(autouse=True)
def reset_memory():
    security._hits.clear()
    geo._cache.clear()
    yield
    security._hits.clear()
    geo._cache.clear()


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    """Fábrica de apps: make_app() usa a configuração de teste; make_app(APP_ENV="production", ...) muda algo."""
    monkeypatch.setattr(numelhor, "load_dotenv", lambda *a, **k: None)  # ignora um .env local
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    base = {
        "SECRET_KEY": secrets.token_urlsafe(48),
        "APP_ENV": "dev",
        "DATABASE_PATH": str(tmp_path / "numelhor.db"),
        "UPLOAD_DIR": str(tmp_path / "uploads"),
        "PUBLIC_BASE_URL": BASE_URL,
        "ADMIN_USERNAME": ADMIN_USER,
        "ADMIN_PASSWORD": ADMIN_PASSWORD,
    }

    def factory(**overrides):
        for key, value in {**base, **overrides}.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)
        app = numelhor.create_app()
        app.config["TESTING"] = True
        return app
    return factory


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def ctx(app):
    """Abre o contexto do app (para chamar funções como price_cart direto)."""
    with app.test_request_context():
        yield


@pytest.fixture
def menu(app):
    return Menu(app)


@pytest.fixture
def store_open(app):
    """Loja aberta pelo painel ("Abrir agora", sem prazo): os pedidos não dependem da hora em que o teste roda."""
    with app.app_context():
        db.set_settings({"open_mode": "aberto", "open_until": ""})


@pytest.fixture
def road_km(monkeypatch):
    """road_km(6.5) faz o app achar que o endereço fica a 6,5 km pelas ruas (sem calcular no mapa)."""
    def set_distance(km):
        # O app multiplica a distância em linha reta por 1,3; devolvemos o valor que vira `km` depois disso.
        monkeypatch.setattr(geo, "haversine_km", lambda *a: km / 1.3)
    return set_distance


@pytest.fixture
def freeze_time(monkeypatch):
    """freeze_time(datetime(..., tzinfo=BRT)) congela o relógio usado pelo horário da loja."""
    def freeze(when):
        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return when if tz is None else when.astimezone(tz)
        monkeypatch.setattr(hours, "datetime", Frozen)
    return freeze
