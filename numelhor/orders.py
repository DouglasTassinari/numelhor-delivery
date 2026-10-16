"""Regras do pedido: status, pagamento confirmado e mensagens de WhatsApp."""
import json
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from flask import current_app

from .db import audit, get_db, settings
from .payments import PAYMENT_WINDOW_MINUTES, PaymentError, provider

STATUS_LABELS = {
    "aguardando_pagamento": "Aguardando pagamento",
    "novo": "Pedido recebido",
    "confirmado": "Confirmado pela loja",
    "em_preparo": "Em preparo",
    "saiu_entrega": "Saiu para entrega",
    "pronto_retirada": "Pronto para retirada",
    "concluido": "Concluído",
    "cancelado": "Cancelado",
}
PAYMENT_LABELS = {
    "pendente": "Aguardando pagamento",
    "pago": "Pago pelo site",
    "na_entrega": "Pagar na entrega/retirada",
    "recebido": "Recebido na entrega/retirada",
    "recusado": "Pagamento recusado",
    "expirado": "Pagamento expirado",
}
METHOD_LABELS = {
    "pix": "Pix",
    "cartao": "Cartão (crédito ou débito)",
    "credito": "Cartão de crédito",
    "debito": "Cartão de débito",
    "dinheiro": "Dinheiro",
}

# Próximos passos permitidos no painel
TRANSITIONS = {
    "aguardando_pagamento": ["cancelado"],
    "novo": ["confirmado", "cancelado"],
    "confirmado": ["em_preparo", "cancelado"],
    "em_preparo": ["saiu_entrega", "pronto_retirada", "cancelado"],
    "saiu_entrega": ["concluido", "cancelado"],
    "pronto_retirada": ["concluido", "cancelado"],
    "concluido": [],
    "cancelado": [],
}
ACTION_LABELS = {
    "confirmado": "Confirmar pedido",
    "em_preparo": "Iniciar preparo",
    "saiu_entrega": "Saiu para entrega",
    "pronto_retirada": "Pronto para retirada",
    "concluido": "Concluir",
    "cancelado": "Cancelar",
}


def allowed_actions(order):
    out = []
    for nxt in TRANSITIONS.get(order["status"], []):
        if nxt == "saiu_entrega" and order["fulfillment"] != "entrega":
            continue
        if nxt == "pronto_retirada" and order["fulfillment"] != "retirada":
            continue
        out.append(nxt)
    return out


def brl(cents):
    s = f"{(cents or 0) / 100:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def phone_digits(raw):
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) in (10, 11):
        digits = "55" + digits
    return digits


def wa_link(number, text):
    return f"https://wa.me/{phone_digits(number)}?text={quote(text)}"


def maps_link(order):
    if order["lat"] is None:
        return None
    return f"https://www.google.com/maps/search/?api=1&query={order['lat']:.6f},{order['lng']:.6f}"


def get_order(order_id=None, token=None):
    db = get_db()
    if token is not None:
        return db.execute("SELECT * FROM orders WHERE token = ?", (token,)).fetchone()
    return db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()


