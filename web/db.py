from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

try:
    import psycopg2
    import psycopg2.extras
except Exception:  # psycopg2 pode nao estar instalado localmente
    psycopg2 = None

BASE_DIR = Path(__file__).resolve().parent

DB_PATH = Path(os.environ.get("JR_ESCALA_DB_PATH", BASE_DIR / "jr_escala_web.db"))
UPLOAD_DIR = Path(os.environ.get("JR_ESCALA_UPLOAD_DIR", BASE_DIR / "uploads"))
REPORTS_DIR = Path(os.environ.get("JR_ESCALA_REPORTS_DIR", BASE_DIR / "reports"))
LOGO_PATH = Path(os.environ.get("JR_ESCALA_LOGO_PATH", BASE_DIR / "static" / "img" / "logo-jr.png"))
FONT_PATH = Path(os.environ.get("JR_ESCALA_FONT_PATH", BASE_DIR / "static" / "fonts" / "Sora.ttf"))

DATABASE_URL = (
    os.environ.get("JR_ESCALA_DATABASE_URL")
    or os.environ.get("NEON_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
)
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES and psycopg2:
    DBError = psycopg2.Error
else:
    DBError = sqlite3.Error


def ensure_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _translate_query(query: str) -> str:
    if not USE_POSTGRES:
        return query
    texto = query
    if re.search(r"\bINSERT\s+OR\s+IGNORE\b", texto, flags=re.IGNORECASE):
        texto = re.sub(r"\bINSERT\s+OR\s+IGNORE\b", "INSERT", texto, flags=re.IGNORECASE)
        texto = texto.rstrip().rstrip(";")
        texto = f"{texto} ON CONFLICT DO NOTHING;"
    texto = re.sub(r"COLLATE\s+NOCASE", "", texto, flags=re.IGNORECASE)
    texto = texto.replace("?", "%s")
    return texto


if psycopg2:
    class QmarkCursor(psycopg2.extensions.cursor):
        def execute(self, query, vars=None):
            return super().execute(_translate_query(query), vars)

        def executemany(self, query, vars_list):
            return super().executemany(_translate_query(query), vars_list)


    class QmarkDictCursor(psycopg2.extras.RealDictCursor):
        def execute(self, query, vars=None):
            return super().execute(_translate_query(query), vars)

        def executemany(self, query, vars_list):
            return super().executemany(_translate_query(query), vars_list)


def get_connection(dict_rows: bool = False):
    ensure_dirs()
    if USE_POSTGRES:
        if psycopg2 is None:
            raise RuntimeError("psycopg2 nao esta instalado para conexao PostgreSQL.")
        cursor_factory = QmarkDictCursor if dict_rows else QmarkCursor
        sslmode = os.environ.get("JR_ESCALA_DB_SSLMODE", "require")
        conn = psycopg2.connect(DATABASE_URL, sslmode=sslmode, cursor_factory=cursor_factory)
        return conn
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    if dict_rows:
        conn.row_factory = sqlite3.Row
    return conn


def insert_and_get_id(cur, query: str, params: tuple) -> int | None:
    if USE_POSTGRES:
        texto = _translate_query(query).rstrip().rstrip(";")
        if "RETURNING" not in texto.upper():
            texto = f"{texto} RETURNING id"
        cur.execute(texto, params)
        row = cur.fetchone()
        if isinstance(row, dict):
            return row.get("id")
        return row[0] if row else None
    cur.execute(query, params)
    return cur.lastrowid


def init_db() -> None:
    ensure_dirs()
    if USE_POSTGRES:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS colaboradores (
                    id SERIAL PRIMARY KEY,
                    nome TEXT NOT NULL,
                    funcao TEXT NOT NULL,
                    observacao TEXT DEFAULT '',
                    foto TEXT DEFAULT '',
                    ativo INTEGER NOT NULL DEFAULT 1
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS folgas (
                    id SERIAL PRIMARY KEY,
                    data TEXT NOT NULL,
                    data_fim TEXT,
                    data_saida TEXT,
                    colaborador_id INTEGER NOT NULL REFERENCES colaboradores(id),
                    observacao_padrao TEXT,
                    observacao_extra TEXT,
                    observacao_cor TEXT,
                    UNIQUE(data, colaborador_id)
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ferias (
                    id SERIAL PRIMARY KEY,
                    colaborador_id INTEGER NOT NULL REFERENCES colaboradores(id),
                    data_inicio TEXT NOT NULL,
                    data_fim TEXT NOT NULL,
                    observacao TEXT
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS carregamentos (
                    id SERIAL PRIMARY KEY,
                    data TEXT NOT NULL,
                    data_saida TEXT,
                    rota TEXT NOT NULL,
                    placa TEXT,
                    motorista_id INTEGER REFERENCES colaboradores(id),
                    ajudante_id INTEGER REFERENCES colaboradores(id),
                    observacao TEXT,
                    observacao_extra TEXT,
                    observacao_cor TEXT,
                    revisado INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(data, rota, placa)
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS oficinas (
                    id SERIAL PRIMARY KEY,
                    data TEXT NOT NULL,
                    motorista_id INTEGER REFERENCES colaboradores(id),
                    placa TEXT NOT NULL,
                    observacao TEXT,
                    observacao_extra TEXT,
                    data_saida TEXT,
                    observacao_cor TEXT,
                    UNIQUE(data, placa)
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS caminhoes (
                    id SERIAL PRIMARY KEY,
                    placa TEXT UNIQUE NOT NULL,
                    modelo TEXT,
                    observacao TEXT,
                    ativo INTEGER NOT NULL DEFAULT 1
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS bloqueios (
                    id SERIAL PRIMARY KEY,
                    colaborador_id INTEGER NOT NULL REFERENCES colaboradores(id),
                    data_inicio TEXT NOT NULL,
                    data_fim TEXT NOT NULL,
                    motivo TEXT,
                    carregamento_id INTEGER
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS rotas_semanais (
                    id SERIAL PRIMARY KEY,
                    dia_semana TEXT NOT NULL,
                    rota TEXT NOT NULL,
                    destino TEXT,
                    observacao TEXT
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS rotas_suprimidas (
                    id SERIAL PRIMARY KEY,
                    data TEXT NOT NULL,
                    rota TEXT NOT NULL,
                    UNIQUE(data, rota)
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS escala_cd (
                    id SERIAL PRIMARY KEY,
                    data TEXT NOT NULL,
                    motorista_id INTEGER REFERENCES colaboradores(id),
                    ajudante_id INTEGER REFERENCES colaboradores(id),
                    observacao TEXT
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ajustes_rotas (
                    id SERIAL PRIMARY KEY,
                    carregamento_id INTEGER NOT NULL REFERENCES carregamentos(id),
                    data_ajuste TEXT NOT NULL,
                    duracao_anterior INTEGER NOT NULL,
                    duracao_nova INTEGER NOT NULL,
                    observacao_ajuste TEXT
                );
                """
            )
            cur.execute(
                "ALTER TABLE carregamentos ADD COLUMN IF NOT EXISTS revisado INTEGER NOT NULL DEFAULT 0;"
            )
            cur.execute("ALTER TABLE folgas ADD COLUMN IF NOT EXISTS data_saida TEXT;")
            conn.commit()
        return

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS colaboradores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                funcao TEXT NOT NULL,
                observacao TEXT DEFAULT '',
                foto TEXT DEFAULT '',
                ativo INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS folgas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL,
                data_fim TEXT,
                data_saida TEXT,
                colaborador_id INTEGER NOT NULL,
                observacao_padrao TEXT,
                observacao_extra TEXT,
                observacao_cor TEXT,
                FOREIGN KEY(colaborador_id) REFERENCES colaboradores(id),
                UNIQUE(data, colaborador_id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ferias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                colaborador_id INTEGER NOT NULL,
                data_inicio TEXT NOT NULL,
                data_fim TEXT NOT NULL,
                observacao TEXT,
                FOREIGN KEY(colaborador_id) REFERENCES colaboradores(id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS carregamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL,
                data_saida TEXT,
                rota TEXT NOT NULL,
                placa TEXT,
                motorista_id INTEGER,
                ajudante_id INTEGER,
                observacao TEXT,
                observacao_extra TEXT,
                observacao_cor TEXT,
                revisado INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(motorista_id) REFERENCES colaboradores(id),
                FOREIGN KEY(ajudante_id) REFERENCES colaboradores(id),
                UNIQUE(data, rota, placa)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS oficinas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL,
                motorista_id INTEGER,
                placa TEXT NOT NULL,
                observacao TEXT,
                observacao_extra TEXT,
                data_saida TEXT,
                observacao_cor TEXT,
                FOREIGN KEY(motorista_id) REFERENCES colaboradores(id),
                UNIQUE(data, placa)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS caminhoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                placa TEXT UNIQUE NOT NULL,
                modelo TEXT,
                observacao TEXT,
                ativo INTEGER NOT NULL DEFAULT 1
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bloqueios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                colaborador_id INTEGER NOT NULL,
                data_inicio TEXT NOT NULL,
                data_fim TEXT NOT NULL,
                motivo TEXT,
                carregamento_id INTEGER,
                FOREIGN KEY(colaborador_id) REFERENCES colaboradores(id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS rotas_semanais (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dia_semana TEXT NOT NULL,
                rota TEXT NOT NULL,
                destino TEXT,
                observacao TEXT
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS rotas_suprimidas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL,
                rota TEXT NOT NULL,
                UNIQUE(data, rota)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS escala_cd (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL,
                motorista_id INTEGER,
                ajudante_id INTEGER,
                observacao TEXT,
                FOREIGN KEY(motorista_id) REFERENCES colaboradores(id),
                FOREIGN KEY(ajudante_id) REFERENCES colaboradores(id)
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ajustes_rotas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                carregamento_id INTEGER NOT NULL,
                data_ajuste TEXT NOT NULL,
                duracao_anterior INTEGER NOT NULL,
                duracao_nova INTEGER NOT NULL,
                observacao_ajuste TEXT,
                FOREIGN KEY(carregamento_id) REFERENCES carregamentos(id)
            );
            """
        )
        cur.execute("PRAGMA table_info(carregamentos);")
        colunas_carregamentos = {row[1] for row in cur.fetchall()}
        if "revisado" not in colunas_carregamentos:
            cur.execute("ALTER TABLE carregamentos ADD COLUMN revisado INTEGER NOT NULL DEFAULT 0;")
        cur.execute("PRAGMA table_info(folgas);")
        colunas_folgas = {row[1] for row in cur.fetchall()}
        if "data_saida" not in colunas_folgas:
            cur.execute("ALTER TABLE folgas ADD COLUMN data_saida TEXT;")
        conn.commit()
