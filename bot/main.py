"""Punto de entrada: comprueba disponibilidad y avisa.

Uso:
    python -m bot.main                 # comprobación normal
    python -m bot.main --dry-run       # comprueba pero no envía avisos
    python -m bot.main --test-notify   # envía un aviso de prueba y termina
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .alsa_client import ConsultaHTTP, ConsultaNavegador, ErrorConsulta, Salida, url_busqueda
from .config import Config, Trayecto, cargar_config
from .notifier import enviar_email, enviar_whatsapp

TZ = ZoneInfo("Europe/Madrid")
log = logging.getLogger("alsa-bot")


# ------------------------------------------------------------------ estado
def cargar_estado(ruta: Path) -> dict:
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def guardar_estado(ruta: Path, estado: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ consulta
class Consultor:
    """Gestiona HTTP / navegador, reintentos y cambio automático de método."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._http = ConsultaHTTP(cfg.pasajeros, cfg.tipo_pasajero_param)
        self._nav: ConsultaNavegador | None = None
        self.metodo_usado = ""

    def _navegador(self) -> ConsultaNavegador:
        if self._nav is None:
            log.info("Iniciando navegador Chromium (Playwright)...")
            self._nav = ConsultaNavegador(self.cfg.pasajeros, self.cfg.tipo_pasajero_param)
        return self._nav

    def _con_reintentos(self, fn, tr: Trayecto, etiqueta: str) -> list[Salida]:
        ultimo = None
        for intento in range(1, self.cfg.reintentos + 1):
            try:
                return fn(tr)
            except ErrorConsulta as e:
                ultimo = e
                log.warning("[%s] intento %d/%d fallido: %s", etiqueta, intento, self.cfg.reintentos, e)
                if intento < self.cfg.reintentos:
                    time.sleep(self.cfg.espera_reintento_seg * intento)
        raise ErrorConsulta(f"{etiqueta}: agotados los reintentos ({ultimo})")

    def consultar(self, tr: Trayecto) -> list[Salida]:
        modo = self.cfg.modo
        if modo in ("auto", "http"):
            try:
                res = self._con_reintentos(self._http.consultar, tr, "http")
                self.metodo_usado = "http"
                return res
            except ErrorConsulta as e:
                if modo == "http":
                    raise
                log.warning("HTTP directo no funcionó (%s). Probando con navegador...", e)
        res = self._con_reintentos(lambda t: self._navegador().consultar(t), tr, "browser")
        self.metodo_usado = "browser"
        return res

    def cerrar(self) -> None:
        if self._nav:
            try:
                self._nav.cerrar()
            except Exception:  # noqa: BLE001
                pass


# ------------------------------------------------------------------ mensajes
def _personas(n: int) -> str:
    return "1 persona" if n == 1 else f"{n} personas"


def construir_mensaje(tr: Trayecto, s: Salida, cfg: Config) -> str:
    precio = f" Precio desde {s.precio:.2f} €.".replace(f"{s.precio:.2f}", f"{s.precio:.2f}".replace(".", ",")) if s.precio else ""
    llegada = f" (llegada {s.llegada})" if s.llegada else ""
    return (
        f"✅ Hay billetes disponibles: {tr.descripcion}, {s.fecha} a las {s.hora}{llegada}, "
        f"para {_personas(cfg.pasajeros)}. Plazas libres: ALSA no indica el número.{precio} "
        f"Reserva aquí: {url_busqueda(tr, cfg.pasajeros, cfg.tipo_pasajero_param)}"
    )


def notificar(cfg: Config, asunto: str, mensajes: list[str]) -> None:
    texto = "\n\n".join(mensajes)
    if cfg.notif_email:
        enviar_email(cfg.secretos, asunto, texto + "\n\n— Bot ALSA (GitHub Actions)")
    if cfg.notif_whatsapp:
        for m in mensajes:  # un WhatsApp por horario: más legible
            enviar_whatsapp(cfg.secretos, m)
            time.sleep(2)


def resumen_github(lineas: list[str]) -> None:
    ruta = os.environ.get("GITHUB_STEP_SUMMARY")
    if ruta:
        with open(ruta, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lineas) + "\n")


