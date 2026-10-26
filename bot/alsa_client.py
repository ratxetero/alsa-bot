"""Consulta de disponibilidad en alsa.es.

Flujo verificado en la web real de ALSA (octubre 2026):

1. GET /checkout?...javax.portlet.action=SearchJourneysAction&originStationId=..&
   destinationStationId=..&departureDate=DD/MM/AAAA&seats=N&travelType=OUTWARD&passengerType-X=N
   -> guarda la búsqueda en la sesión (cookies).
2. GET /checkout?...p_p_resource_id=GetJourneyListResource&journeyDirection=I
   -> JSON con "journeys": [{departureTime, arrivalTime, departureStrGeneral,
      canBeSold ("V" = a la venta / "F" = sin plazas), fares:[{price}]...}]

Hay dos implementaciones:
- ConsultaHTTP: requests puro (rápido, sin navegador).
- ConsultaNavegador: Chromium real con Playwright (por si ALSA bloquea peticiones
  "no navegador"); lee el mismo JSON y, si no puede, la propia página.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlencode

import requests

from .config import Trayecto

log = logging.getLogger(__name__)

BASE = "https://www.alsa.es"
PORTLET = "com_babel_alsa_espania_purchase_web_PurchasePortlet"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
URL_JOURNEYS = (
    f"{BASE}/checkout?p_p_id={PORTLET}&p_p_lifecycle=2&p_p_state=normal&p_p_mode=view"
    "&p_p_resource_id=GetJourneyListResource&p_p_cacheability=cacheLevelPage&journeyDirection=I"
)
TEXTO_SIN_PLAZAS = "No hay plazas disponibles"


class ErrorConsulta(Exception):
    """Fallo temporal o bloqueo al consultar ALSA."""


@dataclass
class Salida:
    fecha: str
    hora: str
    llegada: str
    disponible: bool
    precio: float | None = None
    estado: str = ""  # valor bruto de canBeSold

    @property
    def clave(self) -> str:
        return f"{self.fecha} {self.hora}->{self.llegada}"


def url_busqueda(tr: Trayecto, pasajeros: int, tipo_param: str) -> str:
    """URL de búsqueda de alsa.es (sirve también como enlace para reservar)."""
    params = {
        "p_p_id": PORTLET,
        "p_p_lifecycle": "1",
        "p_p_state": "normal",
        "p_p_mode": "view",
        f"_{PORTLET}_javax.portlet.action": "SearchJourneysAction",
        "alsaspam": "",
        "originStationId": tr.origen_id,
        "destinationStationId": tr.destino_id,
        "departureDate": tr.fecha,
        "returnDate": "",
        "seats": str(pasajeros),
        "promoCode": "",
        "youngPromoCode": "",
        "travelType": "OUTWARD",
        "employee": "false",
        tipo_param: str(pasajeros),
    }
    return f"{BASE}/checkout?{urlencode(params)}"


def parsear_json(data: dict, fecha_esperada: str) -> list[Salida]:
    if not isinstance(data, dict) or "journeys" not in data:
        raise ErrorConsulta("Respuesta JSON sin 'journeys' (¿cambio en la web?)")
    salidas = []
    for j in data.get("journeys") or []:
        fecha = str(j.get("departureStrGeneral", ""))
        if fecha and fecha != fecha_esperada:
            raise ErrorConsulta(
                f"ALSA devolvió la fecha {fecha} en lugar de {fecha_esperada} (sesión mezclada)"
            )
        precio = None
        fares = j.get("fares") or []
        if fares and isinstance(fares[0], dict) and fares[0].get("price") is not None:
            try:
                precio = float(fares[0]["price"])
            except (TypeError, ValueError):
                pass
        estado = str(j.get("canBeSold", ""))
        salidas.append(
            Salida(
                fecha=fecha or fecha_esperada,
                hora=str(j.get("departureTime", "")),
                llegada=str(j.get("arrivalTime", "")),
                disponible=(estado == "V"),
                precio=precio,
                estado=estado,
            )
        )
    return salidas


# ----------------------------------------------------------------- HTTP puro
class ConsultaHTTP:
    nombre = "http"

    def __init__(self, pasajeros: int, tipo_param: str, timeout: int = 30):
        self.pasajeros = pasajeros
        self.tipo_param = tipo_param
        self.timeout = timeout

    def _sesion(self) -> requests.Session:
        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": UA,
                "Accept-Language": "es-ES,es;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )
        return s

    def consultar(self, tr: Trayecto) -> list[Salida]:
        s = self._sesion()  # sesión nueva por trayecto: evita mezclar búsquedas
        try:
            r = s.get(f"{BASE}/", timeout=self.timeout)
            r.raise_for_status()
            url = url_busqueda(tr, self.pasajeros, self.tipo_param)
            r = s.get(url, timeout=self.timeout, headers={"Referer": f"{BASE}/"})
            r.raise_for_status()
            if "jsonJourneySearch" not in r.text:
                raise ErrorConsulta("La página de resultados no contiene la búsqueda (¿bloqueo?)")
            r = s.get(
                URL_JOURNEYS,
                timeout=self.timeout,
                headers={"Referer": url, "Accept": "application/json, text/plain, */*"},
            )
            r.raise_for_status()
            try:
                data = r.json()
            except ValueError as e:
                raise ErrorConsulta(f"Respuesta no JSON ({r.text[:120]!r})") from e
        except requests.RequestException as e:
            raise ErrorConsulta(f"Error HTTP: {e}") from e
        return parsear_json(data, tr.fecha)

    def cerrar(self) -> None:
        pass


# ------------------------------------------------------- Navegador (Playwright)
_JS_FETCH = """async (url) => {
  const r = await fetch(url, {credentials: 'include', headers: {'Accept': 'application/json'}});
  return {status: r.status, text: await r.text()};
}"""

_JS_TARJETAS = """() => [...document.querySelectorAll('li.c-journey-list__card')]
   .map(li => li.innerText)"""

_RE_HORA = re.compile(r"\b(\d{2}:\d{2})\b")
_RE_PRECIO = re.compile(r"(\d+[.,]\d{2})\s*€")


def parsear_tarjetas(textos: list[str], fecha: str) -> list[Salida]:
    """Último recurso: interpretar el texto de las tarjetas de resultados."""
    salidas = []
    for t in textos:
        horas = _RE_HORA.findall(t)
        if not horas:
            continue
        precio = _RE_PRECIO.search(t)
        disponible = TEXTO_SIN_PLAZAS not in t
        salidas.append(
            Salida(
                fecha=fecha,
                hora=horas[0],
                llegada=horas[1] if len(horas) > 1 else "",
                disponible=disponible,
                precio=float(precio.group(1).replace(",", ".")) if precio else None,
                estado="DOM",
            )
        )
    return salidas


class ConsultaNavegador:
    nombre = "browser"

    def __init__(self, pasajeros: int, tipo_param: str, timeout_ms: int = 60000):
        from playwright.sync_api import sync_playwright  # import perezoso

        self.pasajeros = pasajeros
        self.tipo_param = tipo_param
        self.timeout_ms = timeout_ms
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)

    def consultar(self, tr: Trayecto) -> list[Salida]:
        ctx = self._browser.new_context(
            user_agent=UA, locale="es-ES", timezone_id="Europe/Madrid",
            viewport={"width": 1366, "height": 900},
        )
        page = ctx.new_page()
        page.set_default_timeout(self.timeout_ms)
        try:
            page.goto(f"{BASE}/", wait_until="domcontentloaded")
            url = url_busqueda(tr, self.pasajeros, self.tipo_param)
            page.goto(url, wait_until="domcontentloaded")
            # 1) JSON interno, dentro del navegador (mismas cookies)
            try:
                res = page.evaluate(_JS_FETCH, URL_JOURNEYS)
                if res and res.get("status") == 200:
                    import json

                    return parsear_json(json.loads(res["text"]), tr.fecha)
                log.warning("JSON en navegador devolvió estado %s", res and res.get("status"))
            except Exception as e:  # noqa: BLE001
                log.warning("No se pudo leer el JSON en navegador: %s", e)
            # 2) Leer la página renderizada
            try:
                page.wait_for_selector("li.c-journey-list__card", timeout=30000)
            except Exception as e:  # noqa: BLE001
                raise ErrorConsulta(f"No aparecen resultados en la página: {e}") from e
            return parsear_tarjetas(page.evaluate(_JS_TARJETAS), tr.fecha)
        except ErrorConsulta:
            raise
        except Exception as e:  # noqa: BLE001
            raise ErrorConsulta(f"Error en navegador: {e}") from e
        finally:
            ctx.close()

    def cerrar(self) -> None:
        try:
            self._browser.close()
        finally:
            self._pw.stop()
