"""Horário de funcionamento (numelhor/hours.py).

Horário padrão (numelhor/db.py): segunda fechado; terça a quinta e domingo 18:00-23:30; sexta e sábado 18:00-23:59.
Semana usada nos testes (outubro de 2026): seg 12, ter 13, qua 14, qui 15, sex 16, sáb 17, dom 18.
"""
from datetime import datetime, timedelta

import pytest

from numelhor import hours
from numelhor.db import DEFAULT_SETTINGS
from numelhor.hours import BRT


def at(day, hh, mm=0):
    return datetime(2026, 10, day, hh, mm, tzinfo=BRT)


def cfg(**changes):
    return {**DEFAULT_SETTINGS, **changes}


def status(when, **changes):
    return hours.store_status(cfg(**changes), when)


def test_semana_de_teste_comeca_na_segunda():
    assert [at(d, 12).weekday() for d in range(12, 19)] == [0, 1, 2, 3, 4, 5, 6]


# ---------- Aberto e fechado ----------

def test_aberta_dentro_do_horario():
    assert status(at(13, 19)) == {"open": True, "label": "Aberto · fecha às 23h30", "mode": "auto"}


def test_aberta_no_minuto_em_que_abre():
    assert status(at(13, 18, 0))["open"] is True


def test_fechada_antes_de_abrir():
    assert status(at(13, 17, 59)) == {"open": False, "label": "Fechado · abre hoje às 18h", "mode": "auto"}


def test_fechada_no_minuto_em_que_fecha():
    assert status(at(13, 23, 30)) == {"open": False, "label": "Fechado · abre amanhã às 18h", "mode": "auto"}


def test_sexta_fecha_as_23h59():
    assert status(at(16, 23, 58))["label"] == "Aberto · fecha às 23h59"


def test_dia_fechado():
    # Segunda é folga: abre na terça
    assert status(at(12, 20)) == {"open": False, "label": "Fechado · abre amanhã às 18h", "mode": "auto"}


def test_proxima_abertura_pula_o_dia_fechado():
    # Domingo depois de fechar: segunda é folga, então abre terça
    assert status(at(18, 23, 45))["label"] == "Fechado · abre terça às 18h"


def test_sem_nenhum_horario_fica_fechado_sem_previsao():
    closed = {f"hours_{i}": "" for i in range(7)}
    assert status(at(13, 19), **closed) == {"open": False, "label": "Fechado", "mode": "auto"}


# ---------- Horário que passa da meia-noite ----------

def test_turno_que_passa_da_meia_noite_antes_da_meia_noite():
    assert status(at(16, 23, 30), hours_4="18:00-01:00") == {
        "open": True, "label": "Aberto · fecha às 1h", "mode": "auto"}


def test_turno_que_passa_da_meia_noite_depois_da_meia_noite():
    # Sábado 00:30 ainda é o turno de sexta
    assert status(at(17, 0, 30), hours_4="18:00-01:00") == {
        "open": True, "label": "Aberto · fecha às 1h", "mode": "auto"}


def test_turno_que_passa_da_meia_noite_ja_terminou():
    assert status(at(17, 1, 0), hours_4="18:00-01:00") == {
        "open": False, "label": "Fechado · abre hoje às 18h", "mode": "auto"}


def test_madrugada_de_segunda_com_domingo_ate_as_2h():
    # Segunda é folga, mas o turno de domingo ainda está rolando
    assert status(at(12, 1, 0), hours_6="18:00-02:00")["open"] is True
    assert status(at(12, 2, 0), hours_6="18:00-02:00")["open"] is False


# ---------- "Abrir agora" / "Fechar agora" do painel ----------

def test_fechado_pelo_painel_com_prazo():
    until = int(at(14, 18).timestamp())
    assert status(at(13, 19), open_mode="fechado", open_until=str(until)) == {
        "open": False, "label": "Fechado agora · abre amanhã às 18h", "mode": "fechado"}


def test_aberto_pelo_painel_com_prazo():
    until = int(at(13, 23, 30).timestamp())
    assert status(at(13, 10), open_mode="aberto", open_until=str(until)) == {
        "open": True, "label": "Aberto agora · fecha às 23h30", "mode": "aberto"}


def test_aberto_pelo_painel_sem_prazo():
    assert status(at(12, 10), open_mode="aberto") == {"open": True, "label": "Aberto agora", "mode": "aberto"}


def test_prazo_vencido_volta_ao_horario_normal():
    until = int(at(13, 12).timestamp())
    assert status(at(13, 15), open_mode="aberto", open_until=str(until)) == {
        "open": False, "label": "Fechado · abre hoje às 18h", "mode": "auto"}


def test_proxima_abertura():
    assert hours.next_opening_at(cfg(), at(13, 19)) == at(14, 18)
    assert hours.next_opening_at(cfg(), at(13, 10)) == at(13, 18)
    assert hours.next_opening_at(cfg(), at(18, 23, 45)) == at(13, 18) + timedelta(days=7)
    assert hours.next_opening_at(cfg(**{f"hours_{i}": "" for i in range(7)}), at(13, 19)) is None


def test_ate_quando_fica_aberta_ao_clicar_abrir_agora():
    assert hours.next_closing_at(cfg(), at(13, 19)) == at(13, 23, 30)
    assert hours.next_closing_at(cfg(hours_4="18:00-01:00"), at(17, 0, 30)) == at(17, 1, 0)
    # Segunda (folga) ao meio-dia: fica aberta por 3 horas
    assert hours.next_closing_at(cfg(), at(12, 12)) == at(12, 15)


# ---------- Textos ----------

def test_textos_da_semana():
    assert hours.week(cfg()) == [
        ("Segunda", "Fechado"),
        ("Terça", "18h às 23h30"),
        ("Quarta", "18h às 23h30"),
        ("Quinta", "18h às 23h30"),
        ("Sexta", "18h às 23h59"),
        ("Sábado", "18h às 23h59"),
        ("Domingo", "18h às 23h30"),
    ]


def test_resumo_agrupa_dias_seguidos():
    assert hours.summary(cfg()) == "Ter a Qui, 18h às 23h30 · Sex e Sáb, 18h às 23h59 · Dom, 18h às 23h30"


def test_resumo_com_todos_os_dias_iguais():
    same = {f"hours_{i}": "11:00-14:30" for i in range(7)}
    assert hours.summary(cfg(**same)) == "Seg a Dom, 11h às 14h30"


def test_resumo_sem_horario():
    assert hours.summary(cfg(**{f"hours_{i}": "" for i in range(7)})) == "Sem horário definido"


@pytest.mark.parametrize("text, expected", [
    ("18:00-23:30", (1080, 1410)),
    ("18:00-01:00", (1080, 60)),
    (" 09:15-14:00 ", (555, 840)),
    ("", None),
    ("18:00-18:00", None),
    ("24:00-23:00", None),
    ("18h-23h", None),
    ("fechado", None),
])
def test_leitura_do_horario(text, expected):
    assert hours.parse(text) == expected


@pytest.mark.parametrize("minutes, text", [(1080, "18h"), (1410, "23h30"), (60, "1h"), (1439, "23h59"), (5, "0h05")])
def test_formato_da_hora(minutes, text):
    assert hours.fmt(minutes) == text
