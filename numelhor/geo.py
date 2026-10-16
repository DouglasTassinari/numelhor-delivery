"""Busca de endereço (OpenStreetMap) e cálculo da taxa de entrega por distância."""
import math
import time

import requests
from flask import current_app

PHOTON_URL = "https://photon.komoot.io/api/"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"

_cache = {}
CACHE_SECONDS = 600


def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def valid_coords(lat, lng):
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    # Limites aproximados do Brasil
    if not (-34.0 <= lat <= 5.5 and -74.0 <= lng <= -34.0):
        return None
    return lat, lng


def delivery_quote(cfg, lat, lng):
    coords = valid_coords(lat, lng)
    if not coords:
        return {"ok": False, "error": "Marque o local de entrega no mapa."}
    store = valid_coords(cfg["store_lat"], cfg["store_lng"])
    # Distância em linha reta x 1,3 para aproximar o trajeto pelas ruas.
    dist = round(haversine_km(store[0], store[1], *coords) * 1.3, 2)
    max_km = float(cfg["delivery_max_km"])
    if dist > max_km:
        return {"ok": False, "distance_km": dist,
                "error": f"Esse endereço fica a ~{dist:.1f} km e a gente entrega até {max_km:g} km. "
                         "Chama no WhatsApp que a gente vê!"}
    extra_km = max(0.0, dist - float(cfg["delivery_included_km"]))
    fee = int(cfg["delivery_base_cents"]) + math.ceil(extra_km) * int(cfg["delivery_per_km_cents"])
    return {"ok": True, "distance_km": dist, "fee_cents": fee, "lat": coords[0], "lng": coords[1]}


def _headers():
    contact = current_app.config.get("CONTACT_EMAIL") or "contato@example.com"
    return {"User-Agent": f"NuMelhor-Delivery/0.1 ({contact})", "Accept-Language": "pt-BR"}


def _cached(key, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = fn()
    if len(_cache) > 2000:
        _cache.clear()
    _cache[key] = (time.time(), value)
    return value


def _photon_address(props, with_number):
    street = props.get("street") or (props.get("name") if props.get("type") == "street" else None)
    parts = []
    if street:
        number = props.get("housenumber") if with_number else None
        parts.append(street + (f", {number}" if number else ""))
    elif props.get("name"):
        parts.append(props["name"])
    for k in ("district", "locality", "city", "state"):
        v = props.get(k)
        if v and v not in parts:
            parts.append(v)
    return " - ".join(parts)


def search(query, near_lat, near_lng):
    def fetch():
        r = requests.get(PHOTON_URL, params={
            "q": query, "lat": near_lat, "lon": near_lng, "limit": 8, "location_bias_scale": 0.5,
        }, headers=_headers(), timeout=6)
        r.raise_for_status()
        out = []
        for feat in r.json().get("features", []):
            props = feat.get("properties", {})
            if props.get("countrycode", "").upper() != "BR":
                continue
            lng, lat = feat["geometry"]["coordinates"][:2]
            out.append({"label": _photon_address(props, True), "address": _photon_address(props, False),
                        "lat": lat, "lng": lng, "number": props.get("housenumber") or ""})
        return out[:6]
    return _cached(("s", query.lower(), round(near_lat, 2), round(near_lng, 2)), fetch)


def reverse(lat, lng):
    def fetch():
        r = requests.get(NOMINATIM_REVERSE, params={
            "lat": lat, "lon": lng, "format": "jsonv2", "addressdetails": 1, "zoom": 18,
        }, headers=_headers(), timeout=6)
        r.raise_for_status()
        a = r.json().get("address", {})
        street = a.get("road") or a.get("pedestrian") or a.get("residential") or ""
        bairro = a.get("suburb") or a.get("neighbourhood") or a.get("city_district") or ""
        city = a.get("city") or a.get("town") or a.get("village") or ""
        address = " - ".join(p for p in [street, bairro, city] if p)
        return {"address": address, "number": a.get("house_number", "")}
    return _cached(("r", round(lat, 5), round(lng, 5)), fetch)
