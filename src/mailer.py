"""Envío de emails de solicitud de OK vía SMTP.

Config por variables de entorno: SMTP_HOST, SMTP_PORT, MAIL_FROM.
Se apunta a MailHog (SMTP de pruebas), no a correos reales.
"""

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", "1025"))
MAIL_FROM = os.getenv("MAIL_FROM", "seguridad-informatica@mercadolibre.com")

SUBJECT_HIGH = "[Reválida anual] OK requerido: base '{db_name}' clasificada HIGH"
SUBJECT_FAILSAFE = ("[Reválida anual] Validación urgente: base '{db_name}' con "
                    "clasificación no reconocida '{classification}'")

BODY_HIGH = """\
Hola,

Como parte de la reválida anual del proceso de clasificación de la
información, la siguiente base de datos a cargo de una persona de tu
equipo fue clasificada como ALTA (high):

  - Base de datos: {db_name}
  - Owner:         {owner_email}
  - Clasificación: {classification}

Por favor respondé este correo con tu OK para confirmar que la
clasificación es correcta.

Gracias,
Equipo de Seguridad Informática - Mercado Libre
"""

BODY_FAILSAFE = """\
Hola,

Durante la reválida anual encontramos una base de datos a cargo de una
persona de tu equipo cuya clasificación no corresponde a ningún valor
reconocido (high/medium/low). Por precaución la tratamos con el mismo
nivel de atención que una clasificación ALTA:

  - Base de datos:            {db_name}
  - Owner:                    {owner_email}
  - Clasificación registrada: {classification}

Por favor respondé este correo confirmando la clasificación correcta
de la base.

Gracias,
Equipo de Seguridad Informática - Mercado Libre
"""


def _notification_type(classification):
    """'high' -> notificación normal. Valor no reconocido -> fail-safe.
    'medium', 'low' y 'unknown' (campo ausente) no notifican."""
    if classification == "high":
        return "high"
    if classification not in ("medium", "low", "unknown"):
        return "failsafe"
    return None


def build_message(row, kind):
    """Arma el email según el tipo de notificación."""
    subject = SUBJECT_HIGH if kind == "high" else SUBJECT_FAILSAFE
    body = BODY_HIGH if kind == "high" else BODY_FAILSAFE
    msg = EmailMessage()
    msg["From"] = MAIL_FROM
    msg["To"] = row["manager_email"]
    msg["Subject"] = subject.format(db_name=row["db_name"],
                                    classification=row["classification"])
    msg.set_content(body.format(
        db_name=row["db_name"],
        owner_email=row["owner_email"] or "desconocido",
        classification=row["classification"],
    ))
    return msg


def send_high_classification_alerts(report_rows):
    """Notifica al manager cada base 'high' o con clasificación no
    reconocida (fail-safe). Devuelve (enviados, omitidos)."""
    to_notify = [(r, _notification_type(r["classification"]))
                 for r in report_rows]
    to_notify = [(r, kind) for r, kind in to_notify if kind]

    if not to_notify:
        logger.info("No hay bases que requieran notificación.")
        return 0, 0

    sent, skipped = 0, 0
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as smtp:
            for row, kind in to_notify:
                if not row["manager_email"]:
                    # Sin destinatario: el caso ya quedó persistido en
                    # review_flags durante la carga.
                    skipped += 1
                    logger.warning(
                        "Base '%s' requiere OK pero no se conoce el manager "
                        "del owner ('%s'): gestión manual.",
                        row["db_name"], row["owner_email"],
                    )
                    continue
                smtp.send_message(build_message(row, kind))
                sent += 1
                logger.info("Email (%s) enviado a %s por la base '%s'.",
                            kind, row["manager_email"], row["db_name"])
    except (ConnectionError, OSError, smtplib.SMTPException) as exc:
        logger.error(
            "Sin conexión al SMTP %s:%s (%s). Los datos ya están guardados; "
            "los emails pueden reenviarse cuando el servidor esté disponible.",
            SMTP_HOST, SMTP_PORT, exc,
        )

    return sent, skipped
