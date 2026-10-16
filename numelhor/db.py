import os
import shutil
import sqlite3

from flask import current_app, g, has_request_context, request, session

WEEKDAYS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]

DEFAULT_SETTINGS = {
    "store_name": "NuMelhor Lanches",
    "store_whatsapp": "5548991527882",
    "store_instagram": "numelhor",
    "store_address": "Av. Luiz Lazzarin, 155 - 2º andar - Rio Maina",
    "store_city": "Criciúma - SC, 88817-000",
    "store_lat": "-28.676376",
    "store_lng": "-49.411353",
    # auto = segue o horário; aberto/fechado = o dono forçou pelo painel
    "open_mode": "auto",
    # Até quando vale o "Abrir agora"/"Fechar agora" (segundos desde 1970); vazio = sem prazo
    "open_until": "",
    # Horário por dia da semana (0 = segunda). Vazio = fechado. Pode passar da meia-noite (18:00-01:00).
    "hours_0": "",
    "hours_1": "18:00-23:30",
    "hours_2": "18:00-23:30",
    "hours_3": "18:00-23:30",
    "hours_4": "18:00-23:59",
    "hours_5": "18:00-23:59",
    "hours_6": "18:00-23:30",
    "pickup_eta": "20 a 30 min",
    "delivery_eta": "40 a 60 min",
    "delivery_base_cents": "500",
    "delivery_included_km": "2",
    "delivery_per_km_cents": "150",
    "delivery_max_km": "10",
    "pix_key": "",
    "pix_receiver_name": "NUMELHOR LANCHES",
    "pix_city": "CRICIUMA",
    # Mercado Pago cadastrado pelo painel (token e segredo ficam criptografados)
    "mp_mode": "teste",
    "mp_token_enc": "",  # nosec B105 - vazio por padrão; o valor real fica criptografado
    "mp_webhook_secret_enc": "",  # nosec B105
    "mp_account": "",
}

# Chaves que mexem com dinheiro: só mudam com senha (e 2FA) confirmados.
SENSITIVE_SETTINGS = {"pix_key", "pix_receiver_name", "pix_city", "store_whatsapp"}


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE"], timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        g.db = conn
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def settings():
    """Todas as configurações, com os valores padrão preenchidos."""
    if "settings" not in g:
        data = dict(DEFAULT_SETTINGS)
        for row in get_db().execute("SELECT key, value FROM settings"):
            if row["key"] in DEFAULT_SETTINGS:
                data[row["key"]] = row["value"]
        g.settings = data
    return g.settings


def set_settings(values):
    db = get_db()
    for key, value in values.items():
        if key not in DEFAULT_SETTINGS:
            raise KeyError(key)
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
    db.commit()
    g.pop("settings", None)


def client_ip():
    if not has_request_context():
        return "local"
    return request.remote_addr or "?"


def audit(event, detail="", actor=None):
    if actor is None:
        actor = (session.get("admin_user") if has_request_context() else None) or "-"
    db = get_db()
    db.execute(
        "INSERT INTO audit_log (ip, actor, event, detail) VALUES (?, ?, ?, ?)",
        (client_ip(), actor, event, detail[:500]),
    )
    db.commit()


def _migrate(db):
    """Acrescenta colunas novas em bancos criados por versões anteriores."""
    def columns(table):
        return {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}  # nosec B608 - tabela fixa
    if "featured" not in columns("products"):
        db.execute("ALTER TABLE products ADD COLUMN featured INTEGER NOT NULL DEFAULT 0")
    if "old_price_cents" not in columns("products"):
        db.execute("ALTER TABLE products ADD COLUMN old_price_cents INTEGER")
    for col in ("cancel_reason", "cancelled_at", "cancelled_by"):
        if col not in columns("orders"):
            db.execute(f"ALTER TABLE orders ADD COLUMN {col} TEXT")  # nosec B608 - nomes fixos
    if "options" not in columns("order_items"):
        db.execute("ALTER TABLE order_items ADD COLUMN options TEXT NOT NULL DEFAULT '[]'")
    old = db.execute("SELECT value FROM settings WHERE key = 'store_open'").fetchone()
    if old is not None:
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('open_mode', ?)",
                   ("auto" if old["value"] == "1" else "fechado",))
        db.execute("DELETE FROM settings WHERE key IN ('store_open', 'opening_hours')")


