"""Gera o "Pix copia e cola" (BR Code, padrão do Banco Central) e o QR Code em PNG."""
import base64
import io
import re
import unicodedata

import qrcode


def _ascii(text, size):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9 .\-]", "", text).strip().upper()[:size] or "NA"


def _field(tag, value):
    return f"{tag}{len(value):02d}{value}"


def _crc16(payload):
    crc = 0xFFFF
    for byte in payload.encode():
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"


def brcode(key, receiver, city, amount_cents, txid):
    """Pix estático com valor. Serve para cobrar na entrega/retirada (não confirma sozinho)."""
    account = _field("00", "br.gov.bcb.pix") + _field("01", key.strip())
    payload = (
        _field("00", "01")
        + _field("26", account)
        + _field("52", "0000")
        + _field("53", "986")
        + (_field("54", f"{amount_cents / 100:.2f}") if amount_cents else "")
        + _field("58", "BR")
        + _field("59", _ascii(receiver, 25))
        + _field("60", _ascii(city, 15))
        + _field("62", _field("05", re.sub(r"[^A-Za-z0-9]", "", txid)[:25] or "***"))
        + "6304"
    )
    return payload + _crc16(payload)


def qr_png_b64(text):
    img = qrcode.make(text, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()
