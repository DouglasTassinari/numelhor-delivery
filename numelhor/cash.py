"""Caixa e relatórios: vendas por período, por forma de pagamento, produtos, horários e conferência da gaveta."""
import json
from datetime import datetime, timedelta

from .db import audit, get_db
from .hours import BRT
from .orders import brl

# Pedidos que contam como venda (aguardando pagamento e cancelados ficam de fora)
SALE_STATUSES = ("novo", "confirmado", "em_preparo", "saiu_entrega", "pronto_retirada", "concluido")
METHODS = [
    ("pix_site", "Pix pelo site"),
    ("cartao_site", "Cartão pelo site"),
    ("dinheiro", "Dinheiro"),
    ("pix_local", "Pix na entrega/retirada"),
    ("debito", "Débito"),
    ("credito", "Crédito"),
]
METHOD_LABELS = dict(METHODS)
CANCEL_REASONS = [
    "Cliente desistiu",
    "Endereço fora da área de entrega",
    "Faltou produto",
    "Pagamento não aprovado",
    "Pedido duplicado ou de teste",
    "Loja fechada / sem atendimento",
    "Outro motivo",
]
FMT = "%Y-%m-%d %H:%M:%S"
WEEKDAYS_SHORT = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def method_key(order):
    if order["pay_when"] == "online":
        return "pix_site" if order["pay_method"] == "pix" else "cartao_site"
    return {"pix": "pix_local", "dinheiro": "dinheiro", "debito": "debito", "credito": "credito"}.get(
        order["pay_method"], "dinheiro")


def to_utc_str(local_dt):
    """Data/hora de Brasília -> texto UTC igual ao gravado no banco."""
    return (local_dt.replace(tzinfo=None) + timedelta(hours=3)).strftime(FMT)


def to_local(utc_text):
    return datetime.strptime(utc_text, FMT) - timedelta(hours=3)


def local_label(utc_text):
    return to_local(utc_text).strftime("%d/%m/%Y %H:%M") if utc_text else "—"


# ---------- Períodos ----------

PERIODS = [
    ("hoje", "Hoje"), ("ontem", "Ontem"), ("semana", "Esta semana"), ("semana_passada", "Semana passada"),
    ("mes", "Este mês"), ("mes_passado", "Mês passado"), ("custom", "Escolher datas"),
]


def period_range(key, de=None, ate=None, now=None):
    """Retorna (início, fim, título) em horário de Brasília, fim exclusivo."""
    now = (now or datetime.now(BRT)).replace(tzinfo=None)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if key == "ontem":
        return today - timedelta(days=1), today, "Ontem"
    if key == "semana":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=7), "Esta semana"
    if key == "semana_passada":
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=7), "Semana passada"
    if key == "mes":
        start = today.replace(day=1)
        nxt = (start + timedelta(days=32)).replace(day=1)
        return start, nxt, "Este mês"
    if key == "mes_passado":
        end = today.replace(day=1)
        start = (end - timedelta(days=1)).replace(day=1)
        return start, end, "Mês passado"
    if key == "custom":
        try:
            start = datetime.strptime(de, "%Y-%m-%d")
            end = datetime.strptime(ate, "%Y-%m-%d") + timedelta(days=1)
            if end <= start or (end - start).days > 400:
                raise ValueError
            return start, end, f"{start:%d/%m/%Y} a {end - timedelta(days=1):%d/%m/%Y}"
        except (TypeError, ValueError):
            pass
    return today, today + timedelta(days=1), "Hoje"


# ---------- Resumo de vendas ----------