def get_items(order_id):
    rows = get_db().execute("SELECT * FROM order_items WHERE order_id = ? ORDER BY id", (order_id,)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        d["removed"] = json.loads(d["removed"])
        d["sauces"] = json.loads(d["sauces"])
        d["options"] = json.loads(d.get("options") or "[]")
        items.append(d)
    return items


def set_status(order_id, status, payment_status=None):
    db = get_db()
    if payment_status:
        db.execute("UPDATE orders SET status = ?, payment_status = ?, updated_at = datetime('now') WHERE id = ?",
                   (status, payment_status, order_id))
    else:
        db.execute("UPDATE orders SET status = ?, updated_at = datetime('now') WHERE id = ?", (status, order_id))
    db.commit()


def expire_if_needed(order):
    """Pedido online não pago em 30 min é cancelado automaticamente."""
    if order["status"] != "aguardando_pagamento":
        return order
    created = datetime.strptime(order["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - created > timedelta(minutes=PAYMENT_WINDOW_MINUTES + 5):
        set_status(order["id"], "cancelado", "expirado")
        return get_order(order["id"])
    return order


def apply_payment(order, payment):
    """Aplica um pagamento consultado no Mercado Pago. Confere pedido e valor antes de aceitar."""
    if str(payment.get("external_reference")) != order["token"]:
        return False
    status = payment.get("status")
    if status == "approved":
        paid_cents = round(float(payment.get("transaction_amount") or 0) * 100)
        if paid_cents != order["total_cents"]:
            audit("pagamento_valor_divergente",
                  f"pedido {order['id']}: esperado {order['total_cents']}, recebido {paid_cents}", actor="sistema")
            return False
        mark_paid(order, str(payment.get("id")))
        return True
    if status in ("rejected", "cancelled") and order["status"] == "aguardando_pagamento":
        get_db().execute("UPDATE orders SET payment_status = 'recusado', updated_at = datetime('now') "
                         "WHERE id = ? AND status = 'aguardando_pagamento'", (order["id"],))
        get_db().commit()
    return False


def mark_paid(order, payment_id):
    db = get_db()
    cur = db.execute(
        "UPDATE orders SET status = CASE WHEN status = 'aguardando_pagamento' THEN 'novo' ELSE status END, "
        "payment_status = 'pago', paid_at = datetime('now'), payment_ref = ?, updated_at = datetime('now') "
        "WHERE id = ? AND payment_status != 'pago'",
        (payment_id, order["id"]),
    )
    db.commit()
    if cur.rowcount:
        audit("pagamento_aprovado", f"pedido {order['id']} ({brl(order['total_cents'])}) ref {payment_id}",
              actor="sistema")


def refresh_payment(order, min_interval=5):
    """Consulta o provedor no máximo a cada `min_interval` segundos por pedido."""
    if order["status"] != "aguardando_pagamento" or order["pay_when"] != "online":
        return order
    now = time.time()
    if now - (order["last_check_at"] or 0) < min_interval:
        return order
    db = get_db()
    db.execute("UPDATE orders SET last_check_at = ? WHERE id = ?", (now, order["id"]))
    db.commit()
    try:
        for payment in provider().fetch_payments(order):
            if apply_payment(order, payment):
                break
    except PaymentError:
        pass
    return get_order(order["id"])


# ---------- Mensagens ----------

def group_options(options):
    """[{'group': 'Adicionais', 'name': 'Bacon'}, ...] -> [('Adicionais', 'Bacon, Ovo'), ...]"""
    grouped = {}
    for o in options:
        grouped.setdefault(o["group"], []).append(o["name"])
    return [(g, ", ".join(n)) for g, n in grouped.items()]


def _items_text(items):
    lines = []
    for it in items:
        lines.append(f"• {it['qty']}x {it['name']} — {brl(it['line_total_cents'])}")
        for group, names in group_options(it.get("options", [])):
            lines.append(f"   ➕ {group}: {names}")
        if it["removed"]:
            lines.append(f"   ❌ SEM: {', '.join(it['removed'])}")
        if it["sauces"]:
            lines.append(f"   🥫 Molhos: {', '.join(s['name'] for s in it['sauces'])}")
        if it.get("notes"):
            lines.append(f"   📝 {it['notes']}")
    return "\n".join(lines)


def _payment_text(order):
    method = METHOD_LABELS[order["pay_method"]]
    if order["pay_when"] == "online":
        state = "PAGO ✅" if order["payment_status"] == "pago" else PAYMENT_LABELS[order["payment_status"]]
        return f"{method} pelo site — {state}"
    text = f"{method} na {'entrega' if order['fulfillment'] == 'entrega' else 'retirada'}"
    if order["pay_method"] == "dinheiro":
        if order["change_for_cents"]:
            text += f" (troco para {brl(order['change_for_cents'])})"
        else:
            text += " (não precisa de troco)"
    return text


def store_message(order, items):
    base = current_app.config["PUBLIC_BASE_URL"]
    cfg = settings()
    parts = [
        f"🍔 *NOVO PEDIDO #{order['id']}* — {cfg['store_name']}",
        "",
        f"👤 {order['customer_name']} — {order['customer_phone']}",
        "",
        "*Itens*",
        _items_text(items),
        "",
        f"Subtotal: {brl(order['subtotal_cents'])}",
    ]
    if order["fulfillment"] == "entrega":
        parts.append(f"Entrega: {brl(order['delivery_fee_cents'])}")
    parts.append(f"*TOTAL: {brl(order['total_cents'])}*")
    parts.append("")
    if order["fulfillment"] == "entrega":
        addr = order["address"] + (f", nº {order['address_number']}" if order["address_number"] else "")
        parts.append(f"🛵 *ENTREGA:* {addr}")
        if order["complement"]:
            parts.append(f"Compl.: {order['complement']}")
        if order["reference"]:
            parts.append(f"Ref.: {order['reference']}")
        parts.append(f"📍 Mapa: {maps_link(order)}")
    else:
        parts.append("🏪 *RETIRADA NA LOJA*")
    parts.append(f"💳 Pagamento: {_payment_text(order)}")
    if order["notes"]:
        parts.append(f"📝 Obs.: {order['notes']}")
    parts += ["", f"✅ Confirmar no sistema: {base}/admin/pedidos/{order['id']}"]
    return "\n".join(parts)


def customer_message(order):
    cfg = settings()
    base = current_app.config["PUBLIC_BASE_URL"]
    first = order["customer_name"].split(" ")[0]
    msgs = {
        "novo": "recebemos seu pedido",
        "confirmado": "seu pedido foi confirmado e já vai pra chapa",
        "em_preparo": "seu pedido está em preparo 🔥",
        "saiu_entrega": "seu pedido saiu para entrega 🛵",
        "pronto_retirada": "seu pedido está pronto para retirada 🏪",
        "concluido": "obrigado pela preferência! ❤️",
        "cancelado": "seu pedido foi cancelado. Qualquer dúvida, é só chamar aqui",
    }
    msg = msgs.get(order["status"], STATUS_LABELS[order["status"]].lower())
    return (f"Oi, {first}! Aqui é do {cfg['store_name']}: {msg}.\n"
            f"Pedido #{order['id']} — {brl(order['total_cents'])}\n"
            f"Acompanhe: {base}/pedido/{order['token']}")


def courier_message(order, items):
    addr = order["address"] + (f", nº {order['address_number']}" if order["address_number"] else "")
    collect = ("Já está PAGO — não cobrar." if order["payment_status"] == "pago"
               else f"COBRAR {brl(order['total_cents'])} — {_payment_text(order)}")
    lines = [
        f"🛵 *Entrega pedido #{order['id']}*",
        f"Cliente: {order['customer_name']} — {order['customer_phone']}",
        f"Endereço: {addr}",
    ]
    if order["complement"]:
        lines.append(f"Compl.: {order['complement']}")
    if order["reference"]:
        lines.append(f"Ref.: {order['reference']}")
    lines += [f"📍 {maps_link(order)}", f"💰 {collect}",
              f"Itens: {', '.join(str(i['qty']) + 'x ' + i['name'] for i in items)}"]
    return "\n".join(lines)
