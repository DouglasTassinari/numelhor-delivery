"""Taxa de entrega por distância e busca de endereço (com o OpenStreetMap simulado).

Regra padrão (numelhor/db.py): R$ 5,00 até 2 km, mais R$ 1,50 por km a mais (km quebrado conta como km
inteiro) e entrega até 10 km.
"""
import pytest
import requests

from numelhor import geo
from numelhor.db import set_settings, settings

from helpers import LAT, LNG, csrf

def quote(lat=LAT, lng=LNG):
    return geo.delivery_quote(settings(), lat, lng)


@pytest.mark.parametrize("km, fee", [
    (0.5, 500),     # perto: só a taxa base
    (2.0, 500),     # exatamente no limite incluído
    (2.01, 650),    # passou um pouquinho: cobra 1 km a mais
    (3.0, 650),     # 1 km a mais
    (6.5, 1250),    # 4,5 km a mais -> 5 km x R$ 1,50
    (10.0, 1700),   # no raio máximo: 8 km a mais
])
def test_taxa_de_entrega_pela_distancia(ctx, road_km, km, fee):
    road_km(km)
    result = quote()
    assert result["ok"] is True
    assert result["distance_km"] == km
    assert result["fee_cents"] == fee


def test_endereco_alem_do_raio_maximo_e_recusado(ctx, road_km):
    road_km(10.01)
    result = quote()
    assert result["ok"] is False
    assert "fee_cents" not in result
    assert "entrega até 10 km" in result["error"]


def test_taxa_segue_as_configuracoes_do_painel(ctx, road_km):
    set_settings({"delivery_base_cents": "700", "delivery_included_km": "3", "delivery_per_km_cents": "200",
                  "delivery_max_km": "5"})
    road_km(4.2)  # 1,2 km a mais -> conta 2 km
    assert quote()["fee_cents"] == 700 + 2 * 200
    road_km(5.5)
    assert quote()["ok"] is False


@pytest.mark.parametrize("included, km, extra_km", [("2.4", 5.4, 3), ("0.1", 1.1, 1), ("1.7", 3.7, 2)])
def test_km_incluido_quebrado_nao_cobra_km_a_mais(ctx, road_km, included, km, extra_km):
    # 5,4 - 2,4 dá 3,0000000000000004 na conta do computador; mesmo assim são 3 km a mais, não 4
    set_settings({"delivery_included_km": included})
    road_km(km)
    assert quote()["fee_cents"] == 500 + extra_km * 150


def test_endereco_na_porta_da_loja_paga_so_a_taxa_base(ctx):
    cfg = settings()
    result = geo.delivery_quote(cfg, cfg["store_lat"], cfg["store_lng"])
    assert result == {"ok": True, "distance_km": 0.0, "fee_cents": 500,
                      "lat": float(cfg["store_lat"]), "lng": float(cfg["store_lng"])}


def test_distancia_em_linha_reta():
    # 1 grau de latitude = raio da Terra (6371 km) x pi / 180 = 111,19 km
    assert geo.haversine_km(0, 0, 1, 0) == pytest.approx(111.19, abs=0.01)


@pytest.mark.parametrize("lat, lng", [(None, None), ("abc", "-49.4"), (40.7, -74.0), (-28.6, 10.0)])
def test_coordenadas_invalidas_ou_fora_do_brasil(ctx, lat, lng):
    assert quote(lat, lng) == {"ok": False, "error": "Marque o local de entrega no mapa."}


# ---------- Busca de endereço (OpenStreetMap simulado) ----------

class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


PHOTON = {"features": [
    {"properties": {"countrycode": "BR", "street": "Avenida Luiz Lazzarin", "housenumber": "155",
                    "district": "Rio Maina", "city": "Criciúma", "state": "Santa Catarina"},
     "geometry": {"coordinates": [-49.411, -28.676]}},
    {"properties": {"countrycode": "AR", "name": "Avenida de Buenos Aires"},
     "geometry": {"coordinates": [-58.38, -34.60]}},
]}


@pytest.fixture
def fake_osm(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(url)
        if url == geo.PHOTON_URL:
            return FakeResponse(PHOTON)
        return FakeResponse({"address": {"road": "Rua Henrique Lage", "suburb": "Centro", "city": "Criciúma",
                                         "house_number": "100"}})
    monkeypatch.setattr(geo.requests, "get", fake_get)
    return calls


def test_busca_de_endereco_so_mostra_resultados_do_brasil(client, fake_osm):
    resp = client.get("/api/geo/search?q=Luiz Lazzarin")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "results": [{
        "label": "Avenida Luiz Lazzarin, 155 - Rio Maina - Criciúma - Santa Catarina",
        "address": "Avenida Luiz Lazzarin - Rio Maina - Criciúma - Santa Catarina",
        "lat": -28.676, "lng": -49.411, "number": "155"}]}
    assert fake_osm == [geo.PHOTON_URL]


def test_busca_curta_demais_nem_consulta(client, fake_osm):
    assert client.get("/api/geo/search?q=Rua").get_json() == {"ok": True, "results": []}
    assert fake_osm == []


def test_busca_repetida_usa_o_cache(client, fake_osm):
    client.get("/api/geo/search?q=Luiz Lazzarin")
    client.get("/api/geo/search?q=luiz lazzarin")
    assert len(fake_osm) == 1


def test_busca_fora_do_ar_avisa_o_cliente(client, monkeypatch):
    def broken(*a, **k):
        raise requests.ConnectionError("sem internet")
    monkeypatch.setattr(geo.requests, "get", broken)
    resp = client.get("/api/geo/search?q=Luiz Lazzarin")
    assert resp.status_code == 502
    assert "Marque direto no mapa" in resp.get_json()["error"]


def test_endereco_pelo_ponto_no_mapa(client, fake_osm):
    resp = client.get(f"/api/geo/reverse?lat={LAT}&lng={LNG}")
    assert resp.get_json() == {"ok": True, "address": "Rua Henrique Lage - Centro - Criciúma", "number": "100"}


def test_endereco_pelo_ponto_com_coordenada_invalida(client, fake_osm):
    assert client.get("/api/geo/reverse?lat=99&lng=0").status_code == 400
    assert fake_osm == []


def test_api_de_orcamento_calcula_a_taxa_no_servidor(client, menu, road_km):
    road_km(6.5)
    resp = client.post("/api/quote", headers={"X-CSRF-Token": csrf(client)}, json={
        "items": [menu.item("X-Burguer", qty=2)], "fulfillment": "entrega",
        "delivery": {"lat": LAT, "lng": LNG, "fee_cents": 0}})
    data = resp.get_json()
    assert data["subtotal_cents"] == 3600
    assert data["fee_cents"] == 1250
    assert data["total_cents"] == 4850


def test_retirada_nao_tem_taxa(client, menu):
    resp = client.post("/api/quote", headers={"X-CSRF-Token": csrf(client)},
                       json={"items": [menu.item("X-Burguer")], "fulfillment": "retirada"})
    assert resp.get_json()["fee_cents"] == 0
    assert resp.get_json()["total_cents"] == 1800


def test_cardapio_padrao_tem_a_regra_de_entrega_esperada(ctx):
    cfg = settings()
    assert (cfg["delivery_base_cents"], cfg["delivery_included_km"], cfg["delivery_per_km_cents"],
            cfg["delivery_max_km"]) == ("500", "2", "150", "10")
