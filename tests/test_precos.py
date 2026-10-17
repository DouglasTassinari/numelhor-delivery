"""Preço dos itens da sacola (pricing.price_cart) e preço "a partir de" do cardápio.

Os valores esperados foram calculados à mão a partir do cardápio padrão (numelhor/db.py).
"""
import pytest

from numelhor.db import get_db
from numelhor.pricing import CartError, price_cart
from numelhor.public import menu_data

pytestmark = pytest.mark.usefixtures("ctx")


def price(*items):
    return price_cart(list(items))


# ---------- Preço base, adicionais e quantidade ----------

def test_preco_base_do_produto(menu):
    lines, subtotal = price(menu.item("X-Burguer"))
    assert subtotal == 1800
    assert lines[0]["name"] == "X-Burguer"
    assert lines[0]["unit_price_cents"] == 1800
    assert lines[0]["line_total_cents"] == 1800


def test_adicionais_pagos_somam_no_preco(menu):
    # X Salada R$ 20,00 + Bacon R$ 5,00 + Cheddar R$ 5,00 + Ovo R$ 2,00 = R$ 32,00
    lines, subtotal = price(menu.item("X Salada", options=["Bacon", "Cheddar", "Ovo (egg)"]))
    assert lines[0]["unit_price_cents"] == 3200
    assert subtotal == 3200
    assert {o["name"] for o in lines[0]["options"]} == {"Bacon", "Cheddar", "Ovo (egg)"}
    assert all(o["group"] == "Adicionais" for o in lines[0]["options"])


def test_montagem_aberto_no_prato_cobra_5_reais(menu):
    lines, subtotal = price(menu.item("X-Burguer", options=["Aberto (no prato)"]))
    assert subtotal == 1800 + 500


def test_todos_os_13_adicionais_sao_aceitos(menu):
    # 200 + 300 + 300 + 400 x 4 + 500 x 4 + 800 x 2 = 6000
    todos = ["Ovo (egg)", "Picles", "Cebola roxa", "Cebola caramelizada", "Salada", "Reboco", "Calabresa",
             "Bacon", "Cheddar", "Carne de burguer", "Galinha", "Coração", "Fritas"]
    _, subtotal = price(menu.item("X-Burguer", options=todos))
    assert subtotal == 1800 + 6000


def test_adicionais_de_porcao(menu):
    # Meia Fritas R$ 15,00 + Bacon R$ 8,00 + Queijo R$ 6,00
    _, subtotal = price(menu.item("Meia Fritas", options=["Bacon", "Queijo"]))
    assert subtotal == 2900


def test_quantidade_multiplica_o_preco_com_adicionais(menu):
    lines, subtotal = price(menu.item("X Salada", qty=3, options=["Bacon"]))
    assert lines[0]["unit_price_cents"] == 2500
    assert lines[0]["line_total_cents"] == 7500
    assert subtotal == 7500


def test_varios_itens_somam_no_subtotal(menu):
    # 2 x X-Burguer (3600) + 1 Refri Lata (600) + 3 Pastel de Carne (2100)
    lines, subtotal = price(menu.item("X-Burguer", qty=2),
                            menu.item("Refri Lata", options=["Coca-Cola"]),
                            menu.item("Pastel de Carne", qty=3))
    assert [ln["line_total_cents"] for ln in lines] == [3600, 600, 2100]
    assert subtotal == 6300


def test_quantidade_maxima_20_e_aceita(menu):
    _, subtotal = price(menu.item("Água", qty=20))
    assert subtotal == 20 * 400


# ---------- Grupos de opção obrigatórios ----------

def test_refrigerante_exige_escolher_o_sabor(menu):
    with pytest.raises(CartError, match="Falta escolher sabor em Refri Lata"):
        price(menu.item("Refri Lata"))


def test_refrigerante_com_sabor_custa_o_preco_base(menu):
    lines, subtotal = price(menu.item("Refri Lata", options=["Guaraná"]))
    assert subtotal == 600
    assert lines[0]["options"] == [{"group": "Sabor", "name": "Guaraná", "price_cents": 0}]