# ------------------------------------------------------------------ main
def ejecutar(cfg: Config, ruta_estado: Path, dry_run: bool) -> int:
    ahora = datetime.now(TZ)
    log.info("=== Comprobación %s | modo=%s | pasajeros=%d ===",
             ahora.strftime("%d/%m/%Y %H:%M:%S"), cfg.modo, cfg.pasajeros)
    estado = cargar_estado(ruta_estado)
    resumen = [f"### Comprobación ALSA {ahora:%d/%m/%Y %H:%M}", ""]
    nuevos: list[str] = []
    errores = 0
    activos = 0
    consultor = Consultor(cfg)
    try:
        for i, tr in enumerate(cfg.trayectos):
            if tr.fecha_dt.date() < ahora.date():
                log.info("[%s] %s del %s ya ha pasado: se omite.", tr.nombre, tr.descripcion, tr.fecha)
                continue
            activos += 1
            if i:
                time.sleep(cfg.pausa_entre_trayectos_seg)
            franjas = ", ".join(map(str, tr.franjas)) or "todo el día"
            log.info("[%s] %s | %s | horarios: %s", tr.nombre, tr.descripcion, tr.fecha, franjas)
            try:
                salidas = consultor.consultar(tr)
            except ErrorConsulta as e:
                errores += 1
                log.error("[%s] No se pudo comprobar: %s", tr.nombre, e)
                resumen.append(f"- ❌ **{tr.nombre}** {tr.descripcion} {tr.fecha}: error ({e})")
                continue

            en_franja = [s for s in salidas if tr.hora_valida(s.hora)]
            log.info("[%s] %d salidas en total, %d en tus horarios (vía %s)",
                     tr.nombre, len(salidas), len(en_franja), consultor.metodo_usado)
            if not en_franja:
                log.info("[%s] Ninguna salida de ALSA cae en tus horarios ese día.", tr.nombre)
            for s in en_franja:
                log.info("   %s → %s  %s%s", s.hora, s.llegada,
                         "DISPONIBLE" if s.disponible else "completo",
                         f"  ({s.precio:.2f} €)" if s.precio and s.disponible else "")

            disponibles = [s for s in en_franja if s.disponible]
            clave_tr = f"{tr.origen_id}-{tr.destino_id}-{tr.fecha}"
            ya_avisados = set(estado.get(clave_tr, []))
            claves_disp = {s.clave for s in disponibles}
            for s in disponibles:
                if cfg.avisar_solo_cambios and s.clave in ya_avisados:
                    log.info("   (ya avisado antes: %s)", s.hora)
                    continue
                nuevos.append(construir_mensaje(tr, s, cfg))
            # Recordar solo los que siguen disponibles: si se agota y vuelve, se avisa otra vez
            estado[clave_tr] = sorted(claves_disp)

            txt = ", ".join(f"{s.hora} {'✅' if s.disponible else '⛔'}" for s in en_franja) or "sin salidas"
            resumen.append(f"- **{tr.nombre}** {tr.descripcion} {tr.fecha} ({franjas}): {txt}")
    finally:
        consultor.cerrar()

    if activos == 0:
        log.info("No hay trayectos activos (vacíos o con fecha pasada). Nada que hacer.")
    if nuevos:
        log.info("¡Disponibilidad nueva! %d aviso(s).", len(nuevos))
        for m in nuevos:
            log.info("   %s", m)
        if dry_run:
            log.info("--dry-run: no se envían avisos.")
        else:
            notificar(cfg, f"✅ ALSA: hay billetes ({len(nuevos)} horario/s)", nuevos)
    else:
        log.info("Sin disponibilidad nueva en tus horarios.")

    if not dry_run:
        guardar_estado(ruta_estado, estado)
    resumen_github(resumen)
    if errores and errores == activos:
        log.warning("Todas las comprobaciones fallaron en esta ejecución; se reintentará en la próxima.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bot de disponibilidad de billetes ALSA")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--estado", default="state/notified.json")
    ap.add_argument("--dry-run", action="store_true", help="no envía avisos ni guarda estado")
    ap.add_argument("--test-notify", action="store_true", help="envía un aviso de prueba y sale")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    logging.Formatter.converter = lambda *a: datetime.now(TZ).timetuple()

    try:
        cfg = cargar_config(args.config)
    except Exception as e:  # noqa: BLE001
        log.error("Error en la configuración: %s", e)
        return 2

    if args.test_notify:
        msg = "🧪 Prueba del bot ALSA: si lees esto, las notificaciones funcionan."
        if cfg.trayectos:
            tr = cfg.trayectos[0]
            msg += f" Vigilando {tr.descripcion} el {tr.fecha}."
        notificar(cfg, "🧪 Prueba bot ALSA", [msg])
        return 0

    try:
        return ejecutar(cfg, Path(args.estado), args.dry_run)
    except Exception as e:  # noqa: BLE001  -- nunca romper el workflow por un fallo puntual
        log.exception("Error inesperado: %s", e)
        return 0


if __name__ == "__main__":
    sys.exit(main())
