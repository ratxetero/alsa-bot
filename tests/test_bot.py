"""Pruebas sin red: python -m unittest discover -s tests"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot import main as M
from bot.alsa_client import parsear_json, parsear_tarjetas, url_busqueda, ErrorConsulta
from bot.config import cargar_config

# Estructura real observada en alsa.es (02/11/2026, Logroño -> Madrid Av. América)
def _journey(hora, llegada, cbs, precio=29.67, fecha="02/11/2026"):
    return {"departureStrGeneral": fecha, "departureTime": hora, "arrivalTime": llegada,
            "canBeSold": cbs, "fares": [{"price": precio}]}

JSON_REAL = {"countCanBeSold": 1, "journeys": [
    _journey("02:00", "06:30", "V"), _journey("08:00", "12:15", "F"),
    _journey("12:00", "16:30", "F"), _journey("15:00", "19:00", "F"),
    _journey("15:00", "19:15", "F"), _journey("16:00", "20:20", "F"),
    _journey("17:30", "21:45", "F"), _journey("19:30", "23:45", "F")]}

ROOT = Path(__file__).resolve().parent.parent


class Tests(unittest.TestCase):
    def setUp(self):
        self.cfg = cargar_config(ROOT / "config.yaml")
        self.cfg.espera_reintento_seg = 0
        self.cfg.pausa_entre_trayectos_seg = 0

    def test_config(self):
        self.assertEqual(len(self.cfg.trayectos), 1)  # la ida vacía se ignora
        tr = self.cfg.trayectos[0]
        self.assertTrue(tr.hora_valida("15:00") and tr.hora_valida("17:30"))
        self.assertFalse(tr.hora_valida("14:59") or tr.hora_valida("19:30"))

    def test_parse(self):
        s = parsear_json(JSON_REAL, "02/11/2026")
        self.assertEqual([x.hora for x in s if x.disponible], ["02:00"])
        with self.assertRaises(ErrorConsulta):
            parsear_json(JSON_REAL, "03/11/2026")

    def test_dom(self):
        s = parsear_tarjetas([
            "02:00 4 h 30 min 06:30 Logroño Madrid Desde 29,67 € Ver tarifas",
            "Más rápido 15:00 4 h 19:00 Logroño Madrid No hay plazas disponibles."], "02/11/2026")
        self.assertTrue(s[0].disponible and s[0].precio == 29.67)
        self.assertFalse(s[1].disponible)
        self.assertEqual(s[1].hora, "15:00")

    def test_url(self):
        u = url_busqueda(self.cfg.trayectos[0], 1, "passengerType-4")
        for p in ("originStationId=594", "destinationStationId=5155",
                  "departureDate=02%2F11%2F2026", "seats=1", "passengerType-4=1"):
            self.assertIn(p, u)

    def _run(self, data, estado):
        enviados = []
        with mock.patch.object(M.ConsultaHTTP, "consultar",
                               lambda self, tr: parsear_json(data, tr.fecha)), \
             mock.patch.object(M, "notificar", lambda cfg, a, msgs: enviados.extend(msgs)), \
             mock.patch.object(M, "datetime") as dt:
            from datetime import datetime as real
            dt.now.return_value = real(2026, 10, 26, 12, 0, tzinfo=M.TZ)
            M.ejecutar(self.cfg, estado, dry_run=False)
        return enviados

    def test_flujo_y_no_repetir(self):
        with tempfile.TemporaryDirectory() as d:
            est = Path(d) / "s.json"
            self.assertEqual(self._run(JSON_REAL, est), [])  # todo completo en franja
            libre = json.loads(json.dumps(JSON_REAL))
            libre["journeys"][5]["canBeSold"] = "V"  # se libera la de 16:00
            msgs = self._run(libre, est)
            self.assertEqual(len(msgs), 1)
            self.assertIn("02/11/2026 a las 16:00", msgs[0])
            self.assertIn("1 persona", msgs[0])
            self.assertIn("29,67 €", msgs[0])
            print("\nEjemplo de aviso:\n" + msgs[0])
            self.assertEqual(self._run(libre, est), [])      # no repite
            self._run(JSON_REAL, est)                          # se vuelve a agotar
            self.assertEqual(len(self._run(libre, est)), 1)    # y si vuelve, avisa

    def test_fallback_navegador(self):
        cons = M.Consultor(self.cfg)
        self.cfg.reintentos = 1
        fake_nav = mock.Mock()
        fake_nav.consultar.return_value = parsear_json(JSON_REAL, "02/11/2026")
        with mock.patch.object(M.ConsultaHTTP, "consultar", side_effect=ErrorConsulta("403")), \
             mock.patch.object(cons, "_navegador", return_value=fake_nav):
            r = cons.consultar(self.cfg.trayectos[0])
        self.assertEqual(cons.metodo_usado, "browser")
        self.assertEqual(len(r), 8)


if __name__ == "__main__":
    unittest.main()
