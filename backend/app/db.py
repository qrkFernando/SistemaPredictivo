"""Transactional persistence for PostgreSQL and isolated SQLite tests."""
import os
import re
import sqlite3
import hashlib
import secrets
from pathlib import Path
from urllib.parse import urlparse, unquote
import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env', override=False)
DATABASE_URL = os.environ.get('ATC_DATABASE_URL', '')
DB_PATH = Path(os.environ.get('ATC_DATABASE', str(ROOT / 'data' / 'atc_operativo.db')))
SCHEMA = os.environ.get('ATC_DATABASE_SCHEMA', 'atc')
DEMO_DATA = False  # Only patched to True by tests; operational startup never seeds sales.
SEDES = ['Paseo la Breña', 'San Carlos', 'Giráldez']
PRODUCTS = ['Pollo broaster', 'Hamburguesa', 'Alitas BBQ', 'Salchipapa']
CATALOG = [('Pollo','kg','Marinar'),('Papa','kg','Prefreír'),('Pan de hamburguesa','und','Preparar'),('Carne de hamburguesa','kg','Preparar'),('Alitas','kg','Marinar'),('Salsa BBQ','kg','Preparar'),('Salchicha','kg','Preparar'),('Aceite','L','Preparar')]


class Record(dict):
    """Name/index lookup compatible with the repository's sqlite.Row queries."""
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def pg_row(cursor):
    names = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: Record(zip(names, values))


class Database:
    """Small DB-API adapter; SQL is application-owned and always parameterized."""
    def __init__(self):
        self.postgres = bool(DATABASE_URL)
        if self.postgres:
            import psycopg
            if not re.fullmatch(r'[a-z_][a-z0-9_]*', SCHEMA):
                raise ValueError('ATC_DATABASE_SCHEMA debe ser un identificador SQL simple.')
            self.raw = psycopg.connect(DATABASE_URL, row_factory=pg_row, connect_timeout=5, application_name='American Texas Chicken')
            self.raw.execute(f'SET search_path TO "{SCHEMA}", pg_catalog')
        else:
            self.raw = sqlite3.connect(DB_PATH, timeout=30)
            self.raw.row_factory = sqlite3.Row
            self.raw.execute('PRAGMA foreign_keys=ON')

    def execute(self, sql, parameters=()):
        return self.raw.execute(sql.replace('?', '%s') if self.postgres else sql, parameters)

    def executemany(self, sql, parameters):
        if self.postgres:
            cursor = self.raw.cursor()
            cursor.executemany(sql.replace('?', '%s'), parameters)
            return cursor
        return self.raw.executemany(sql, parameters)

    def executescript(self, script):
        # DDL only, under one transaction (including schema initialization).
        for statement in script.split(';'):
            if statement.strip():
                self.execute(statement)

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        try:
            self.raw.rollback() if kind else self.raw.commit()
        finally:
            self.raw.close()


def connect():
    return Database()


def database_info():
    if not DATABASE_URL:
        return {'engine':'SQLite','database':DB_PATH.name,'schema':'main','host':'local','port':None}
    parsed = urlparse(DATABASE_URL)
    return {'engine':'PostgreSQL','database':unquote(parsed.path.lstrip('/')),'schema':SCHEMA,'host':parsed.hostname,'port':parsed.port or 5432}


def database_identity():
    source=(DATABASE_URL+'|'+SCHEMA) if DATABASE_URL else str(DB_PATH.resolve())
    return hashlib.sha256(source.encode()).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 260000).hex()


def init_db():
    if not DATABASE_URL:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema = '''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT, password TEXT, role TEXT, sede TEXT);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), expires REAL);
        CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, name TEXT, owner INTEGER REFERENCES users(id), created TEXT DEFAULT CURRENT_TIMESTAMP, row_count INTEGER, quality REAL, demo INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS branches(name TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY, file_id TEXT REFERENCES files(id), fecha TEXT, sede TEXT, producto TEXT, cantidad REAL CHECK(cantidad>=0), turno TEXT, hora INTEGER, temperatura REAL, lluvia REAL, evento REAL DEFAULT 0, UNIQUE(fecha,sede,producto,turno));
        CREATE TABLE IF NOT EXISTS ingredients(name TEXT PRIMARY KEY, unit TEXT, cost REAL CHECK(cost>=0), action TEXT);
        CREATE TABLE IF NOT EXISTS recipes(producto TEXT, insumo TEXT REFERENCES ingredients(name), amount REAL CHECK(amount>0), PRIMARY KEY(producto,insumo));
        CREATE TABLE IF NOT EXISTS inventory(sede TEXT, insumo TEXT REFERENCES ingredients(name), amount REAL CHECK(amount>=0), PRIMARY KEY(sede,insumo));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, name TEXT, fecha TEXT, sede TEXT, impact REAL);
        CREATE TABLE IF NOT EXISTS forecasts(cache_key TEXT PRIMARY KEY, payload TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value INTEGER);
        INSERT INTO meta VALUES('revision',1) ON CONFLICT(key) DO NOTHING;
        INSERT INTO meta VALUES('data_revision',1) ON CONFLICT(key) DO NOTHING;
    '''
    with connect() as db:
        if db.postgres:
            db.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
            schema = schema.replace('id INTEGER PRIMARY KEY','id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY').replace('REAL','DOUBLE PRECISION').replace('created TEXT DEFAULT CURRENT_TIMESTAMP','created TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP')
        db.executescript(schema)
        # Preserve existing recipe quantities and label only newly added guides.
        if db.postgres:
            db.execute('ALTER TABLE recipes ADD COLUMN IF NOT EXISTS approximate INTEGER NOT NULL DEFAULT 0')
        elif 'approximate' not in [row['name'] for row in db.execute('PRAGMA table_info(recipes)')]:
            db.execute('ALTER TABLE recipes ADD COLUMN approximate INTEGER NOT NULL DEFAULT 0')
        if not db.execute('SELECT count(*) FROM users').fetchone()[0]:
            accounts = [('admin@americantexas.pe','Administrador ATC',os.environ.get('ATC_ADMIN_PASSWORD','Texas2026!'),'admin',None),('sancarlos@americantexas.pe','Encargado San Carlos',os.environ.get('ATC_MANAGER_PASSWORD','Sede2026!'),'encargado','San Carlos')]
            db.executemany('INSERT INTO users(email,name,password,role,sede) VALUES(?,?,?,?,?)',[(email,name,password_hash(password),role,sede) for email,name,password,role,sede in accounts])
        db.executemany('INSERT INTO branches(name) VALUES(?) ON CONFLICT(name) DO NOTHING',[(s,) for s in SEDES])
        db.executemany('INSERT INTO ingredients(name,unit,cost,action) VALUES(?,?,NULL,?) ON CONFLICT(name) DO NOTHING',CATALOG)
        if not DEMO_DATA:
            from .recipe_catalog import install_default_recipes
            install_default_recipes(db)
    if DEMO_DATA:
        from .demo import seed_demo_data
        seed_demo_data()


def sales_frame(user):
    with connect() as db:
        cursor = db.execute('SELECT * FROM sales ORDER BY fecha') if user['role']=='admin' else db.execute('SELECT * FROM sales WHERE sede=? ORDER BY fecha',(user['sede'],))
        columns = [column[0] for column in cursor.description]
        return pd.DataFrame([dict(row) for row in cursor],columns=columns)


def bump_revision(db,data_changed=True):
    db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
    if data_changed:
        db.execute("UPDATE meta SET value=value+1 WHERE key='data_revision'")
    db.execute('DELETE FROM forecasts')
