"""Editable starting recipes, explicitly approximate and expressed per serving.

These values are planning assumptions requested by the user, not measured yields
or the restaurant's official formulation. Costs and inventory are never seeded.
"""
INGREDIENTS = [
    ('Harina de trigo','kg','Preparar'),('Huevo','und','Preparar'),
    ('Leche','L','Preparar'),('Ajo','kg','Preparar'),('Sal','kg','Preparar'),
    ('Pimienta','kg','Preparar'),('Paprika','kg','Preparar'),
    ('Queso','kg','Preparar'),('Lechuga','kg','Preparar'),
    ('Tomate','kg','Preparar'),('Cebolla','kg','Preparar'),
    ('Mayonesa','kg','Preparar'),('Kétchup','kg','Preparar'),
    ('Mostaza','kg','Preparar'),
]

# Base storage units: kg, L, und. The UI displays grams and millilitres.
RECIPES = {
    'Pollo broaster': [
        ('Pollo',.300),('Papa',.180),('Harina de trigo',.040),
        ('Huevo',.25),('Leche',.030),('Ajo',.003),('Sal',.003),
        ('Pimienta',.0005),('Paprika',.002),('Aceite',.025),
    ],
    'Hamburguesa': [
        ('Pan de hamburguesa',1),('Carne de hamburguesa',.120),
        ('Queso',.020),('Lechuga',.015),('Tomate',.030),('Cebolla',.015),
        ('Mayonesa',.015),('Kétchup',.010),('Mostaza',.005),
        ('Papa',.100),('Aceite',.015),('Sal',.001),('Pimienta',.0003),
    ],
    'Alitas BBQ': [
        ('Alitas',.250),('Salsa BBQ',.060),('Papa',.120),
        ('Ajo',.003),('Sal',.002),('Pimienta',.0005),('Aceite',.020),
    ],
    'Salchipapa': [
        ('Papa',.300),('Salchicha',.130),('Aceite',.025),('Sal',.002),
        ('Mayonesa',.025),('Kétchup',.025),('Mostaza',.010),
    ],
}


def install_default_recipes(db):
    """One-time, non-overwriting migration; later removals are preserved."""
    db.executemany('INSERT INTO ingredients(name,unit,cost,action) VALUES(?,?,NULL,?) ON CONFLICT(name) DO NOTHING',INGREDIENTS)
    if db.execute("SELECT value FROM meta WHERE key='default_recipes_v1'").fetchone():
        return
    rows=[(product,ingredient,amount,1) for product,ingredients in RECIPES.items() for ingredient,amount in ingredients]
    db.executemany('INSERT INTO recipes(producto,insumo,amount,approximate) VALUES(?,?,?,?) ON CONFLICT(producto,insumo) DO NOTHING',rows)
    db.execute("INSERT INTO meta(key,value) VALUES('default_recipes_v1',1) ON CONFLICT(key) DO NOTHING")


if __name__=='__main__':
    from .db import init_db,connect
    init_db()
    with connect() as db:
        for row in db.execute('SELECT producto,count(*) AS ingredients FROM recipes GROUP BY producto ORDER BY producto'):
            print(f"{row['producto']}: {row['ingredients']} ingredientes")
