"""Carga y valida config.yaml + variables de entorno (secretos)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path

import yaml

_HORA = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")


def _parse_hora(txt: str) -> time:
    m = _HORA.match(txt)
    if not m:
        raise ValueError(f"Hora no válida: '{txt}' (usa HH:MM)")
    return time(int(m.group(1)), int(m.group(2)))


@dataclass
class Franja:
    desde: time
    hasta: time

    def contiene(self, hhmm: str) -> bool:
        h = _parse_hora(hhmm)
        return self.desde <= h <= self.hasta

    def __str__(self) -> str:
        if self.desde == self.hasta:
            return self.desde.strftime("%H:%M")
        return f"{self.desde:%H:%M}-{self.hasta:%H:%M}"


@dataclass
class Trayecto:
    nombre: str
    origen_nombre: str
    origen_id: str
    destino_nombre: str
    destino_id: str
    fecha: str  # DD/MM/AAAA
    franjas: list[Franja] = field(default_factory=list)

    @property
    def fecha_dt(self) -> datetime:
        return datetime.strptime(self.fecha, "%d/%m/%Y")

    def hora_valida(self, hhmm: str) -> bool:
        # Sin horarios = cualquier hora del día
        return not self.franjas or any(f.contiene(hhmm) for f in self.franjas)

    @property
    def descripcion(self) -> str:
        return f"{self.origen_nombre} → {self.destino_nombre}"


@dataclass
class Secretos:
    gmail_user: str = ""
    gmail_app_password: str = ""
    email_to: str = ""
    callmebot_phone: str = ""
    callmebot_apikey: str = ""


@dataclass
class Config:
    pasajeros: int
    tipo_pasajero_param: str
    trayectos: list[Trayecto]
    modo: str
    reintentos: int
    espera_reintento_seg: int
    pausa_entre_trayectos_seg: int
    avisar_solo_cambios: bool
    notif_email: bool
    notif_whatsapp: bool
    secretos: Secretos


def _parse_franjas(items) -> list[Franja]:
    franjas = []
    for it in items or []:
        it = str(it).strip()
        if not it:
            continue
        if "-" in it:
            a, b = it.split("-", 1)
            franjas.append(Franja(_parse_hora(a), _parse_hora(b)))
        else:
            h = _parse_hora(it)
            franjas.append(Franja(h, h))
    return franjas


def cargar_config(ruta: str | Path = "config.yaml") -> Config:
    with open(ruta, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    trayectos = []
    for t in raw.get("trayectos", []) or []:
        fecha = str(t.get("fecha") or "").strip()
        if not fecha:
            continue  # trayecto desactivado (p. ej. ida ya comprada)
        tr = Trayecto(
            nombre=str(t.get("nombre", "Trayecto")),
            origen_nombre=str(t.get("origen_nombre", "")),
            origen_id=str(t.get("origen_id", "")).strip(),
            destino_nombre=str(t.get("destino_nombre", "")),
            destino_id=str(t.get("destino_id", "")).strip(),
            fecha=fecha,
            franjas=_parse_franjas(t.get("horarios")),
        )
        if not tr.origen_id or not tr.destino_id:
            raise ValueError(f"Trayecto '{tr.nombre}': faltan origen_id/destino_id")
        tr.fecha_dt  # valida formato
        trayectos.append(tr)

    notif = raw.get("notificaciones", {}) or {}
    env = os.environ.get
    secretos = Secretos(
        gmail_user=env("GMAIL_USER", "").strip(),
        gmail_app_password=env("GMAIL_APP_PASSWORD", "").replace(" ", "").strip(),
        email_to=(env("EMAIL_TO", "") or env("GMAIL_USER", "")).strip(),
        callmebot_phone=env("CALLMEBOT_PHONE", "").strip(),
        callmebot_apikey=env("CALLMEBOT_APIKEY", "").strip(),
    )

    modo = str(raw.get("modo", "auto")).lower()
    if modo not in {"auto", "http", "browser"}:
        raise ValueError("modo debe ser auto, http o browser")

    return Config(
        pasajeros=int(raw.get("pasajeros", 1)),
        tipo_pasajero_param=str(raw.get("tipo_pasajero_param", "passengerType-4")),
        trayectos=trayectos,
        modo=modo,
        reintentos=int(raw.get("reintentos", 3)),
        espera_reintento_seg=int(raw.get("espera_reintento_seg", 20)),
        pausa_entre_trayectos_seg=int(raw.get("pausa_entre_trayectos_seg", 5)),
        avisar_solo_cambios=bool(raw.get("avisar_solo_cambios", True)),
        notif_email=bool(notif.get("email", True)),
        notif_whatsapp=bool(notif.get("whatsapp", True)),
        secretos=secretos,
    )
