"""Punto de entrada: lee JSON + CSV, persiste en SQLite y notifica
a los managers las bases 'high' (o con clasificación no reconocida,
por política fail-safe)."""

import logging
import os
import sys

import database
import mailer
import readers

DATA_DIR = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "revalidation.db"))

JSON_PATH = os.path.join(DATA_DIR, "databases.json")
CSV_PATH = os.path.join(DATA_DIR, "users.csv")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    logger = logging.getLogger("main")
    logger.info("=== Reválida anual de clasificación de bases de datos ===")

    # 1. Lectura y validación de los archivos de entrada.
    try:
        db_records = readers.read_databases_json(JSON_PATH)
        users_by_email = readers.read_users_csv(CSV_PATH)
    except (OSError, ValueError) as exc:
        logger.error("No se pudieron leer los archivos de entrada: %s", exc)
        return 1

    # 2. Persistencia (siempre antes de notificar: si el SMTP falla,
    #    los datos ya quedaron guardados).
    conn = database.get_connection(DB_PATH)
    try:
        report_rows = database.load_records(conn, db_records, users_by_email)
        pending = conn.execute(
            "SELECT record_reference, db_name, reason FROM pending_manual_review"
        ).fetchall()
    finally:
        conn.close()

    # 3. Notificación a managers (high + fail-safe).
    sent, skipped = mailer.send_high_classification_alerts(report_rows)

    logger.info(
        "Resumen: %d registros guardados | %d emails enviados | "
        "%d sin destinatario | %d casos en pending_manual_review.",
        len(report_rows), sent, skipped, len(pending),
    )
    for row in pending:
        item = row["db_name"] or row["record_reference"]
        logger.info("  · Revisión manual -> %s: %s", item, row["reason"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