def test_refrigerante_nao_aceita_dois_sabores(menu):
    with pytest.raises(CartError, match="no máximo 1 em sabor"):
        price(menu.item("Refri Lata", options=["Coca-Cola", "Guaraná"]))


def test_suco_exige_sabor(menu):
    with pytest.raises(CartError, match="Falta escolher sabor em Suco"):
        price(menu.item("Suco"))
    _, subtotal = price(menu.item("Suco", options=["Uva"]))
    assert subtotal == 500


def test_combos_exigem_a_bebida(menu):
    with pytest.raises(CartError, match="Falta escolher bebida em Combo Kids"):
        price(menu.item("Combo Kids"))
    with pytest.raises(CartError, match="Falta escolher sabor do refri em Combo Casal"):
        price(menu.item("Combo Casal"))
    _, subtotal = price(menu.item("Combo Kids", options=["Suco de uva"]),
                        menu.item("Combo Casal", options=["Laranja"]))
    assert subtotal == 1800 + 5000


def test_opcao_de_outro_produto_e_recusada(menu):
    item = menu.item("Refri Lata", options=["Coca-Cola"])
    item["options"].append(menu.option("Suco", "Uva"))
    with pytest.raises(CartError, match="Opção inválida em Refri Lata"):
        price(item)


# ---------- Ingredientes retirados ----------

def test_retirar_ingrediente_nao_muda_o_preco(menu):
    lines, subtotal = price(menu.item("X Salada", removed=["Tomate", "Alface"]))
    assert subtotal == 2000
    assert lines[0]["removed"] == ["Tomate", "Alface"]


def test_ingrediente_que_nao_pode_ser_retirado(menu):
    with pytest.raises(CartError, match="Ingrediente inválido em X Salada"):
        price(menu.item("X Salada", removed=["Pão"]))


def test_ingrediente_de_outro_produto_e_recusado(menu):
    item = menu.item("X Salada")
    item["removed"] = [menu.ingredient("Nuclássico", "Barbecue")]
    with pytest.raises(CartError, match="Ingrediente inválido"):
        price(item)


# ---------- Molhos ----------

def test_molhos_gratis_nao_mudam_o_preco(menu):
    lines, subtotal = price(menu.item("Pastel de Carne", sauces=["Ketchup", "Mostarda"]))
    assert subtotal == 700
    assert [s["name"] for s in lines[0]["sauces"]] == ["Ketchup", "Mostarda"]


def test_molho_pago_soma_no_preco(menu):
    get_db().execute("UPDATE sauces SET price_cents = 150 WHERE name = 'Barbecue'")
    _, subtotal = price(menu.item("X-Burguer", qty=2, sauces=["Barbecue", "Ketchup"]))
    assert subtotal == 2 * (1800 + 150)


def test_produto_sem_molho_recusa_molho(menu):
    with pytest.raises(CartError, match="Refri Lata não leva molho"):
        price(menu.item("Refri Lata", options=["Coca-Cola"], sauces=["Ketchup"]))


def test_molho_desativado_e_recusado(menu):
    get_db().execute("UPDATE sauces SET active = 0 WHERE name = 'Mostarda'")
    with pytest.raises(CartError, match="molhos escolhidos não está disponível"):
        price(menu.item("X-Burguer", sauces=["Mostarda"]))


# ---------- Erros ----------

def test_id_de_produto_inexistente(menu):
    with pytest.raises(CartError, match="não está mais disponível"):
        price({"product_id": 99999, "qty": 1})


def test_id_de_produto_que_nao_e_numero(menu):
    with pytest.raises(CartError, match="Valor inválido em produto"):
        price({"product_id": "abc", "qty": 1})


def test_produto_inativo(menu):
    get_db().execute("UPDATE products SET active = 0 WHERE name = 'X Bacon'")
    with pytest.raises(CartError, match="não está mais disponível"):
        price(menu.item("X Bacon"))


