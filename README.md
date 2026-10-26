# 🚌 Bot de disponibilidad de billetes ALSA

Comprueba cada 15 minutos (gratis, con GitHub Actions) si hay plazas en ALSA para los
trayectos, fechas y horarios que tú elijas, y te avisa por **email (Gmail)** y **WhatsApp
(CallMeBot)** en cuanto se libera un asiento.

**Configuración incluida:**

| Trayecto | Ruta | Fecha | Horarios | Pasajeros |
|---|---|---|---|---|
| Ida | *(vacía, desactivada)* | — | — | — |
| Vuelta | Logroño → Madrid Interc. Av. América | 02/11/2026 | de 15:00 a 17:30 | 1 (Joven) |

---

## ¿Cómo consulta ALSA? (verificado, no inventado)

ALSA no tiene API pública, pero su web usa un endpoint interno que he comprobado en
alsa.es el 26/10/2026:

1. `GET https://www.alsa.es/checkout?...SearchJourneysAction&originStationId=594&destinationStationId=5155&departureDate=02/11/2026&seats=1&travelType=OUTWARD&passengerType-4=1`
   → guarda la búsqueda en la sesión (cookies).
2. `GET https://www.alsa.es/checkout?...p_p_resource_id=GetJourneyListResource&journeyDirection=I`
   → devuelve JSON con `journeys[]`: `departureTime`, `arrivalTime`, `canBeSold`, `fares[].price`.

`canBeSold = "V"` significa que se puede comprar; `"F"` es lo que la web muestra como
«No hay plazas disponibles» (comprobado comparando el JSON con la página, horario a horario).
**ALSA no muestra el número de plazas libres**, por eso el aviso no lo incluye.

El bot tiene tres niveles, de más ligero a más robusto (modo `auto`):

1. **HTTP directo** con `requests` (2-3 segundos).
2. Si ALSA bloquea eso desde los servidores de GitHub → **Chromium real con Playwright**,
   leyendo el mismo JSON dentro del navegador.
3. Si el JSON fallase → lee las tarjetas de resultados de la página.

Hace como mucho 3 peticiones por trayecto cada 15 minutos (~300 al día), una carga similar a
la de una persona consultando la web. Solo lee información pública: no compra, no inicia sesión
y no salta ningún captcha.

---

## Estructura

```
alsa-bot/
├── .github/workflows/check-alsa.yml   # Ejecución automática cada 15 min
├── bot/
│   ├── config.py        # Lee config.yaml + secretos
│   ├── alsa_client.py   # Consulta ALSA (HTTP / navegador)
│   ├── notifier.py      # Gmail + CallMeBot
│   └── main.py          # Lógica, log, avisos sin repetir
├── tests/test_bot.py    # Pruebas sin red
├── config.yaml          # ← RUTA, FECHAS Y HORARIOS
├── requirements.txt
└── .env.example
```

---

## Paso 1 · Crear el repositorio en GitHub

1. Entra en <https://github.com> (crea una cuenta gratis si no tienes).
2. Arriba a la derecha, pulsa **+ → New repository**.
3. Nombre: `alsa-bot`. Marca **Public** (en repositorios públicos los minutos de Actions son
   ilimitados). No te preocupes: tus contraseñas irán en *Secrets*, que nadie ve.
4. Pulsa **Create repository**.
5. En la página del repo vacío, pulsa **uploading an existing file**, arrastra **todo el
   contenido** de la carpeta `alsa-bot` (descomprimida) y pulsa **Commit changes**.
   - ⚠️ La carpeta `.github` empieza por punto y a veces queda oculta. Comprueba después que
     en el repo existe `.github/workflows/check-alsa.yml`. Si no aparece, crea el archivo a
     mano: **Add file → Create new file**, escribe como nombre
     `.github/workflows/check-alsa.yml` y pega su contenido.

## Paso 2 · Contraseña de aplicación de Gmail

Gmail no deja usar tu contraseña normal desde programas; necesitas una «contraseña de aplicación».

1. Activa la verificación en dos pasos: <https://myaccount.google.com/signinoptions/twosv>.
2. Ve a <https://myaccount.google.com/apppasswords>.
3. Escribe un nombre, por ejemplo `Bot ALSA`, y pulsa **Crear**.
4. Copia la clave de 16 letras (p. ej. `abcd efgh ijkl mnop`). Da igual si copias los espacios:
   el bot los quita.

## Paso 3 · WhatsApp con CallMeBot (gratis, uso personal)

1. Guarda en los contactos de tu móvil el número **+34 694 25 79 52** (nombre: CallMeBot).
2. Envíale por WhatsApp exactamente: `I allow callmebot to send me messages`
3. En unos minutos te responde con tu **API key** (un número, p. ej. `123456`).
   Si no llega en 2 minutos, espera 24 h y vuelve a probar (lo indica su web).
4. Instrucciones oficiales: <https://www.callmebot.com/blog/free-api-whatsapp-messages/>

## Paso 4 · Añadir los secretos en GitHub

En tu repositorio: **Settings → Secrets and variables → Actions → New repository secret**.
Crea estos 5 (el nombre debe ser exactamente así):

