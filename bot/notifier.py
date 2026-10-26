"""Notificaciones por Gmail (SMTP + contraseña de aplicación) y WhatsApp (CallMeBot)."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.text import MIMEText

import requests

from .config import Secretos

log = logging.getLogger(__name__)


def enviar_email(sec: Secretos, asunto: str, cuerpo: str) -> bool:
    if not (sec.gmail_user and sec.gmail_app_password and sec.email_to):
        log.warning("Email no configurado (faltan GMAIL_USER / GMAIL_APP_PASSWORD)")
        return False
    msg = MIMEText(cuerpo, "plain", "utf-8")
    msg["Subject"] = asunto
    msg["From"] = sec.gmail_user
    msg["To"] = sec.email_to
    destinatarios = [d.strip() for d in sec.email_to.split(",") if d.strip()]
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as s:
            s.login(sec.gmail_user, sec.gmail_app_password)
            s.sendmail(sec.gmail_user, destinatarios, msg.as_string())
        log.info("Email enviado a %s", sec.email_to)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("Fallo enviando email: %s", e)
        return False


def enviar_whatsapp(sec: Secretos, texto: str) -> bool:
    if not (sec.callmebot_phone and sec.callmebot_apikey):
        log.warning("WhatsApp no configurado (faltan CALLMEBOT_PHONE / CALLMEBOT_APIKEY)")
        return False
    try:
        r = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": sec.callmebot_phone, "text": texto, "apikey": sec.callmebot_apikey},
            timeout=30,
        )
        ok = r.status_code == 200 and "error" not in r.text.lower()[:300]
        if ok:
            log.info("WhatsApp enviado")
        else:
            log.error("CallMeBot respondió %s: %s", r.status_code, r.text[:200])
        return ok
    except requests.RequestException as e:
        log.error("Fallo enviando WhatsApp: %s", e)
        return False
