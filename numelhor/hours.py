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


def _when(now, target):
    days = (target.date() - now.date()).days
    return "hoje" if days == 0 else ("amanhã" if days == 1 else WEEKDAYS[target.weekday()].lower())


def next_opening_at(cfg, now):
    """Data/hora da próxima abertura pelo horário (depois de agora), ou None."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for offset in range(0, 8):
        slot = parse(cfg[f"hours_{(now.weekday() + offset) % 7}"])
        if not slot:
            continue
        start = midnight + timedelta(days=offset, minutes=slot[0])
        if start > now:
            return start
    return None


def next_closing_at(cfg, now):
    """Até quando fica aberta ao clicar "Abrir agora": o fim do turno de hoje (ou o que está rolando).
    Fora do horário e sem turno pela frente hoje, mantém aberta por 3 horas."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    candidates = []
    for offset in (-1, 0):
        slot = parse(cfg[f"hours_{(now.weekday() + offset) % 7}"])
        if not slot:
            continue
        start, end = slot
        end_dt = midnight + timedelta(days=offset + (1 if end <= start else 0), minutes=end)
        if end_dt > now:
            candidates.append(end_dt)
    if candidates:
        return min(candidates)
    return now + timedelta(hours=3)


def _next_opening(cfg, now):
    start = next_opening_at(cfg, now)
    return f"abre {_when(now, start)} às {fmt(start.hour * 60 + start.minute)}" if start else None


def store_status(cfg, now=None):
    now = now or datetime.now(BRT)
    mode = cfg.get("open_mode", "auto")
    until = None
    if cfg.get("open_until"):
        try:
            until = datetime.fromtimestamp(int(cfg["open_until"]), BRT)
        except (TypeError, ValueError):
            until = None
    if mode in ("aberto", "fechado") and until and now >= until:
        mode = "auto"  # o "agora" já venceu: volta ao horário normal sozinho
    if mode == "fechado":
        label = "Fechado agora"
        if until:
            label += f" · abre {_when(now, until)} às {fmt(until.hour * 60 + until.minute)}"
        return {"open": False, "label": label, "mode": mode}
    if mode == "aberto":
        label = "Aberto agora"
        if until:
            label += f" · fecha às {fmt(until.hour * 60 + until.minute)}"
        return {"open": True, "label": label, "mode": mode}
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
