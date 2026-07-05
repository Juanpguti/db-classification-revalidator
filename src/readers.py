"""Lectura y validación de los archivos de entrada (JSON y CSV)."""

import csv
import json
import logging

logger = logging.getLogger(__name__)

CLASSIFICATION_UNKNOWN = "unknown"
VALID_CLASSIFICATIONS = {"high", "medium", "low"}


def read_databases_json(path):
    """Lee el JSON de bases de datos y normaliza cada registro."""
    with open(path, encoding="utf-8") as f:
        raw_records = json.load(f)

    if not isinstance(raw_records, list):
        raise ValueError("El JSON debe contener una lista de registros.")

    records = [_normalize_db_record(raw, i) for i, raw in enumerate(raw_records)]
    logger.info("JSON procesado: %d registros.", len(records))
    return records


def _normalize_db_record(raw, position):
    """Normaliza un registro y anota los motivos de revisión manual."""
    db_name = _clean(raw.get("db_name"))
    owner_email = _clean(raw.get("owner_email"))
    classification = _clean(raw.get("classification"))
    review_reasons = []
    ref = db_name or f"registro #{position}"

    if not db_name:
        logger.warning("Registro #%d sin nombre de base: %s", position, raw)
        review_reasons.append("el registro no tiene nombre de base en el JSON")

    if not classification:
        # Campo ausente: no hay valor que validar, no dispara email.
        classification = CLASSIFICATION_UNKNOWN
        logger.warning("'%s': sin clasificación, se guarda como '%s'.",
                       ref, CLASSIFICATION_UNKNOWN)
    elif classification.lower() not in VALID_CLASSIFICATIONS:
        # Fail-safe: un valor desconocido podría ser más grave que 'high'.
        # Se conserva el valor original (trazabilidad) y se notifica igual.
        classification = classification.lower()
        review_reasons.append(
            f"clasificación '{classification}' no reconocida: "
            "se notifica como HIGH por precaución (fail-safe)"
        )
        logger.warning("'%s': clasificación '%s' no reconocida, se aplica "
                       "fail-safe.", ref, classification)
    else:
        classification = classification.lower()

    if not owner_email:
        logger.warning("'%s': sin owner, pendiente de revisión manual.", ref)

    return {
        "db_name": db_name,
        "owner_email": owner_email,
        "classification": classification,
        "review_reasons": review_reasons,
        "source_position": position,
        "raw_payload": raw,
    }


def read_users_csv(path):
    """Lee el CSV (row_id, user_id, user_state, user_manager) y devuelve
    un dict {user_id: {state, manager}} para cruzar owners en O(1)."""
    users = {}
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        for row in reader:
            user_id = _clean(row.get("user_id"))
            if not user_id:
                logger.warning("Fila %s del CSV sin user_id, se omite.",
                               row.get("row_id"))
                continue
            users[user_id] = {
                "state": _clean(row.get("user_state")) or "unknown",
                "manager": _clean(row.get("user_manager")) or None,
            }

    logger.info("CSV procesado: %d usuarios.", len(users))
    return users


def _clean(value):
    """Strip si es string; None si queda vacío."""
    if value is None:
        return None
    value = str(value).strip()
    return value or None
