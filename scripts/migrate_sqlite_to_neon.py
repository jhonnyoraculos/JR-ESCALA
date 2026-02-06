import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, Sequence

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


TABLE_ORDER = [
    "colaboradores",
    "caminhoes",
    "rotas_semanais",
    "rotas_suprimidas",
    "carregamentos",
    "oficinas",
    "folgas",
    "ferias",
    "escala_cd",
    "bloqueios",
    "ajustes_rotas",
]


def _chunked(rows: Sequence[tuple], size: int) -> Iterable[Sequence[tuple]]:
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def _get_env_url() -> str | None:
    return (
        os.environ.get("NEON_DATABASE_URL")
        or os.environ.get("JR_ESCALA_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
    )


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?;",
        (table,),
    )
    return cur.fetchone() is not None


def _fetch_table(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[tuple]]:
    cur = conn.execute(f"PRAGMA table_info({table});")
    cols = [row[1] for row in cur.fetchall()]
    if not cols:
        return [], []
    rows = conn.execute(f"SELECT {', '.join(cols)} FROM {table};").fetchall()
    return cols, rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrar dados SQLite para Neon/Postgres preservando IDs."
    )
    parser.add_argument(
        "--sqlite",
        required=True,
        help="Caminho para o arquivo SQLite (ex: web/jr_escala_web.db)",
    )
    parser.add_argument(
        "--neon-url",
        help="URL do Neon/Postgres. Se nao informado, usa variaveis de ambiente.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Apaga os dados do Neon antes de importar (TRUNCATE).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Tamanho do lote de insert.",
    )

    args = parser.parse_args()
    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        print(f"SQLite nao encontrado: {sqlite_path}")
        return 1

    neon_url = args.neon_url or _get_env_url()
    if not neon_url:
        print("Informe NEON_DATABASE_URL (ou JR_ESCALA_DATABASE_URL) ou use --neon-url.")
        return 1

    # Garantir que web.db carregue Postgres
    os.environ["NEON_DATABASE_URL"] = neon_url

    from web import db

    if not db.USE_POSTGRES:
        print("Falha ao configurar Postgres. Verifique a URL.")
        return 1
    if db.psycopg2 is None and getattr(db, "psycopg", None) is None:
        print("Driver Postgres nao instalado. Instale as dependencias antes de migrar.")
        return 1

    db.init_db()

    src = sqlite3.connect(sqlite_path)
    src.row_factory = None

    with db.get_connection() as dest:
        cur = dest.cursor()

        if args.replace:
            tables = ", ".join(TABLE_ORDER)
            cur.execute(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE;")
            dest.commit()

        for table in TABLE_ORDER:
            if not _table_exists(src, table):
                print(f"Ignorando tabela ausente: {table}")
                continue

            cols, rows = _fetch_table(src, table)
            if not rows:
                print(f"{table}: 0 registros")
                continue

            placeholders = ", ".join(["%s"] * len(cols))
            col_list = ", ".join(cols)
            insert_sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
            if not args.replace:
                insert_sql += " ON CONFLICT DO NOTHING"

            total = 0
            for batch in _chunked(rows, args.batch_size):
                cur.executemany(insert_sql, batch)
                total += len(batch)
            dest.commit()
            print(f"{table}: {total} inseridos")

        # Ajustar sequencias
        for table in TABLE_ORDER:
            cur.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table}), 1), true);"
            )
        dest.commit()

    src.close()
    print("Migracao concluida.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
