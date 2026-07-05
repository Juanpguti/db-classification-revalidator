"""Persistencia en SQLite con esquema normalizado (3FN).

users y databases guardan los datos; review_flags y manual_review_items
persisten los hallazgos que requieren atención humana. Las vistas
revalidation_report y pending_manual_review solo consultan (no almacenan).
"""

import json
import logging
import sqlite3

logger = logging.getLogger(__name__)

SCHEMA = """
PRAGMA foreign_keys = ON;

-- Las vistas se regeneran en cada arranque por si su definición cambió.
DROP VIEW IF EXISTS pending_manual_review;
DROP VIEW IF EXISTS revalidation_report;

CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    email      TEXT NOT NULL UNIQUE,
    state      TEXT,
    manager_id INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS databases (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    classification TEXT NOT NULL,
    owner_id       INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS review_flags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    database_id INTEGER NOT NULL REFERENCES databases(id),
    reason      TEXT NOT NULL,
    UNIQUE (database_id, reason)
);

-- Hallazgos sin fila en databases (ej: registro JSON sin nombre).
-- raw_payload conserva el registro original completo.
CREATE TABLE IF NOT EXISTS manual_review_items (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    record_reference TEXT NOT NULL,
    db_name          TEXT,
    classification   TEXT,
    owner_email      TEXT,
    reason           TEXT NOT NULL,
    raw_payload      TEXT NOT NULL,
    UNIQUE (record_reference, reason)
);

-- La información en su forma desnormalizada.
CREATE VIEW revalidation_report AS
SELECT
    d.name            AS db_name,
    owner.email       AS owner_email,
    manager.email     AS manager_email,
    d.classification  AS classification
FROM databases d
LEFT JOIN users owner   ON owner.id = d.owner_id
LEFT JOIN users manager ON manager.id = owner.manager_id;

-- Unifica review_flags + manual_review_items para gestión manual.
CREATE VIEW pending_manual_review AS
SELECT
    NULL             AS record_reference,
    d.name           AS db_name,
    d.classification AS classification,
    owner.email      AS owner_email,
    rf.reason        AS reason,
    NULL             AS raw_payload
FROM review_flags rf
JOIN databases d      ON d.id = rf.database_id
LEFT JOIN users owner ON owner.id = d.owner_id
UNION ALL
SELECT record_reference, db_name, classification, owner_email, reason, raw_payload
FROM manual_review_items;
"""


def get_connection(db_path):
    """Abre la conexión y crea el esquema si no existe."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_user(conn, email, state=None, manager_id=None):
    """Inserta el usuario si no existe y devuelve su id. Si existe,
    solo completa datos que lleguen nuevos (COALESCE)."""
    if not email:
        return None
    conn.execute(
        """
        INSERT INTO users (email, state, manager_id)
        VALUES (?, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
            state      = COALESCE(excluded.state, users.state),
            manager_id = COALESCE(excluded.manager_id, users.manager_id)
        """,
        (email, state, manager_id),
    )
    row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    return row["id"]


def insert_database(conn, name, classification, owner_id):
    """Inserta o actualiza una base clasificada (upsert por nombre)."""
    conn.execute(
        """
        INSERT INTO databases (name, classification, owner_id)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            classification = excluded.classification,
            owner_id       = COALESCE(excluded.owner_id, databases.owner_id)
        """,
        (name, classification, owner_id),
    )


def add_review_flag(conn, db_name, reason):
    """Marca una base para revisión manual. Idempotente por UNIQUE."""
    conn.execute(
        """
        INSERT INTO review_flags (database_id, reason)
        SELECT id, ? FROM databases WHERE name = ?
        ON CONFLICT(database_id, reason) DO NOTHING
        """,
        (reason, db_name),
    )
    logger.warning("Base '%s' marcada para revisión manual: %s.", db_name, reason)


def add_manual_review_item(conn, record, reason):
    """Persiste un hallazgo que no tiene fila en databases."""
    reference = "json_record_#%s" % record["source_position"]
    raw_payload = json.dumps(record["raw_payload"], ensure_ascii=False, sort_keys=True)
    conn.execute(
        """
        INSERT INTO manual_review_items (
            record_reference, db_name, classification, owner_email, reason, raw_payload
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(record_reference, reason) DO UPDATE SET
            db_name        = excluded.db_name,
            classification = excluded.classification,
            owner_email    = excluded.owner_email,
            raw_payload    = excluded.raw_payload
        """,
        (reference, record["db_name"], record["classification"],
         record["owner_email"], reason, raw_payload),
    )
    logger.warning("Registro '%s' marcado para revisión manual: %s.",
                   reference, reason)


def load_records(conn, db_records, users_by_email):
    """Cruza JSON + CSV y persiste todo. Devuelve las filas de
    revalidation_report para la etapa de notificación."""
    for record in db_records:
        db_name = record["db_name"]
        owner_email = record["owner_email"]
        owner_id = None
        flags = list(record.get("review_reasons", []))

        if record["classification"] == "unknown":
            flags.append("clasificación faltante en el JSON")

        if not owner_email:
            flags.append("el registro no tiene owner en el JSON")

        # Sin nombre no hay fila en databases: va a manual_review_items.
        if not db_name:
            for reason in flags:
                add_manual_review_item(conn, record, reason)
            continue

        if owner_email:
            csv_info = users_by_email.get(owner_email)
            manager_id = None
            state = None
            if csv_info:
                state = csv_info["state"]
                if csv_info["manager"]:
                    manager_id = upsert_user(conn, csv_info["manager"])
                else:
                    flags.append("el owner no tiene manager asignado en el CSV")
                if state == "inactive":
                    flags.append("el owner figura como inactivo en el CSV "
                                 "(posible base huérfana)")
            else:
                flags.append("el owner no aparece en el CSV: "
                             "no se conoce su manager")
            owner_id = upsert_user(conn, owner_email, state=state,
                                   manager_id=manager_id)

        insert_database(conn, db_name, record["classification"], owner_id)
        for reason in flags:
            add_review_flag(conn, db_name, reason)

    conn.commit()
    rows = conn.execute("SELECT * FROM revalidation_report").fetchall()
    logger.info("Base cargada: %d registros en revalidation_report.", len(rows))
    return rows
