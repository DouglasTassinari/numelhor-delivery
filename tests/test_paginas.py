"""Páginas públicas, dados para o Google (JSON-LD), robots.txt, sitemap, /saude, erros e cabeçalhos de segurança."""
import json
import re
import xml.etree.ElementTree as ET

import pytest

from numelhor.db import set_settings

from helpers import BASE_URL


def ld_json(html):
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert match, "A página não tem o bloco JSON-LD"
    return json.loads(match.group(1))


def graph(client):
    data = ld_json(client.get("/").get_data(as_text=True))
    assert data["@context"] == "https://schema.org"
    return {node["@type"]: node for node in data["@graph"]}


def walk(value):
    """Todos os valores do JSON, para conferir que não sobrou campo vazio."""
    if isinstance(value, dict):
        for v in value.values():
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)
    else:
        yield value


# ---------- Página inicial ----------

def test_pagina_inicial(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "NuMelhor" in html
    assert 'id="sobre"' in html and 'id="perguntas"' in html
    assert f'<link rel="canonical" href="{BASE_URL}/">' in html
    assert '<meta name="robots" content="index, follow' in html
    assert "X-Burguer" in html


def test_json_ld_tem_restaurante_site_e_perguntas(client):
    nodes = graph(client)
    assert set(nodes) == {"Restaurant", "WebSite", "FAQPage"}


def test_json_ld_do_restaurante(client):
    r = graph(client)["Restaurant"]
    assert r["@id"] == f"{BASE_URL}/#restaurante"
    assert r["name"] == "NuMelhor Lanches"
    assert r["telephone"] == "+5548991527882"
    assert r["address"] == {"@type": "PostalAddress", "streetAddress": "Av. Luiz Lazzarin, 155 - 2º andar - Rio Maina",
                            "addressLocality": "Criciúma", "addressRegion": "SC", "postalCode": "88817-000",
                            "addressCountry": "BR"}
    assert r["geo"] == {"@type": "GeoCoordinates", "latitude": -28.676376, "longitude": -49.411353}
    assert r["hasMap"] == "https://www.google.com/maps/search/?api=1&query=-28.676376,-49.411353"
    assert r["areaServed"]["geoRadius"] == 10000  # entrega até 10 km
    assert r["description"].startswith("Lanchonete no Rio Maina, em Criciúma:")
    assert r["sameAs"] == ["https://www.instagram.com/numelhor/"]
    assert all(img.startswith(BASE_URL + "/") for img in r["image"])


def test_json_ld_do_horario(client):
    opening = graph(client)["Restaurant"]["openingHoursSpecification"]
    days = {o["dayOfWeek"].rsplit("/", 1)[1]: (o["opens"], o["closes"]) for o in opening}
    assert days == {"Tuesday": ("18:00", "23:30"), "Wednesday": ("18:00", "23:30"), "Thursday": ("18:00", "23:30"),
                    "Friday": ("18:00", "23:59"), "Saturday": ("18:00", "23:59"), "Sunday": ("18:00", "23:30")}


def test_json_ld_do_cardapio_usa_o_preco_a_partir_de(client):
    sections = graph(client)["Restaurant"]["hasMenu"]["hasMenuSection"]
    assert len(sections) == 8
    prices = {item["name"]: item["offers"]["price"] for s in sections for item in s["hasMenuItem"]}
    assert len(prices) == 50
    assert (prices["X-Burguer"], prices["Refri Lata"], prices["Tábua para Dois"]) == ("18.00", "6.00", "50.00")


def test_json_ld_das_perguntas_frequentes(client):
    faq = graph(client)["FAQPage"]["mainEntity"]
    assert len(faq) == 6
    answers = {q["name"]: q["acceptedAnswer"]["text"] for q in faq}
    assert answers["Qual o horário de funcionamento da NuMelhor Lanches?"].startswith(
        "Ter a Qui, 18h às 23h30 · Sex e Sáb, 18h às 23h59 · Dom, 18h às 23h30.")
    assert answers["A NuMelhor Lanches faz entrega em Criciúma?"] == (
        "Faz. Entregamos até 10 km da loja, em média em 40 a 60 min. A taxa é de R$ 5,00 até 2 km, "
        "mais R$ 1,50 por km a mais, e o valor certinho aparece quando você marca o endereço no mapa.")
    # As mesmas perguntas aparecem para o cliente na página
    html = client.get("/").get_data(as_text=True)
    assert "A NuMelhor Lanches faz entrega em Criciúma?" in html


def test_json_ld_nao_tem_campos_vazios(client):
    data = ld_json(client.get("/").get_data(as_text=True))
    assert not [v for v in walk(data) if v in (None, "")]


def test_json_ld_acompanha_as_configuracoes(client, app):
    with app.app_context():
        set_settings({"delivery_max_km": "7", "store_instagram": "", "hours_0": "11:00-14:00"})
    r = graph(client)["Restaurant"]
    assert r["areaServed"]["geoRadius"] == 7000
    assert "sameAs" not in r
    assert len(r["openingHoursSpecification"]) == 7


def test_nome_da_loja_com_codigo_malicioso_nao_quebra_a_pagina(client, app):
    evil = 'NuMelhor</script><script>alert(1)</script>'
    with app.app_context():
        set_settings({"store_name": evil})
    html = client.get("/").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html
    assert ld_json(html)["@graph"][0]["name"] == evil


# ---------- robots.txt, sitemap e /saude ----------

def test_robots_txt(client):
    resp = client.get("/robots.txt")
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    lines = resp.get_data(as_text=True).splitlines()
    assert lines[:2] == ["User-agent: *", "Allow: /"]
    for path in ("/admin", "/api/", "/pedido/", "/checkout", "/meus-pedidos", "/simulador/", "/webhooks/"):
        assert f"Disallow: {path}" in lines
    assert f"Sitemap: {BASE_URL}/sitemap.xml" in lines


def test_sitemap(client):
    resp = client.get("/sitemap.xml")
    assert resp.status_code == 200
    assert resp.mimetype == "application/xml"
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    root = ET.fromstring(resp.data)
    assert [loc.text for loc in root.findall("s:url/s:loc", ns)] == [f"{BASE_URL}/", f"{BASE_URL}/privacidade"]


def test_saude(client):
    resp = client.get("/saude")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert re.fullmatch(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}", data["ligado_desde"])


@pytest.mark.parametrize("path", ["/privacidade", "/checkout", "/meus-pedidos", "/manifest.webmanifest",
                                  "/static/css/app.css", "/uploads/seed-xis.webp"])
def test_outras_paginas_abrem(client, path):
    assert client.get(path).status_code == 200


def test_manifesto_do_app(client):
    data = json.loads(client.get("/manifest.webmanifest").get_data(as_text=True))
    assert (data["name"], data["short_name"], data["start_url"]) == ("NuMelhor Lanches", "NuMelhor", "/")


# ---------- Erros ----------

def test_pagina_inexistente_da_404_sem_indexar(client):
    resp = client.get("/pagina-que-nao-existe")
    assert resp.status_code == 404
    html = resp.get_data(as_text=True)
    assert '<meta name="robots" content="noindex">' in html
    assert "Essa página não existe." in html


def test_api_inexistente_responde_em_json(client):
    resp = client.get("/api/nao-existe")
    assert resp.status_code == 404
    assert resp.get_json() == {"ok": False, "error": "Essa página não existe."}


@pytest.mark.parametrize("name", ["../numelhor.db", "foto.exe", "seed-xis.webp.php", "nao-existe.webp"])
def test_fotos_so_servem_imagens_da_pasta(client, name):
    assert client.get(f"/uploads/{name}").status_code == 404


# ---------- Cabeçalhos de segurança ----------

@pytest.mark.parametrize("path", ["/", "/saude", "/pagina-que-nao-existe", "/admin/login", "/static/css/app.css"])
def test_cabecalhos_de_seguranca(client, path):
    h = client.get(path).headers
    csp = [d.strip() for d in h["Content-Security-Policy"].split(";")]
    for directive in ("default-src 'self'", "script-src 'self'", "object-src 'none'", "base-uri 'self'",
                      "form-action 'self'", "frame-ancestors 'none'"):
        assert directive in csp
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in h["Permissions-Policy"] and "microphone=()" in h["Permissions-Policy"]
    assert h["Cross-Origin-Opener-Policy"] == "same-origin"
    assert "Strict-Transport-Security" not in h  # só em produção (https)


def test_paginas_privadas_nao_ficam_em_cache(client):
    assert client.get("/admin/login").headers["Cache-Control"] == "no-store"
    assert client.get("/api/pedido/" + "a" * 24).headers["Cache-Control"] == "no-store"
    assert "no-store" not in client.get("/").headers.get("Cache-Control", "")


# ---------- Configuração de produção ----------

def test_producao_com_endereco_do_render(make_app):
    app = make_app(APP_ENV="production", PUBLIC_BASE_URL=None, RENDER_EXTERNAL_URL="https://numelhor.onrender.com",
                   TRUST_PROXY="1")
    assert app.config["PUBLIC_BASE_URL"] == "https://numelhor.onrender.com"
    assert (app.config["SESSION_COOKIE_NAME"], app.config["SESSION_COOKIE_SECURE"]) == ("__Host-nm", True)
    client = app.test_client()
    resp = client.get("/saude", base_url="https://numelhor.onrender.com")
    assert resp.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    html = client.get("/", base_url="https://numelhor.onrender.com").get_data(as_text=True)
    assert ld_json(html)["@graph"][0]["url"] == "https://numelhor.onrender.com/"


def test_producao_sem_https_nao_sobe(make_app):
    with pytest.raises(RuntimeError, match="https"):
        make_app(APP_ENV="production", PUBLIC_BASE_URL="http://numelhor.com.br")


def test_chave_secreta_curta_nao_sobe(make_app):
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        make_app(SECRET_KEY="curta")
