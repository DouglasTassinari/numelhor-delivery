"""Cálculo do pedido feito SEMPRE no servidor. O navegador só manda IDs e quantidades."""
from .db import get_db

MAX_LINES = 40
MAX_QTY = 20
MAX_NOTE = 140


class CartError(ValueError):
    pass


def _int(value, field):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise CartError(f"Valor inválido em {field}.")


def _id_list(value, field):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 50:
        raise CartError(f"Lista inválida em {field}.")
    return sorted({_int(v, field) for v in value})


def product_groups(product_id):
    """Grupos de opções de um produto, com as opções dentro."""
    db = get_db()
    groups = []
    for g in db.execute("SELECT * FROM option_groups WHERE product_id = ? ORDER BY sort, id", (product_id,)):
        opts = db.execute("SELECT * FROM options WHERE group_id = ? ORDER BY sort, id", (g["id"],)).fetchall()
        if opts:
            groups.append({"id": g["id"], "name": g["name"], "min": g["min_choices"], "max": g["max_choices"],
                           "options": [{"id": o["id"], "name": o["name"], "price_cents": o["price_cents"]}
                                       for o in opts]})
    return groups


def price_cart(raw_items):
    if not isinstance(raw_items, list) or not raw_items:
        raise CartError("Sua sacola está vazia.")
    if len(raw_items) > MAX_LINES:
        raise CartError("Pedido grande demais. Fale com a loja pelo WhatsApp.")

    db = get_db()
    sauces = {r["id"]: r for r in db.execute("SELECT * FROM sauces WHERE active = 1")}
    lines = []
    subtotal = 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise CartError("Item inválido.")
        product_id = _int(raw.get("product_id"), "produto")
        qty = _int(raw.get("qty"), "quantidade")
        if not 1 <= qty <= MAX_QTY:
            raise CartError(f"Quantidade deve ser entre 1 e {MAX_QTY}.")
        product = db.execute(
            "SELECT p.* FROM products p JOIN categories c ON c.id = p.category_id "
            "WHERE p.id = ? AND p.active = 1 AND c.active = 1", (product_id,)
        ).fetchone()
        if not product:
            raise CartError("Um dos itens da sacola não está mais disponível. Remova e tente de novo.")
        pname = product["name"]

        ingredients = {r["id"]: r for r in db.execute(
            "SELECT * FROM ingredients WHERE product_id = ? ORDER BY sort, id", (product_id,))}
        removed_ids = _id_list(raw.get("removed"), "ingredientes")
        for iid in removed_ids:
            if iid not in ingredients or not ingredients[iid]["removable"]:
                raise CartError(f"Ingrediente inválido em {pname}.")

        sauce_ids = _id_list(raw.get("sauces"), "molhos")
        if sauce_ids and not product["allows_sauces"]:
            raise CartError(f"{pname} não leva molho.")
        for sid in sauce_ids:
            if sid not in sauces:
                raise CartError("Um dos molhos escolhidos não está disponível.")

        chosen = set(_id_list(raw.get("options"), "opções"))
        picked = []
        known = set()
        for grp in product_groups(product_id):
            ids_here = [o for o in grp["options"] if o["id"] in chosen]
            known.update(o["id"] for o in grp["options"])
            if len(ids_here) < grp["min"]:
                raise CartError(f"Falta escolher {grp['name'].lower()} em {pname}.")
            if len(ids_here) > grp["max"]:
                raise CartError(f"Em {pname}, escolha no máximo {grp['max']} em {grp['name'].lower()}.")
            picked += [{"group": grp["name"], "name": o["name"], "price_cents": o["price_cents"]} for o in ids_here]
        if chosen - known:
            raise CartError(f"Opção inválida em {pname}. Remova o item e adicione de novo.")

        notes = str(raw.get("notes") or "").strip()[:MAX_NOTE]
        unit = (product["price_cents"] + sum(sauces[s]["price_cents"] for s in sauce_ids)
                + sum(o["price_cents"] for o in picked))
        line_total = unit * qty
        subtotal += line_total
        lines.append({
            "product_id": product_id,
            "name": pname,
            "qty": qty,
            "unit_price_cents": unit,
            "line_total_cents": line_total,
            "removed": [ingredients[i]["name"] for i in removed_ids],
            "sauces": [{"name": sauces[s]["name"], "price_cents": sauces[s]["price_cents"]} for s in sauce_ids],
            "options": picked,
            "notes": notes,
        })
    return lines, subtotal
