"""
Bot Reserva Doble — Canchas Las Condes
v4.7 · Marzo 2026
- Fix CRÍTICO: campo celular es name="patientPhone" type="text"
- Fix CRÍTICO: botones de hora tienen ícono dentro (texto parcial)
- Fix: selector de botón hora usa .text-content() en lugar de inner_text exacto
"""

import asyncio
import datetime
import os
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from playwright.async_api import async_playwright

# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("BotReserva")

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN no configurado en variables de entorno")

URL = "https://reservadehoras.lascondes.cl/#/agenda/28/agendar"

def ahora():
    # Chile verano = UTC-3
    return datetime.datetime.utcnow() - datetime.timedelta(hours=3)

(
    E_RUT1, E_HORA1, E_HORA1_ALT, E_TEL1,
    E_RUT2, E_HORA2, E_HORA2_ALT, E_TEL2,
) = range(8)

configs       = {}
tareas        = {}
estado        = {}
horas_ganadas = {}


# ═══════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════

def calcular_dv(cuerpo: str) -> str:
    s, m = 0, 2
    for d in reversed(cuerpo):
        s += int(d) * m
        m = m + 1 if m < 7 else 2
    r = 11 - (s % 11)
    return {11: "0", 10: "K"}.get(r, str(r))

def validar_rut(texto: str):
    rut = texto.replace(".", "").replace("-", "").replace(" ", "").upper()
    if len(rut) < 2 or not rut[:-1].isdigit():
        return None
    return rut if calcular_dv(rut[:-1]) == rut[-1] else None

def fmt(rut: str) -> str:
    return f"{rut[:-1]}-{rut[-1]}"

def parsear_hora(texto: str):
    """Acepta: 9, 09, 9:00, 09:00, 9:00 hrs, etc."""
    try:
        limpio = texto.strip().lower()
        limpio = limpio.replace("hrs", "").replace(":", "").replace(" ", "")
        if len(limpio) > 2:
            limpio = limpio[:2]
        h = int(limpio)
        return h if 6 <= h <= 23 else None
    except Exception as e:
        logger.error(f"parsear_hora error: {e} — texto='{texto}'")
        return None

def sleep_dinamico() -> float:
    h = ahora()
    hr, mn, sg = h.hour, h.minute, h.second
    if (hr == 17 and mn == 59 and sg >= 45) or (hr == 18 and mn == 0) or (hr == 18 and mn == 1 and sg <= 30):
        return 0.5
    if (hr == 17 and mn == 59) or (hr == 18 and mn <= 5):
        return 1.0
    return 2.0


# ═══════════════════════════════════════════════════════════════
# HELPERS PLAYWRIGHT
# ═══════════════════════════════════════════════════════════════

async def _ingresar_rut(page, rut_raw: str) -> bool:
    for sel in ['input[name="rut"]', 'input[placeholder*="RUT"]', '#rut', 'input[type="text"]']:
        campo = await page.query_selector(sel)
        if campo:
            await campo.fill("")
            await campo.type(rut_raw, delay=30)
            return True
    return False

async def _click_validar(page) -> bool:
    for sel in ['button:has-text("Validar")', 'button[type="submit"]']:
        btn = await page.query_selector(sel)
        if btn:
            await btn.click()
            return True
    return False

async def _buscar_boton_hora(page, hora_int: int, ganadas: set):
    """
    Los botones de hora contienen un ícono ⓘ además del texto.
    Ejemplo real en el DOM: <button>9:00<i ...>ⓘ</i></button>
    Por eso usamos text-content parcial en lugar de match exacto.
    """
    if hora_int in ganadas:
        return None, None

    # Formatos posibles que puede mostrar el sitio
    prefijos = [f"{hora_int}:00", f"{hora_int:02d}:00"]

    botones = await page.query_selector_all("button")
    for btn in botones:
        try:
            # inner_text() incluye texto visible (sin tags HTML internos)
            texto = (await btn.inner_text()).strip()

            # Verificar que empiece con la hora buscada
            coincide = any(texto.startswith(p) for p in prefijos)
            if not coincide:
                continue

            if hora_int in ganadas:
                continue

            # Verificar que no esté deshabilitado
            if await btn.get_attribute("disabled") is not None:
                continue

            clase = (await btn.get_attribute("class") or "").lower()
            if "disabled" in clase or "unavailable" in clase:
                continue

            # Verificar que sea clickeable (visible y habilitado)
            if not await btn.is_visible():
                continue

            return btn, f"{hora_int}:00"

        except:
            continue
    return None, None