def init_db():
    db = get_db()
    with current_app.open_resource("schema.sql") as f:
        db.executescript(f.read().decode("utf-8"))
    _migrate(db)
    if db.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        _seed(db)
    db.commit()


def _seed(db):
    load_menu(db)


# ---------- Cardápio oficial da NuMelhor (conforme os cardápios impressos da loja) ----------
# Grupos de opções: (nome, mínimo, máximo, [(opção, preço em centavos)])
ADIC_LANCHE = ("Adicionais", 0, 13, [
    ("Ovo (egg)", 200), ("Picles", 300), ("Cebola roxa", 300), ("Cebola caramelizada", 400), ("Salada", 400),
    ("Reboco", 400), ("Calabresa", 400), ("Bacon", 500), ("Cheddar", 500), ("Carne de burguer", 500),
    ("Galinha", 500), ("Coração", 800), ("Fritas", 800)])
ADIC_PORCAO = ("Adicionais", 0, 5, [("Queijo", 600), ("Calabresa", 700), ("Bacon", 800), ("Galinha", 800),
                                    ("Coração", 1000)])
ABERTO = ("Montagem", 0, 1, [("Aberto (no prato)", 500)])
REFRIS = [("Coca-Cola", 0), ("Guaraná", 0), ("Laranja", 0)]
SABOR_REFRI = ("Sabor", 1, 1, REFRIS)
SABOR_SUCO = ("Sabor", 1, 1, [("Uva", 0), ("Laranja", 0)])


def _xis(main=None, carne=True, presunto=True):
    ings = [("Pão", 0), ("Maionese caseira", 1)]
    if carne:
        ings.append(("Carne", 0))
    if presunto:
        ings.append(("Presunto", 1))
    ings += [("Queijo", 1), ("Tomate", 1), ("Alface", 1), ("Milho", 1), ("Ervilha", 1), ("Vinagrete", 1)]
    if main:
        ings.append((main, 0))
    return ings


def _xis_desc(carne, extra):
    return f"Pão, maionese caseira, {carne}queijo, tomate, alface, milho, ervilha, vinagrete{extra}."


def _pasteis(names, price, featured=()):
    return [(f"Pastel de {n}", "", price if isinstance(price, int) else price[n], "pastel", 1,
             1 if n in featured else 0, [], []) for n in names]


