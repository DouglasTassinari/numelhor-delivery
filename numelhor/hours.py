"""Horário de funcionamento: diz se a loja está aberta agora e monta os textos ("abre hoje às 18h")."""
import re
from datetime import datetime, timedelta, timezone

from .db import WEEKDAYS

BRT = timezone(timedelta(hours=-3))  # Brasil não tem mais horário de verão
SHORT = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
HOURS_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)-([01]\d|2[0-3]):([0-5]\d)$")


def parse(value):
    """'18:00-23:30' -> (1080, 1410) em minutos; vazio/ inválido -> None (fechado)."""
    m = HOURS_RE.match((value or "").strip())
    if not m:
        return None
    start = int(m.group(1)) * 60 + int(m.group(2))
    end = int(m.group(3)) * 60 + int(m.group(4))
    if start == end:
        return None
    return start, end


def fmt(minutes):
    h, m = divmod(minutes % (24 * 60), 60)
    return f"{h}h" if m == 0 else f"{h}h{m:02d}"


def _open_by_schedule(cfg, now):
    """Retorna (aberto, minuto em que fecha) considerando turnos que passam da meia-noite."""
    day = now.weekday()
    minute = now.hour * 60 + now.minute
    today = parse(cfg[f"hours_{day}"])
    if today:
        start, end = today
        if start < end and start <= minute < end:
            return True, end
        if start > end and minute >= start:  # ex.: 18:00-01:00, ainda antes da meia-noite
            return True, end
    yesterday = parse(cfg[f"hours_{(day - 1) % 7}"])
    if yesterday and yesterday[0] > yesterday[1] and minute < yesterday[1]:
        return True, yesterday[1]
    return False, None


def _next_opening(cfg, now):
    minute = now.hour * 60 + now.minute
    for offset in range(0, 8):
        day = (now.weekday() + offset) % 7
        slot = parse(cfg[f"hours_{day}"])
        if not slot or (offset == 0 and slot[0] <= minute):
            continue
        when = "hoje" if offset == 0 else ("amanhã" if offset == 1 else WEEKDAYS[day].lower())
        return f"abre {when} às {fmt(slot[0])}"
    return None


def store_status(cfg, now=None):
    now = now or datetime.now(BRT)
    mode = cfg.get("open_mode", "auto")
    if mode == "fechado":
        return {"open": False, "label": "Fechado no momento", "mode": mode}
    if mode == "aberto":
        return {"open": True, "label": "Aberto agora", "mode": mode}
    is_open, closes = _open_by_schedule(cfg, now)
    if is_open:
        return {"open": True, "label": f"Aberto · fecha às {fmt(closes)}", "mode": mode}
    nxt = _next_opening(cfg, now)
    return {"open": False, "label": f"Fechado · {nxt}" if nxt else "Fechado", "mode": mode}


def week(cfg):
    """Lista [(dia, '18h às 23h30' ou 'Fechado')] para mostrar ao cliente."""
    out = []
    for i, name in enumerate(WEEKDAYS):
        slot = parse(cfg[f"hours_{i}"])
        out.append((name, f"{fmt(slot[0])} às {fmt(slot[1])}" if slot else "Fechado"))
    return out


def summary(cfg):
    """Resumo curto: 'Ter a Dom, 18h às 23h30' (agrupa dias seguidos com o mesmo horário)."""
    parts, i = [], 0
    while i < 7:
        slot = cfg[f"hours_{i}"] if parse(cfg[f"hours_{i}"]) else ""
        j = i
        while j + 1 < 7 and (cfg[f"hours_{j + 1}"] if parse(cfg[f"hours_{j + 1}"]) else "") == slot:
            j += 1
        if slot:
            s, e = parse(slot)
            days = SHORT[i] if i == j else (f"{SHORT[i]} e {SHORT[j]}" if j == i + 1 else f"{SHORT[i]} a {SHORT[j]}")
            parts.append(f"{days}, {fmt(s)} às {fmt(e)}")
        i = j + 1
    return " · ".join(parts) or "Sem horário definido"
