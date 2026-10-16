"""Criptografa segredos guardados no banco (ex.: token do Mercado Pago).

A chave vem do SECRET_KEY do servidor: quem copiar só o arquivo do banco não consegue ler o token.
Se o SECRET_KEY mudar, os segredos antigos ficam ilegíveis e precisam ser cadastrados de novo.
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


def _fernet():
    digest = hashlib.sha256(b"numelhor-secretbox:" + current_app.config["SECRET_KEY"].encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value):
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(value):
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def mask(value, keep=6):
    if not value:
        return ""
    return value[:keep] + "•" * 8 + value[-4:]