| Nombre del secreto | Valor | Ejemplo |
|---|---|---|
| `GMAIL_USER` | Tu Gmail, desde el que se envía | `tucorreo@gmail.com` |
| `GMAIL_APP_PASSWORD` | La contraseña de aplicación del paso 2 | `abcdefghijklmnop` |
| `EMAIL_TO` | Dónde recibir el aviso (varios separados por comas). Si lo dejas sin crear, se usa `GMAIL_USER` | `aagudelo@jig.es` |
| `CALLMEBOT_PHONE` | Tu móvil con prefijo internacional | `+34600111222` |
| `CALLMEBOT_APIKEY` | La API key que te dio CallMeBot | `123456` |

Si no quieres alguno de los dos canales, pon `email: false` o `whatsapp: false` en `config.yaml`.

## Paso 5 · Activar y probar

1. Ve a la pestaña **Actions** del repo. Si aparece un aviso, pulsa
   **I understand my workflows, go ahead and enable them**.
2. En la lista de la izquierda, elige **Comprobar billetes ALSA** → **Run workflow**.
3. En «Qué hacer», elige **prueba-notificaciones** → **Run workflow**.
   Debería llegarte un email y un WhatsApp de prueba en 1-2 minutos.
4. Lanza otra vez con **comprobar-sin-avisar**: entra en la ejecución → *check* →
   *Comprobar disponibilidad* y verás el log, por ejemplo:

   ```
   2026-10-26 12:15:03 [INFO] === Comprobación 26/10/2026 12:15:03 | modo=auto | pasajeros=1 ===
   2026-10-26 12:15:03 [INFO] [Vuelta] Logroño → Madrid Interc. Av América | 02/11/2026 | horarios: 15:00-17:30
   2026-10-26 12:15:05 [INFO] [Vuelta] 8 salidas en total, 4 en tus horarios (vía http)
   2026-10-26 12:15:05 [INFO]    15:00 → 19:00  completo
   2026-10-26 12:15:05 [INFO]    15:00 → 19:15  completo
   2026-10-26 12:15:05 [INFO]    16:00 → 20:20  completo
   2026-10-26 12:15:05 [INFO]    17:30 → 21:45  completo
   2026-10-26 12:15:05 [INFO] Sin disponibilidad nueva en tus horarios.
   ```

   Si ves `vía http`, perfecto. Si ves `Probando con navegador...` y luego `vía browser`,
   ALSA ha bloqueado el modo rápido y el bot ha cambiado solo al navegador (más lento, pero funciona).
5. A partir de aquí se ejecuta solo cada 15 min. Cuando haya plazas recibirás algo como:

   > ✅ Hay billetes disponibles: Logroño → Madrid Interc. Av América, 02/11/2026 a las 16:00
   > (llegada 20:20), para 1 persona. Plazas libres: ALSA no indica el número. Precio desde
   > 29,67 €. Reserva aquí: https://www.alsa.es/checkout?...

**No te avisa dos veces del mismo horario.** Si ese horario se vuelve a agotar y después se
libera otra vez, te vuelve a avisar.

## Paso 6 · Cambiar ruta, fechas u horarios

Todo se cambia en `config.yaml` (en GitHub: abre el archivo → icono del lápiz → edita →
**Commit changes**).

- **Fecha:** `fecha: "02/11/2026"` (formato DD/MM/AAAA). Vacía `""` = trayecto desactivado.
- **Horarios:** lista de horas exactas o rangos:
  ```yaml
  horarios:
    - "08:30"          # solo la salida de las 08:30
    - "15:00-17:30"    # cualquier salida entre 15:00 y 17:30, ambas incluidas
  ```
  Con `horarios: []` vale cualquier hora del día.
- **Pasajeros:** `pasajeros: 2` → solo avisa si hay plazas para los 2.
- **Activar la ida:** rellena el bloque `Ida` igual que el de la vuelta.

**Cómo saber los IDs de estación (y el tipo de pasajero):**

1. Haz la búsqueda normal en <https://www.alsa.es>.
2. Mira la dirección de la página de resultados. Contiene, por ejemplo:
   `originStationId=594&destinationStationId=5155&...&passengerType-4=1`
3. `originStationId` → `origen_id`, `destinationStationId` → `destino_id`, y
   `passengerType-4` → `tipo_pasajero_param` (4 = Joven, verificado; para Adulto u otros
   tipos, copia el número que te salga a ti).

IDs verificados: **Logroño = 594**, **Madrid Interc. Av. América = 5155**.

## Probar en tu ordenador (opcional)

```bash
pip install -r requirements.txt
python -m playwright install chromium
python -m unittest discover -s tests -t .      # pruebas sin red
# Exporta las variables de .env.example con tus valores y luego:
python -m bot.main --dry-run                    # comprueba sin avisar
python -m bot.main --test-notify                # prueba de notificaciones
```

## Cosas a tener en cuenta

- **GitHub puede retrasar** las ejecuciones programadas (a veces 5-20 min en horas punta).
  No es exacto al minuto.
- **Cuando pase el viaje**, el bot detecta que la fecha ya ha pasado y no consulta nada. Aun así
  conviene desactivarlo: **Actions → Comprobar billetes ALSA → ··· → Disable workflow**.
- GitHub desactiva los workflows programados si el repo pasa **60 días sin actividad**.
- Si ALSA cambia su web, el log mostrará errores tipo «Respuesta JSON sin 'journeys'». El bot
  no se cae (sigue intentándolo cada 15 min), pero habría que adaptar `bot/alsa_client.py`.
- Si todo falla varias veces seguidas puedes poner `modo: "browser"` en `config.yaml`.
- Uso personal y moderado: consulta solo información pública. Revisa las condiciones de uso de
  alsa.es si vas a ampliar mucho el número de rutas o la frecuencia.