def test_produto_de_categoria_inativa(menu):
    get_db().execute("UPDATE categories SET active = 0 WHERE name = 'Bebidas'")
    with pytest.raises(CartError, match="não está mais disponível"):
        price(menu.item("Água"))


@pytest.mark.parametrize("qty", [0, -1, 21, 1000, 10**9])
def test_quantidade_absurda(menu, qty):
    with pytest.raises(CartError, match="Quantidade deve ser entre 1 e 20"):
        price(menu.item("X-Burguer", qty=qty))


def test_quantidade_que_nao_e_numero(menu):
    with pytest.raises(CartError, match="Valor inválido em quantidade"):
        price(menu.item("X-Burguer", qty="muitos"))


@pytest.mark.parametrize("cart", [[], None, "x", {"product_id": 1}])
def test_sacola_vazia_ou_invalida(cart):
    with pytest.raises(CartError, match="Sua sacola está vazia"):
        price_cart(cart)


def test_item_que_nao_e_objeto(menu):
    with pytest.raises(CartError, match="Item inválido"):
        price("X-Burguer")


def test_lista_de_ingredientes_invalida(menu):
    item = menu.item("X Salada")
    item["removed"] = "Tomate"
    with pytest.raises(CartError, match="Lista inválida em ingredientes"):
        price(item)


def test_pedido_com_mais_de_40_linhas(menu):
    agua = menu.item("Água")
    price(*[agua] * 40)
    with pytest.raises(CartError, match="Pedido grande demais"):
        price(*[agua] * 41)


def test_observacao_do_item_e_cortada_em_140_letras(menu):
    lines, _ = price(menu.item("X-Burguer", notes="  bem passado " + "x" * 300))
    assert len(lines[0]["notes"]) == 140
    assert lines[0]["notes"].startswith("bem passado")


# ---------- Preço "a partir de" no cardápio ----------

def find(menu_json, name):
    return next(p for c in menu_json["categories"] for p in c["products"] if p["name"] == name)


def test_a_partir_de_ignora_adicionais_opcionais():
    item = find(menu_data(), "X-Burguer")
    assert item["from_cents"] == 1800
    assert item["price_varies"] is False


def test_a_partir_de_com_sabores_gratis():
    item = find(menu_data(), "Refri Lata")
    assert item["from_cents"] == 600
    assert item["price_varies"] is False


def test_a_partir_de_soma_a_opcao_obrigatoria_mais_barata(menu):
    for name, cents in (("Coca-Cola", 300), ("Guaraná", 200), ("Laranja", 250)):
        get_db().execute("UPDATE options SET price_cents = ? WHERE id = ?", (cents, menu.option("Refri 2 Litros", name)))
    get_db().commit()
    item = find(menu_data(), "Refri 2 Litros")
    assert item["from_cents"] == 1500 + 200
    assert item["price_varies"] is True
    # E o pedido cobra a opção escolhida
    _, subtotal = price(menu.item("Refri 2 Litros", options=["Coca-Cola"]))
    assert subtotal == 1800


def test_preco_antigo_so_aparece_se_for_maior():
    get_db().execute("UPDATE products SET old_price_cents = 2500 WHERE name = 'X Salada'")
    get_db().execute("UPDATE products SET old_price_cents = 1500 WHERE name = 'X-Burguer'")
    get_db().commit()
    data = menu_data()
    assert find(data, "X Salada")["old_price_cents"] == 2500
    assert find(data, "X-Burguer")["old_price_cents"] is None


def test_cardapio_esconde_produto_e_categoria_inativos():
    get_db().execute("UPDATE products SET active = 0 WHERE name = 'X Bacon'")
    get_db().execute("UPDATE categories SET active = 0 WHERE name = 'Bebidas'")
    get_db().commit()
    data = menu_data()
    names = [p["name"] for c in data["categories"] for p in c["products"]]
    assert "X Bacon" not in names
    assert "Água" not in names
    assert "Bebidas" not in [c["name"] for c in data["categories"]]
    assert len(data["categories"]) == 7