def summarize(start_local, end_local, by_day=False):
    db = get_db()
    start, end = to_utc_str(start_local), to_utc_str(end_local)
    rows = db.execute("SELECT * FROM orders WHERE created_at >= ? AND created_at < ? ORDER BY id",
                      (start, end)).fetchall()
    sales = [o for o in rows if o["status"] in SALE_STATUSES]
    cancelled = [o for o in rows if o["status"] == "cancelado"]
    waiting = [o for o in rows if o["status"] == "aguardando_pagamento"]

    by_method = {k: {"label": label, "count": 0, "total": 0} for k, label in METHODS}
    for o in sales:
        m = by_method[method_key(o)]
        m["count"] += 1
        m["total"] += o["total_cents"]

    gross = sum(o["total_cents"] for o in sales)
    fees = sum(o["delivery_fee_cents"] for o in sales)
    delivery = [o for o in sales if o["fulfillment"] == "entrega"]
    reasons = {}
    for o in cancelled:
        reason = o["cancel_reason"] or "Sem motivo informado"
        reasons.setdefault(reason, {"count": 0, "total": 0})
        reasons[reason]["count"] += 1
        reasons[reason]["total"] += o["total_cents"]

    hours = {}
    for o in sales:
        h = to_local(o["created_at"]).hour
        hours.setdefault(h, {"count": 0, "total": 0})
        hours[h]["count"] += 1
        hours[h]["total"] += o["total_cents"]

    days = []
    if by_day:
        cursor = start_local
        totals = {}
        for o in sales:
            d = to_local(o["created_at"]).date()
            totals.setdefault(d, {"count": 0, "total": 0})
            totals[d]["count"] += 1
            totals[d]["total"] += o["total_cents"]
        while cursor < end_local:
            t = totals.get(cursor.date(), {"count": 0, "total": 0})
            days.append({"label": f"{WEEKDAYS_SHORT[cursor.weekday()]} {cursor:%d/%m}", **t})
            cursor += timedelta(days=1)

    top = []
    if sales:
        ids = [o["id"] for o in sales]
        marks = ",".join("?" * len(ids))
        top = [dict(r) for r in db.execute(
            f"SELECT name, SUM(qty) AS qty, SUM(line_total_cents) AS total FROM order_items "  # nosec B608 - só "?"
            f"WHERE order_id IN ({marks}) GROUP BY name ORDER BY qty DESC, total DESC LIMIT 15", ids)]

    return {
        "count": len(sales),
        "gross": gross,
        "fees": fees,
        "products": gross - fees,
        "avg": gross // len(sales) if sales else 0,
        "delivery_count": len(delivery),
        "pickup_count": len(sales) - len(delivery),
        "delivery_total": sum(o["total_cents"] for o in delivery),
        "pickup_total": sum(o["total_cents"] for o in sales) - sum(o["total_cents"] for o in delivery),
        "by_method": by_method,
        "cash_sales": by_method["dinheiro"]["total"],
        "cancelled_count": len(cancelled),
        "cancelled_total": sum(o["total_cents"] for o in cancelled),
        "cancel_reasons": reasons,
        "waiting_count": len(waiting),
        "hours": [{"hour": h, **hours[h]} for h in sorted(hours)],
        "max_hour": max((v["count"] for v in hours.values()), default=0),
        "days": days,
        "max_day": max((d["total"] for d in days), default=0),
        "top": top,
        "max_top": max((t["qty"] for t in top), default=0),
    }


# ---------- Caixa (gaveta) ----------

def current_session():
    return get_db().execute("SELECT * FROM cash_sessions WHERE closed_at IS NULL ORDER BY id DESC LIMIT 1").fetchone()


def session_moves(session_id):
    return get_db().execute("SELECT * FROM cash_moves WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()


def session_report(session):
    """Resumo do caixa: vendas desde a abertura (até o fechamento) e dinheiro esperado na gaveta."""
    start = to_local(session["opened_at"])
    end = to_local(session["closed_at"]) if session["closed_at"] else datetime.now(BRT).replace(tzinfo=None)
    summary = summarize(start, end + timedelta(seconds=1))
    moves = session_moves(session["id"])
    sangrias = sum(m["amount_cents"] for m in moves if m["kind"] == "sangria")
    suprimentos = sum(m["amount_cents"] for m in moves if m["kind"] == "suprimento")
    expected = session["opening_cents"] + summary["cash_sales"] + suprimentos - sangrias
    return {"summary": summary, "moves": moves, "sangrias": sangrias, "suprimentos": suprimentos,
            "expected": expected, "start": start, "end": end}


def open_session(opening_cents, user):
    db = get_db()
    if current_session():
        return None
    sid = db.execute("INSERT INTO cash_sessions (opened_by, opening_cents) VALUES (?, ?)",
                     (user, opening_cents)).lastrowid
    db.commit()
    audit("caixa_aberto", f"caixa {sid} com {brl(opening_cents)}")
    return sid


def add_move(session_id, kind, amount_cents, reason, user):
    db = get_db()
    db.execute("INSERT INTO cash_moves (session_id, kind, amount_cents, reason, by_user) VALUES (?, ?, ?, ?, ?)",
               (session_id, kind, amount_cents, reason, user))
    db.commit()
    audit(f"caixa_{kind}", f"caixa {session_id}: {brl(amount_cents)} {reason}")


def close_session(session, counted_cents, notes, user):
    db = get_db()
    db.execute("UPDATE cash_sessions SET closed_at = datetime('now'), closed_by = ? WHERE id = ?",
               (user, session["id"]))
    closed = db.execute("SELECT * FROM cash_sessions WHERE id = ?", (session["id"],)).fetchone()
    rep = session_report(closed)
    snapshot = {k: v for k, v in rep["summary"].items() if k not in ("hours", "days")}
    db.execute("UPDATE cash_sessions SET counted_cents = ?, expected_cents = ?, notes = ?, summary = ? WHERE id = ?",
               (counted_cents, rep["expected"], notes, json.dumps(snapshot, ensure_ascii=False), session["id"]))
    db.commit()
    diff = counted_cents - rep["expected"]
    audit("caixa_fechado", f"caixa {session['id']}: vendas {brl(rep['summary']['gross'])}, "
                           f"gaveta {brl(counted_cents)} (diferença {brl(diff)})")


def sessions_between(start_local, end_local):
    return get_db().execute(
        "SELECT * FROM cash_sessions WHERE opened_at >= ? AND opened_at < ? ORDER BY id DESC",
        (to_utc_str(start_local), to_utc_str(end_local))).fetchall()