async def _listar_horas_visibles(page) -> list:
    """Lista todas las horas disponibles/no disponibles en la pantalla."""
    horas = []
    botones = await page.query_selector_all("button")
    for btn in botones:
        try:
            texto = (await btn.inner_text()).strip()
            # Detectar botones de hora (contienen ":00")
            if ":00" not in texto:
                continue
            if len(texto) > 15:  # evitar botones largos que no son horas
                continue
            disabled = await btn.get_attribute("disabled")
            est = "OCUPADA" if disabled is not None else "LIBRE"
            horas.append(f"{est}: {texto[:8]}")
        except:
            continue
    return horas

async def _click_boton(page, textos: list) -> bool:
    for texto in textos:
        btn = await page.query_selector(f'button:has-text("{texto}")')
        if btn and await btn.get_attribute("disabled") is None:
            await btn.click()
            return True
    return False


# ═══════════════════════════════════════════════════════════════
# PANTALLA 3 — Confirmación de datos personales
# CAMPO REAL (del inspector HTML): name="patientPhone" type="text"
# ═══════════════════════════════════════════════════════════════

async def _completar_pantalla3(page, tel: str, msg_fn) -> bool:
    """
    Pantalla 3 real del sitio:
      - Nombre (readonly): PABLO
      - Apellido (readonly): COSTA
      - Correo (readonly): placeholder "Correo"
      - Celular (EDITABLE): name="patientPhone" placeholder="Celular" type="text"
      - Botón: "Aceptar y finalizar"
    """

    # 1. Esperar que cargue la Pantalla 3
    await msg_fn("⏳ Esperando Pantalla 3...")
    pantalla3_ok = False
    for _ in range(15):  # hasta 7.5 segundos
        try:
            body = await page.inner_text("body")
            if any(kw in body for kw in [
                "Confirmación de datos personales",
                "Aceptar y finalizar",
                "patientPhone",
                "Celular",
            ]):
                pantalla3_ok = True
                break
        except:
            pass
        await page.wait_for_timeout(500)

    if not pantalla3_ok:
        await msg_fn("❌ Pantalla 3 no apareció (timeout 7.5s)")
        return False

    await msg_fn("📋 Pantalla 3 cargada — buscando campo celular...")

    # 2. Buscar campo Celular
    # SELECTOR EXACTO según el HTML inspeccionado:
    # <input placeholder="Celular" name="patientPhone" type="text">
    SELECTORES_CEL = [
        'input[name="patientPhone"]',       # ← EXACTO del inspector HTML
        'input[placeholder="Celular"]',     # ← por placeholder exacto
        'input[placeholder*="Celular"]',    # ← parcial
        'input[placeholder*="elular"]',     # ← sin mayúscula
        'input[name="celular"]',
        'input[name="cel"]',
        'input[name="fono"]',
        'input[name="telefono"]',
        'input[name="phone"]',
        'input[type="tel"]',
    ]

    campo_cel = None
    selector_usado = None
    for sel in SELECTORES_CEL:
        campo = await page.query_selector(sel)
        if campo and await campo.is_visible():
            campo_cel = campo
            selector_usado = sel
            break

    # Fallback: buscar input editable que no sea nombre/apellido/correo
    if not campo_cel:
        await msg_fn("⚠️ Selector directo falló — buscando por fallback...")
        inputs = await page.query_selector_all("input")
        for inp in inputs:
            try:
                if not await inp.is_visible():
                    continue
                ph  = (await inp.get_attribute("placeholder") or "").lower()
                nm  = (await inp.get_attribute("name")        or "").lower()
                ro  = await inp.get_attribute("readonly")
                dis = await inp.get_attribute("disabled")
                # Buscar el que tenga placeholder "celular" o name con "phone"
                # y no sea readonly ni disabled
                if ro is not None or dis is not None:
                    continue
                if "celular" in ph or "phone" in nm or "fono" in nm or "tel" in nm:
                    campo_cel = inp
                    selector_usado = f"fallback: ph={ph}, nm={nm}"
                    break
            except:
                continue

    if not campo_cel:
        await msg_fn("❌ Campo celular no encontrado — tomando screenshot para debug")
        return False

    await msg_fn(f"✅ Campo celular encontrado: {selector_usado}")

    # 3. Rellenar campo Celular
    try:
        await campo_cel.click()
        await page.wait_for_timeout(200)
        await campo_cel.fill("")                    # limpiar
        await campo_cel.type(tel, delay=50)         # escribir natural
        await page.wait_for_timeout(400)

        # Verificar que se escribió correctamente
        valor = await campo_cel.input_value()
        await msg_fn(f"📱 Celular ingresado: {valor}")

        if valor != tel:
            await msg_fn(f"⚠️ Valor esperado: {tel}, valor real: {valor} — reintentando")
            await campo_cel.triple_click()
            await campo_cel.fill(tel)
            await page.wait_for_timeout(300)

    except Exception as e:
        await msg_fn(f"❌ Error al ingresar celular: {e}")
        return False

    # 4. Click en "Aceptar y finalizar"
    await page.wait_for_timeout(300)
    finalizar_ok = False
    for texto_btn in ["Aceptar y finalizar", "Aceptar", "Finalizar", "Confirmar"]:
        btn = await page.query_selector(f'button:has-text("{texto_btn}")')
        if not btn:
            btn = await page.query_selector(f'a:has-text("{texto_btn}")')
        if btn:
            try:
                if await btn.get_attribute("disabled") is None:
                    await btn.click()
                    finalizar_ok = True
                    await msg_fn(f"🖱️ Click en '{texto_btn}'")
                    break
            except Exception as e:
                await msg_fn(f"⚠️ Error clickeando '{texto_btn}': {e}")
                continue

    if not finalizar_ok:
        await msg_fn("❌ Botón 'Aceptar y finalizar' no encontrado")
        return False

    # 5. Verificar Pantalla 4 (éxito)
    await page.wait_for_timeout(3000)
    body_final = (await page.inner_text("body")).upper()

    if any(kw in body_final for kw in [
        "AGENDADO CON EXITO",
        "SU HORA SE HA AGENDADO",
        "HORA SE HA AGENDADO",
        "AGENDADO",
    ]):
        return True

    await msg_fn(f"⚠️ Pantalla 4 inesperada:\n{body_final[:300]}")
    return False