# (categoria, ícone, [(nome, descrição, preço, imagem, aceita molho, destaque, ingredientes, grupos)])
MENU = [
    ("Xis e Mistos", "xis", [
        ("X-Burguer", "Pão, maionese caseira, duas carnes e queijo.", 1800, "xis", 1, 0,
         [("Pão", 0), ("Maionese caseira", 1), ("Duas carnes", 0), ("Queijo", 1)], [ADIC_LANCHE, ABERTO]),
        ("X Salada", _xis_desc("carne, presunto, ", ""), 2000, "xis", 1, 1, _xis(), [ADIC_LANCHE, ABERTO]),
        ("X Egg", _xis_desc("carne, presunto, ", " e ovo"), 2200, "xis", 1, 0, _xis("Ovo"), [ADIC_LANCHE, ABERTO]),
        ("X Acebolado", _xis_desc("carne, presunto, ", " e cebola"), 2300, "xis", 1, 0,
         _xis("Cebola"), [ADIC_LANCHE, ABERTO]),
        ("X Calabresa", _xis_desc("carne, ", " e calabresa"), 2400, "xis", 1, 0,
         _xis("Calabresa", presunto=False), [ADIC_LANCHE, ABERTO]),
        ("X Galinha", _xis_desc("", " e galinha"), 2500, "xis", 1, 0,
         _xis("Galinha", carne=False, presunto=False), [ADIC_LANCHE, ABERTO]),
        ("X Bacon", _xis_desc("carne, ", " e bacon"), 2600, "xis", 1, 0,
         _xis("Bacon", presunto=False), [ADIC_LANCHE, ABERTO]),
        ("X Coração", _xis_desc("carne, ", " e coração"), 2800, "xis", 1, 0,
         _xis("Coração", presunto=False), [ADIC_LANCHE, ABERTO]),
        ("Misto Quente", "Pão, presunto, queijo e maionese caseira.", 1200, "xis", 1, 0,
         [("Pão", 0), ("Presunto", 1), ("Queijo", 1), ("Maionese caseira", 1)], [ADIC_LANCHE]),
        ("Misto com Carne", "Pão, carne, presunto, queijo e maionese caseira.", 1600, "xis", 1, 0,
         [("Pão", 0), ("Carne", 0), ("Presunto", 1), ("Queijo", 1), ("Maionese caseira", 1)], [ADIC_LANCHE]),
    ]),
    ("Hambúrgueres", "burger", [
        ("Nuclássico", "Pão brioche, maionese caseira, carne, queijo e barbecue.", 1200, "burger", 1, 0,
         [("Pão brioche", 0), ("Maionese caseira", 1), ("Carne", 0), ("Queijo", 1), ("Barbecue", 1)],
         [ADIC_LANCHE]),
        ("Nubrabo", "Pão brioche, maionese caseira, carne, queijo, barbecue e cheddar. Acompanha fritas.",
         2000, "burger", 1, 1,
         [("Pão brioche", 0), ("Maionese caseira", 1), ("Carne", 0), ("Queijo", 1), ("Barbecue", 1),
          ("Cheddar", 1), ("Fritas (acompanha)", 1)], [ADIC_LANCHE]),
        ("Nusupremo", "Pão brioche, maionese caseira, carne, queijo, ovo, bacon, barbecue e salada. "
                      "Acompanha fritas.", 2800, "burger", 1, 0,
         [("Pão brioche", 0), ("Maionese caseira", 1), ("Carne", 0), ("Queijo", 1), ("Ovo", 1), ("Bacon", 1),
          ("Barbecue", 1), ("Salada", 1), ("Fritas (acompanha)", 1)], [ADIC_LANCHE]),
    ]),
    ("Pastéis", "pastel", _pasteis(
        ["Carne", "Carne e Ovos", "Queijo", "Presunto e Queijo", "Carne e Queijo", "Palmito", "Calabresa",
         "Frango", "Frango com Queijo", "Brócolis com Queijo", "Pizza", "Bacon e Queijo"],
        {"Carne": 700, "Carne e Ovos": 700, **{n: 800 for n in [
            "Queijo", "Presunto e Queijo", "Carne e Queijo", "Palmito", "Calabresa", "Frango", "Frango com Queijo",
            "Brócolis com Queijo", "Pizza", "Bacon e Queijo"]}})),
    ("Pastéis Especiais", "pastel", _pasteis(
        ["Frango com Catupiry", "Frango com Cheddar", "Bacon com Catupiry", "Bacon com Cheddar",
         "Calabresa com Catupiry", "Calabresa com Cheddar"], 1000, featured=("Frango com Catupiry",))),
    ("Pastéis Doces", "doce", [
        ("Pastel de Chocolate ao Leite", "", 700, "pastel", 0, 0, [], []),
        ("Pastel de Prestígio", "", 800, "pastel", 0, 0, [], []),
        ("Pastel de Creme de Ninho", "", 800, "pastel", 0, 0, [], []),
        ("Pastel de Queijo com Goiabada", "", 900, "pastel", 0, 0, [], []),
        ("Pastel Sensação", "", 1000, "pastel", 0, 0, [], []),
    ]),
    ("Porções e Tábuas", "batata", [
        ("Meia Fritas", "Porção individual de batata frita.", 1500, "fritas", 1, 0, [], [ADIC_PORCAO]),
        ("Fritas (2 pessoas)", "Porção de batata frita para duas pessoas.", 2500, "fritas", 1, 0, [],
         [ADIC_PORCAO]),
        ("Fritas com Bacon e Cheddar", "Batata frita coberta com bacon e cheddar.", 3500, "fritas", 1, 0,
         [("Bacon", 1), ("Cheddar", 1)], [ADIC_PORCAO]),
        ("Tábua para Dois", "Pão (com queijo, maionese e vinagrete), carne, coração, frango, calabresa e fritas.",
         5000, "combo", 1, 0,
         [("Pão", 0), ("Queijo", 1), ("Maionese", 1), ("Vinagrete", 1), ("Carne", 0), ("Coração", 1),
          ("Frango", 1), ("Calabresa", 1), ("Fritas", 0)], [ADIC_PORCAO]),
    ]),
    ("Combos e Especiais", "combo", [
        ("Combo Kids", "Pão, maionese caseira, carne, presunto e queijo + fritas + Refri Jr. (200ml) "
                       "ou suco de uva.", 1800, "combo", 1, 0,
         [("Pão", 0), ("Maionese caseira", 1), ("Carne", 0), ("Presunto", 1), ("Queijo", 1)],
         [("Bebida", 1, 1, [("Refri Jr. 200ml", 0), ("Suco de uva", 0)])]),
        ("Combo Casal", "Dois Nuclássicos partidos, fritas com bacon e cheddar + Refri 600ml.", 5000, "combo", 1, 1,
         [], [("Sabor do refri", 1, 1, REFRIS)]),
        ("X-Doce", "Pão com chocolate ao leite ou leite ninho.", 1600, None, 0, 0, [],
         [("Recheio", 1, 1, [("Chocolate ao leite", 0), ("Leite ninho", 0)])]),
    ]),
    ("Bebidas", "refri", [
        ("Refri 200ml", "", 400, "refri", 0, 0, [], [SABOR_REFRI]),
        ("Refri Lata", "", 600, "refri", 0, 0, [], [SABOR_REFRI]),
        ("Refri 600ml", "", 900, "refri", 0, 0, [], [SABOR_REFRI]),
        ("Refri 2 Litros", "", 1500, "refri", 0, 0, [], [SABOR_REFRI]),
        ("Água", "", 400, None, 0, 0, [], []),
        ("Suco", "", 500, None, 0, 0, [], [SABOR_SUCO]),
        ("Cerveja Lata", "Venda proibida para menores de 18 anos.", 600, None, 0, 0, [], []),
    ]),
]
SAUCES = [("Ketchup", 0), ("Mostarda", 0), ("Maionese", 0), ("Barbecue", 0)]


