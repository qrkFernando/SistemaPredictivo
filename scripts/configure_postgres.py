"""Connect to an existing PostgreSQL database and save local configuration safely."""
import argparse
import getpass
import os
import sys
from pathlib import Path
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    import psycopg
    from dotenv import set_key,load_dotenv
    parser=argparse.ArgumentParser(description='Configurar PostgreSQL sin imprimir la contraseña.')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=5432)
    parser.add_argument('--database',default='atc_db')
    parser.add_argument('--user',default='postgres')
    parser.add_argument('--schema',default='atc')
    args=parser.parse_args()
    secret=getpass.getpass('Contraseña de PostgreSQL: ')
    try:
        with psycopg.connect(host=args.host,port=args.port,dbname=args.database,user=args.user,password=secret,connect_timeout=5) as connection:
            database=connection.execute('SELECT current_database()').fetchone()[0]
    except psycopg.Error:
        print('No se pudo conectar. Revisa host, puerto, base, usuario y contraseña.',file=sys.stderr)
        return 1
    destination=ROOT/'.env'
    fd=os.open(destination,os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
    os.close(fd)
    os.chmod(destination,0o600)
    url=f'postgresql://{quote(args.user,safe="")}:{quote(secret,safe="")}@{args.host}:{args.port}/{quote(args.database,safe="")}'
    set_key(str(destination),'ATC_DATABASE_URL',url)
    set_key(str(destination),'ATC_DATABASE_SCHEMA',args.schema)
    os.chmod(destination,0o600)
    load_dotenv(destination,override=True)
    from backend.app.db import init_db,connect
    init_db()
    with connect() as db:
        count=db.execute('SELECT count(*) FROM sales').fetchone()[0]
    print(f'Conectado: PostgreSQL · {database} · {args.host}:{args.port} · esquema {args.schema}.')
    print(f'Ventas almacenadas: {count}. No se insertaron ventas, inventarios ni precios simulados.')
    print('Configuración guardada en .env (permisos 600; ignorado por Git).')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