# ═══════════════════════════════════════════════════════════════
# MOTOR DE RESERVA
# ═══════════════════════════════════════════════════════════════

async def motor_reserva(
    reserva: dict,
    numero: int,
    chat_id: int,
    ctx: ContextTypes.DEFAULT_TYPE,
    uid: int,
    test: bool = False,
) -> str:
    rut       = reserva["rut"]
    hora_pref = reserva["pref"]
    hora_alt  = reserva["alt"]
    tel       = reserva["tel"]
    tag       = f"[R{numero}]"
    ganadas   = horas_ganadas.setdefault(uid, set())

    async def msg(texto):
        try:
            await ctx.bot.send_message(chat_id, f"{tag} {texto}")
        except:
            pass

    async def foto(page, caption):
        try:
            ss = await page.screenshot(full_page=True)
            await ctx.bot.send_photo(chat_id, ss, caption=f"{tag} {caption}")
        except:
            pass

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu"],
            )
            page = await browser.new_page(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page.set_default_timeout(15000)

            # ── PANTALLA 1: Abrir sitio ──────────────────────────────────────
            await msg("🌐 Abriendo página...")
            await page.goto(URL, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

            # ── PANTALLA 1: Ingresar y validar RUT ───────────────────────────
            await msg(f"🪪 RUT: {fmt(rut)}")
            if not await _ingresar_rut(page, rut):
                await foto(page, "ERROR: Campo RUT no encontrado")
                await browser.close()
                return "ERROR: campo RUT no encontrado"

            if not await _click_validar(page):
                await foto(page, "ERROR: Botón Validar no encontrado")
                await browser.close()
                return "ERROR: botón Validar no encontrado"

            await page.wait_for_timeout(3000)

            # Verificar si el RUT fue aceptado
            rut_sigue = await page.query_selector('input[name="rut"]')
            if rut_sigue and await rut_sigue.is_visible():
                await foto(page, "RUT RECHAZADO")
                await browser.close()
                return "ERROR: RUT rechazado"

            await msg("✅ RUT validado")
            horas = await _listar_horas_visibles(page)
            if horas:
                await msg("Horas:\n" + "\n".join(horas[:12]))

            # ── PANTALLA 2: Loop búsqueda de hora ────────────────────────────
            fin = (
                ahora() + datetime.timedelta(minutes=5)
                if test
                else ahora().replace(hour=18, minute=10, second=0, microsecond=0)
            )
            if not test and fin <= ahora():
                fin += datetime.timedelta(days=1)

            intentos = 0
            while ahora() < fin:
                intentos += 1

                # Auto-recovery
                rut_visible = await page.query_selector('input[name="rut"]')
                if rut_visible and await rut_visible.is_visible():
                    await msg("🔄 Reingresando RUT...")
                    await _ingresar_rut(page, rut)
                    await _click_validar(page)
                    await page.wait_for_timeout(3000)
                    continue

                # Buscar hora
                for hora_int in [hora_pref, hora_alt]:
                    btn, texto = await _buscar_boton_hora(page, hora_int, ganadas)
                    if not btn:
                        continue

                    await msg(f"🎾 {texto} DISPONIBLE! (intento {intentos})")
                    ganadas.add(hora_int)

                    await btn.click()
                    await page.wait_for_timeout(600)
                    await foto(page, f"P2 — {texto} seleccionada")

                    # Click "Ok, programar"
                    ok = await _click_boton(
                        page, ["Ok, programar", "Ok, Programar", "OK", "Programar"]
                    )
                    if not ok:
                        await foto(page, "ERROR: Ok, programar no encontrado")
                        ganadas.discard(hora_int)
                        continue

                    await page.wait_for_timeout(1000)
                    await foto(page, "P2→P3 — cargando confirmación")

                    # ── PANTALLA 3 ────────────────────────────────────────────
                    exito = await _completar_pantalla3(page, tel, msg)

                    if exito:
                        await foto(page, f"✅ RESERVA EXITOSA — {texto}")
                        await browser.close()
                        return f"✅ RESERVA CONFIRMADA — {texto} — {fmt(rut)}"
                    else:
                        await foto(page, "❌ Error en Pantalla 3")
                        await browser.close()
                        return f"❌ Falló Pantalla 3 — {texto}"

                if intentos % 30 == 0:
                    modo = (
                        "ULTRA"    if sleep_dinamico() <= 0.5 else
                        "AGRESIVO" if sleep_dinamico() <= 1.0 else
                        "NORMAL"
                    )
                    await msg(f"🔄 Intento {intentos} | {ahora().strftime('%H:%M:%S')} | {modo}")

                await asyncio.sleep(sleep_dinamico())

            await foto(page, "⏸️ TIEMPO AGOTADO")
            await browser.close()
            return f"⏸️ AGOTADO — {intentos} intentos"

    except Exception as e:
        logger.exception(f"[R{numero}] Error fatal")
        try:
            await ctx.bot.send_message(chat_id, f"{tag} ❌ ERROR FATAL: {str(e)[:200]}")
        except:
            pass
        return f"ERROR FATAL: {str(e)[:100]}"


# ═══════════════════════════════════════════════════════════════
# ORQUESTADOR
# ═══════════════════════════════════════════════════════════════

async def orquestar(uid: int, chat_id: int, ctx, test=False):
    c = configs[uid]
    horas_ganadas[uid] = set()
    estado[uid] = "ejecutando"

    await ctx.bot.send_message(
        chat_id,
        f"🎾 DOBLE RESERVA {'TEST' if test else 'REAL'}\n\n"
        f"R1 → {c['r1']['pref']}:00 / {c['r1']['alt']}:00 | {fmt(c['r1']['rut'])}\n"
        f"R2 → {c['r2']['pref']}:00 / {c['r2']['alt']}:00 | {fmt(c['r2']['rut'])}\n"
        f"Hora Chile: {ahora().strftime('%H:%M:%S')}",
    )

    resultados = await asyncio.gather(
        motor_reserva(c["r1"], 1, chat_id, ctx, uid, test),
        motor_reserva(c["r2"], 2, chat_id, ctx, uid, test),
        return_exceptions=True,
    )

    estado[uid] = "idle"
    resumen = "📋 RESUMEN FINAL\n\n"
    for i, r in enumerate(resultados, 1):
        resumen += f"R{i}: {r}\n"
    await ctx.bot.send_message(chat_id, resumen)


async def _esperar_y_ejecutar(uid, chat_id, ctx, test):
    if not test:
        inicio = ahora().replace(hour=17, minute=55, second=0, microsecond=0)
        if ahora() > ahora().replace(hour=18, minute=10):
            inicio += datetime.timedelta(days=1)
        if ahora() < inicio:
            espera = int((inicio - ahora()).total_seconds())
            await ctx.bot.send_message(
                chat_id, f"⏳ Esperando {espera // 60}m hasta las 17:55..."
            )
            await asyncio.sleep(espera)
    await orquestar(uid, chat_id, ctx, test)


# ═══════════════════════════════════════════════════════════════
# COMANDOS
# ═══════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎾 Bot Reserva Doble v4.7\n\n"
        "/config  → configurar RUTs y horas\n"
        "/ver     → ver configuración\n"
        "/test1   → test solo R1 (5 min)\n"
        "/test2   → test solo R2 (5 min)\n"
        "/test    → test ambos (5 min)\n"
        "/auto    → modo real (espera 17:55)\n"
        "/detener → parar\n"
        "/reset   → reiniciar si se traba\n"
        "/status  → estado actual",
    )

async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in tareas:
        for t in tareas[uid]:
            t.cancel()
        tareas.pop(uid, None)
    estado[uid] = "idle"
    horas_ganadas.pop(uid, None)
    await update.message.reply_text("✅ Reset completo. Usa /config para configurar.")
    return ConversationHandler.END

async def cmd_ver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c = configs.get(uid, {})
    if "r2" not in c or "tel" not in c.get("r2", {}):
        return await update.message.reply_text("Incompleto. Usa /config")
    r1, r2 = c["r1"], c["r2"]
    await update.message.reply_text(
        f"R1 → {r1['pref']}:00 / {r1['alt']}:00 | {fmt(r1['rut'])} | {r1['tel']}\n"
        f"R2 → {r2['pref']}:00 / {r2['alt']}:00 | {fmt(r2['rut'])} | {r2['tel']}"
    )

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    est = estado.get(uid, "idle")
    await update.message.reply_text(
        f"Estado: {est}\nHora Chile aprox: {ahora().strftime('%H:%M:%S')}"
    )

async def cmd_detener(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in tareas:
        for t in tareas[uid]:
            t.cancel()
        tareas.pop(uid, None)
        estado[uid] = "idle"
        horas_ganadas.pop(uid, None)
    await update.message.reply_text("⏸️ Detenido")

async def cmd_auto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if "r2" not in configs.get(uid, {}) or "tel" not in configs[uid].get("r2", {}):
        return await update.message.reply_text("Configuración incompleta. Usa /config")
    tarea = asyncio.create_task(
        _esperar_y_ejecutar(uid, update.effective_chat.id, ctx, test=False)
    )
    tareas[uid] = [tarea]
    await update.message.reply_text("🤖 Modo real activado — esperando 17:55")

async def cmd_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if "r2" not in configs.get(uid, {}) or "tel" not in configs[uid].get("r2", {}):
        return await update.message.reply_text("Configuración incompleta. Usa /config")
    tarea = asyncio.create_task(
        _esperar_y_ejecutar(uid, update.effective_chat.id, ctx, test=True)
    )
    tareas[uid] = [tarea]
    await update.message.reply_text("🧪 Test ambos — 5 minutos")

async def cmd_test1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c = configs.get(uid, {})
    if "r1" not in c or "tel" not in c.get("r1", {}):
        return await update.message.reply_text("R1 no configurado. Usa /config")
    tarea = asyncio.create_task(
        motor_reserva(c["r1"], 1, update.effective_chat.id, ctx, uid, test=True)
    )
    tareas[uid] = [tarea]
    await update.message.reply_text("🧪 Test R1 — 5 minutos")

async def cmd_test2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c = configs.get(uid, {})
    if "r2" not in c or "tel" not in c.get("r2", {}):
        return await update.message.reply_text("R2 no configurado. Usa /config")
    tarea = asyncio.create_task(
        motor_reserva(c["r2"], 2, update.effective_chat.id, ctx, uid, test=True)
    )
    tareas[uid] = [tarea]
    await update.message.reply_text("🧪 Test R2 — 5 minutos")


# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN — ConversationHandler
# ═══════════════════════════════════════════════════════════════

async def cfg_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚙️ Configuración\n\n"
        "RUT Reserva 1\n"
        "Ejemplo: 12345678-9"
    )
    return E_RUT1

async def cfg_rut1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        rut = validar_rut(update.message.text)
        if not rut:
            await update.message.reply_text("❌ RUT inválido. Ejemplo: 12345678-9")
            return E_RUT1
        configs[update.effective_user.id] = {"r1": {"rut": rut}, "r2": {}}
        await update.message.reply_text(
            f"✅ RUT1: {fmt(rut)}\n\n"
            "Hora preferida R1\n"
            "Solo número (ej: 9 para las 9:00)"
        )
        return E_HORA1
    except Exception as e:
        logger.error(f"cfg_rut1: {e}")
        await update.message.reply_text("❌ Error. Intenta de nuevo:")
        return E_RUT1

async def cfg_h1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = parsear_hora(update.message.text)
        if h is None:
            await update.message.reply_text("❌ Hora inválida. Número entre 6 y 23 (ej: 9):")
            return E_HORA1
        configs[update.effective_user.id]["r1"]["pref"] = h
        await update.message.reply_text(
            f"✅ Hora preferida R1: {h}:00\n\nHora alternativa R1 (ej: 10):"
        )
        return E_HORA1_ALT
    except Exception as e:
        logger.error(f"cfg_h1: {e}")
        await update.message.reply_text(f"❌ Error: {e}\nEjemplo: 9")
        return E_HORA1

async def cfg_h1a(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = parsear_hora(update.message.text)
        if h is None:
            await update.message.reply_text("❌ Hora inválida. Número entre 6 y 23:")
            return E_HORA1_ALT
        configs[update.effective_user.id]["r1"]["alt"] = h
        await update.message.reply_text(
            f"✅ Hora alternativa R1: {h}:00\n\n"
            "Teléfono R1 (9 dígitos, ej: 912345678):"
        )
        return E_TEL1
    except Exception as e:
        logger.error(f"cfg_h1a: {e}")
        await update.message.reply_text(f"❌ Error: {e}")
        return E_HORA1_ALT

async def cfg_t1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        t = update.message.text.strip().replace(" ", "").replace("+56", "")
        if len(t) != 9 or not t.isdigit():
            await update.message.reply_text("❌ Debe tener 9 dígitos. Ejemplo: 912345678")
            return E_TEL1
        configs[update.effective_user.id]["r1"]["tel"] = t
        await update.message.reply_text(f"✅ Tel R1: {t}\n\nRUT Reserva 2:")
        return E_RUT2
    except Exception as e:
        logger.error(f"cfg_t1: {e}")
        await update.message.reply_text(f"❌ Error: {e}")
        return E_TEL1

async def cfg_rut2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        rut = validar_rut(update.message.text)
        if not rut:
            await update.message.reply_text("❌ RUT inválido:")
            return E_RUT2
        configs[update.effective_user.id]["r2"]["rut"] = rut
        await update.message.reply_text(
            f"✅ RUT2: {fmt(rut)}\n\nHora preferida R2 (ej: 19):"
        )
        return E_HORA2
    except Exception as e:
        logger.error(f"cfg_rut2: {e}")
        await update.message.reply_text(f"❌ Error: {e}")
        return E_RUT2

async def cfg_h2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = parsear_hora(update.message.text)
        if h is None:
            await update.message.reply_text("❌ Hora inválida. Número entre 6 y 23:")
            return E_HORA2
        configs[update.effective_user.id]["r2"]["pref"] = h
        await update.message.reply_text(f"✅ {h}:00\n\nHora alternativa R2:")
        return E_HORA2_ALT
    except Exception as e:
        logger.error(f"cfg_h2: {e}")
        await update.message.reply_text(f"❌ Error: {e}")
        return E_HORA2

async def cfg_h2a(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = parsear_hora(update.message.text)
        if h is None:
            await update.message.reply_text("❌ Hora inválida. Número entre 6 y 23:")
            return E_HORA2_ALT
        configs[update.effective_user.id]["r2"]["alt"] = h
        await update.message.reply_text(f"✅ {h}:00\n\nTeléfono R2 (9 dígitos):")
        return E_TEL2
    except Exception as e:
        logger.error(f"cfg_h2a: {e}")
        await update.message.reply_text(f"❌ Error: {e}")
        return E_HORA2_ALT

async def cfg_t2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        t = update.message.text.strip().replace(" ", "").replace("+56", "")
        if len(t) != 9 or not t.isdigit():
            await update.message.reply_text("❌ Debe tener 9 dígitos:")
            return E_TEL2
        uid = update.effective_user.id
        configs[uid]["r2"]["tel"] = t
        r1, r2 = configs[uid]["r1"], configs[uid]["r2"]
        await update.message.reply_text(
            "✅ CONFIGURACIÓN COMPLETA\n\n"
            f"R1 → {r1['pref']}:00 / {r1['alt']}:00 | {fmt(r1['rut'])} | {r1['tel']}\n"
            f"R2 → {r2['pref']}:00 / {r2['alt']}:00 | {fmt(r2['rut'])} | {r2['tel']}\n\n"
            "Usa /test1 para probar R1\n"
            "Usa /auto para reserva real"
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"cfg_t2: {e}")
        await update.message.reply_text(f"❌ Error: {e}")
        return E_TEL2


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("ver",     cmd_ver))
    app.add_handler(CommandHandler("status",  cmd_status))
    app.add_handler(CommandHandler("auto",    cmd_auto))
    app.add_handler(CommandHandler("test",    cmd_test))
    app.add_handler(CommandHandler("test1",   cmd_test1))
    app.add_handler(CommandHandler("test2",   cmd_test2))
    app.add_handler(CommandHandler("detener", cmd_detener))
    app.add_handler(CommandHandler("reset",   cmd_reset))

    conv = ConversationHandler(
        entry_points=[CommandHandler("config", cfg_inicio)],
        states={
            E_RUT1:      [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_rut1)],
            E_HORA1:     [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_h1)],
            E_HORA1_ALT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_h1a)],
            E_TEL1:      [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_t1)],
            E_RUT2:      [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_rut2)],
            E_HORA2:     [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_h2)],
            E_HORA2_ALT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_h2a)],
            E_TEL2:      [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_t2)],
        },
        fallbacks=[CommandHandler("reset", cmd_reset)],
        per_message=False,
    )
    app.add_handler(conv)

    logger.info("Bot Reserva Doble v4.7 — LISTO")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