def load_menu(db, replace=False):
    """Cadastra o cardápio. Com replace=True apaga o cardápio atual antes (os pedidos antigos ficam)."""
    upload_dir = current_app.config["UPLOAD_DIR"]
    seed_dir = os.path.join(current_app.root_path, "seed_img")
    for name in os.listdir(seed_dir):
        shutil.copy(os.path.join(seed_dir, name), os.path.join(upload_dir, "seed-" + name))
    if replace:
        for table in ("options", "option_groups", "ingredients", "products", "categories", "sauces"):
            db.execute(f"DELETE FROM {table}")  # nosec B608 - nomes de tabela fixos, nada vem do usuário

    sort = 0
    for ci, (cat, icon, items) in enumerate(MENU):
        cat_id = db.execute("INSERT INTO categories (name, icon, sort) VALUES (?, ?, ?)", (cat, icon, ci)).lastrowid
        for name, desc, price, img, sauces, featured, ingredients, groups in items:
            sort += 1
            pid = db.execute(
                "INSERT INTO products (category_id, name, description, price_cents, image, allows_sauces, "
                "featured, sort) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (cat_id, name, desc, price, f"seed-{img}.webp" if img else None, sauces, featured, sort),
            ).lastrowid
            for i, (ing, removable) in enumerate(ingredients):
                db.execute("INSERT INTO ingredients (product_id, name, removable, sort) VALUES (?, ?, ?, ?)",
                           (pid, ing, removable, i))
            for gi, (gname, gmin, gmax, opts) in enumerate(groups):
                gid = db.execute("INSERT INTO option_groups (product_id, name, min_choices, max_choices, sort) "
                                 "VALUES (?, ?, ?, ?, ?)", (pid, gname, gmin, gmax, gi)).lastrowid
                for oi, (oname, oprice) in enumerate(opts):
                    db.execute("INSERT INTO options (group_id, name, price_cents, sort) VALUES (?, ?, ?, ?)",
                               (gid, oname, oprice, oi))
    for i, (name, price) in enumerate(SAUCES):
        db.execute("INSERT INTO sauces (name, price_cents, sort) VALUES (?, ?, ?)", (name, price, i))


def init_app(app):
    app.teardown_appcontext(close_db)
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    with app.app_context():
        init_db()
