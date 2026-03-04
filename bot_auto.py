"""
Bot Reserva Doble — Canchas Las Condes
v4.5 · Marzo 2026
- Pantalla 3: campo Celular SIEMPRE vacío, se rellena obligatoriamente
- Flujo: P1 (RUT) → P2 (hora) → P3 (celular + confirmar) → P4 (éxito)
"""

import asyncio
import datetime
import os
import logging
from zoneinfo import ZoneInfo

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
CHILE_TZ = ZoneInfo("America/Santiago")

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

def ahora():
    return datetime.datetime.now(CHILE_TZ)

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

def formatos_hora(hora_int: int) -> list:
    f1 = f"{hora_int}:00"
    f2 = f"{hora_int:02d}:00"
    res = [f1]
    if f2 != f1:
        res.append(f2)
    res.append(f"{f1} hrs")
    if f2 != f1:
        res.append(f"{f2} hrs")
    return res

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
    formatos = formatos_hora(hora_int)
    if all(f in ganadas for f in formatos):
        return None, None
    botones = await page.query_selector_all("button")
    for btn in botones:
        try:
            texto = (await btn.inner_text()).strip()
            if texto not in formatos:
                continue
            if texto in ganadas:
                continue
            if await btn.get_attribute("disabled") is not None:
                continue
            clase = (await btn.get_attribute("class") or "").lower()
            if "disabled" in clase or "unavailable" in clase:
                continue
            return btn, texto
        except:
            continue
    return None, None

async def _listar_horas_visibles(page) -> list:
    horas = []
    botones = await page.query_selector_all("button")
    for btn in botones:
        try:
            texto = (await btn.inner_text()).strip()
            if ":00" in texto and len(texto) <= 12:
                disabled = await btn.get_attribute("disabled")
                est = "SOLD OUT" if disabled is not None else "AVAILABLE"
                horas.append(f"{est} {texto}")
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
# Campo Celular SIEMPRE aparece vacío → rellenar obligatoriamente
# ═══════════════════════════════════════════════════════════════

async def _completar_pantalla3(page, tel: str, msg_fn) -> bool:
    """
    Flujo real Pantalla 3:
      1. Esperar que cargue el formulario de confirmación
      2. Rellenar campo Celular (siempre vacío)
      3. Click en 'Aceptar y finalizar'
      4. Verificar texto de éxito en Pantalla 4
    """

    # ── 1. Esperar Pantalla 3 ────────────────────────────────────────────────
    await msg_fn("⏳ Esperando Pantalla 3...")
    pantalla3_ok = False
    for _ in range(15):                         # hasta 7.5 segundos
        try:
            body = await page.inner_text("body")
            if any(kw in body for kw in [
                "Confirmación de datos personales",
                "Aceptar y finalizar",
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

    await msg_fn("📋 Pantalla 3 OK — rellenando celular...")

    # ── 2. Rellenar campo Celular ────────────────────────────────────────────
    # El campo siempre está vacío. Se prueban selectores en orden de
    # probabilidad según el HTML típico de este tipo de formulario Angular.
    SELECTORES_CEL = [
        'input[name="celular"]',
        'input[name="cel"]',
        'input[name="fono"]',
        'input[name="telefono"]',
        'input[name="phone"]',
        'input[type="tel"]',
        'input[placeholder*="Celular"]',
        'input[placeholder*="celular"]',
        'input[placeholder*="Teléfono"]',
        'input[placeholder*="9"]',
    ]

    campo_cel = None
    for sel in SELECTORES_CEL:
        campo = await page.query_selector(sel)
        if campo and await campo.is_visible():
            campo_cel = campo
            break

    # Fallback: recorrer todos los inputs del formulario
    if not campo_cel:
        inputs = await page.query_selector_all("input")
        for inp in inputs:
            try:
                if not await inp.is_visible():
                    continue
                tp = (await inp.get_attribute("type")        or "").lower()
                ph = (await inp.get_attribute("placeholder") or "").lower()
                nm = (await inp.get_attribute("name")        or "").lower()
                if tp in ["tel", "number"] or any(
                    k in ph or k in nm
                    for k in ["cel", "tel", "fono", "phone"]
                ):
                    campo_cel = inp
                    break
            except:
                continue

    if not campo_cel:
        await msg_fn("❌ Campo Celular no encontrado en Pantalla 3")
        return False

    # Rellenar: limpiar primero, luego escribir dígito a dígito
    try:
        await campo_cel.click()
        await campo_cel.fill("")                    # limpiar por si acaso
        await campo_cel.type(tel, delay=50)         # escribir natural
        await page.wait_for_timeout(400)
        await msg_fn(f"📱 Celular ingresado: {tel}")
    except Exception as e:
        await msg_fn(f"❌ Error al ingresar celular: {e}")
        return False

    # ── 3. Click en "Aceptar y finalizar" ───────────────────────────────────
    # Según la imagen real de la Pantalla 3, el botón se llama exactamente
    # "Aceptar y finalizar". También se prueban variantes por seguridad.
    finalizar_ok = False
    for texto_btn in ["Aceptar y finalizar", "Aceptar", "Finalizar", "Confirmar"]:
        # Buscar como <button>
        btn = await page.query_selector(f'button:has-text("{texto_btn}")')
        # Fallback como <a>
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

    # ── 4. Verificar Pantalla 4 (éxito) ─────────────────────────────────────
    await page.wait_for_timeout(3000)

    body_final = (await page.inner_text("body")).upper()
    if any(kw in body_final for kw in [
        "AGENDADO CON EXITO",
        "SU HORA SE HA AGENDADO",
        "HORA SE HA AGENDADO",
        "AGENDADO",
    ]):
        return True

    # Si no aparece texto de éxito, mostrar qué dice la página para debug
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
            ss = await page.screenshot()
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
                viewport={"width": 390, "height": 844},
                user_agent=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                    "AppleWebKit/605.1.15"
                ),
            )
            page.set_default_timeout(15000)

            # ── PANTALLA 1: Abrir sitio ──────────────────────────────────────
            await msg("🌐 Abriendo página...")
            await page.goto(URL, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

            # ── PANTALLA 1: Ingresar y validar RUT ───────────────────────────
            await msg(f"🪪 Ingresando RUT {fmt(rut)}...")
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
                return "ERROR: RUT rechazado (verifica que sea vecino de Las Condes)"

            await msg("✅ RUT validado")
            horas = await _listar_horas_visibles(page)
            if horas:
                await msg("Horas en pantalla:\n" + "\n".join(horas[:12]))

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

                # Auto-recovery: si la página volvió a pedir el RUT
                rut_visible = await page.query_selector('input[name="rut"]')
                if rut_visible and await rut_visible.is_visible():
                    await msg("🔄 Página reiniciada → reingresando RUT")
                    await _ingresar_rut(page, rut)
                    await _click_validar(page)
                    await page.wait_for_timeout(3000)
                    continue

                # Buscar hora preferida, luego alternativa
                for hora_int in [hora_pref, hora_alt]:
                    btn, texto = await _buscar_boton_hora(page, hora_int, ganadas)
                    if not btn:
                        continue

                    await msg(f"🎾 {texto} DISPONIBLE! (intento {intentos})")
                    for f in formatos_hora(hora_int):
                        ganadas.add(f)

                    # Click en hora
                    await btn.click()
                    await page.wait_for_timeout(600)
                    await foto(page, f"P2 — hora {texto} seleccionada")

                    # Click "Ok, programar" → abre Pantalla 3
                    ok = await _click_boton(
                        page, ["Ok, programar", "Ok, Programar", "OK", "Programar"]
                    )
                    if not ok:
                        await foto(page, "ERROR: 'Ok, programar' no encontrado")
                        await msg("❌ 'Ok, programar' no encontrado — reintentando")
                        for f in formatos_hora(hora_int):
                            ganadas.discard(f)
                        continue

                    await page.wait_for_timeout(800)
                    await foto(page, "P2 → P3 — cargando confirmación")

                    # ── PANTALLA 3: Celular + Aceptar y finalizar ─────────────
                    exito = await _completar_pantalla3(page, tel, msg)

                    if exito:
                        # ── PANTALLA 4: ¡Reserva exitosa! ─────────────────────
                        await foto(page, f"✅ RESERVA EXITOSA — {texto} — {fmt(rut)}")
                        await browser.close()
                        return f"✅ RESERVA CONFIRMADA — {texto} — {fmt(rut)}"
                    else:
                        await foto(page, f"❌ Error Pantalla 3 — {texto}")
                        await browser.close()
                        return f"❌ Falló en Pantalla 3 — {texto}"

                # Log de progreso cada 30 intentos (~1 min)
                if intentos % 30 == 0:
                    modo = (
                        "ULTRA"    if sleep_dinamico() <= 0.5 else
                        "AGRESIVO" if sleep_dinamico() <= 1.0 else
                        "NORMAL"
                    )
                    await msg(
                        f"🔄 Intento {intentos} | {ahora().strftime('%H:%M:%S')} | {modo}"
                    )

                await asyncio.sleep(sleep_dinamico())

            # Fin de ventana sin éxito
            await foto(page, "⏸️ TIEMPO AGOTADO")
            await browser.close()
            return f"⏸️ AGOTADO — {intentos} intentos sin disponibilidad"

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
        "🎾 Bot Reserva Doble v4.5\n\n"
        "/config  → configurar RUTs y horas\n"
        "/ver     → ver configuración\n"
        "/test    → test ambas reservas (5 min)\n"
        "/test1   → solo R1\n"
        "/test2   → solo R2\n"
        "/auto    → modo real (espera 17:55)\n"
        "/detener → parar\n"
        "/status  → estado actual",
    )

async def cmd_ver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c = configs.get(uid, {})
    if "r2" not in c or "tel" not in c.get("r2", {}):
        return await update.message.reply_text("Configuración incompleta. Usa /config")
    r1, r2 = c["r1"], c["r2"]
    await update.message.reply_text(
        f"R1 → {r1['pref']}:00 / {r1['alt']}:00 | {fmt(r1['rut'])}\n"
        f"R2 → {r2['pref']}:00 / {r2['alt']}:00 | {fmt(r2['rut'])}"
    )

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    est = estado.get(uid, "idle")
    await update.message.reply_text(
        f"Estado: {est}\nHora Chile: {ahora().strftime('%H:%M:%S')}"
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
    await update.message.reply_text("🧪 Test ambos — 5 minutos de búsqueda")

async def cmd_test1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c = configs.get(uid, {})
    if "r1" not in c or "tel" not in c.get("r1", {}):
        return await update.message.reply_text("R1 no configurado. Usa /config")
    tarea = asyncio.create_task(
        motor_reserva(c["r1"], 1, update.effective_chat.id, ctx, uid, test=True)
    )
    tareas[uid] = [tarea]
    await update.message.reply_text("🧪 Test R1 iniciado — 5 minutos")

async def cmd_test2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c = configs.get(uid, {})
    if "r2" not in c or "tel" not in c.get("r2", {}):
        return await update.message.reply_text("R2 no configurado. Usa /config")
    tarea = asyncio.create_task(
        motor_reserva(c["r2"], 2, update.effective_chat.id, ctx, uid, test=True)
    )
    tareas[uid] = [tarea]
    await update.message.reply_text("🧪 Test R2 iniciado — 5 minutos")


# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN — ConversationHandler
# ═══════════════════════════════════════════════════════════════

async def cfg_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("RUT Reserva 1 (ej: 12345678-K):")
    return E_RUT1

async def cfg_rut1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rut = validar_rut(update.message.text)
    if not rut:
        await update.message.reply_text("❌ RUT inválido. Intenta de nuevo:")
        return E_RUT1
    configs[update.effective_user.id] = {"r1": {"rut": rut}, "r2": {}}
    await update.message.reply_text(f"✅ RUT1: {fmt(rut)}\n\nHora preferida R1 (ej: 19):")
    return E_HORA1

async def cfg_h1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = int(update.message.text.strip())
        configs[update.effective_user.id]["r1"]["pref"] = h
        await update.message.reply_text(f"✅ {h}:00\n\nHora alternativa R1:")
        return E_HORA1_ALT
    except:
        await update.message.reply_text("❌ Número inválido")
        return E_HORA1

async def cfg_h1a(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = int(update.message.text.strip())
        configs[update.effective_user.id]["r1"]["alt"] = h
        await update.message.reply_text(
            f"✅ {h}:00\n\nTeléfono R1 (9 dígitos, ej: 912345678):"
        )
        return E_TEL1
    except:
        await update.message.reply_text("❌ Número inválido")
        return E_HORA1_ALT

async def cfg_t1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip().replace(" ", "").replace("+56", "")
    if len(t) != 9 or not t.isdigit():
        await update.message.reply_text("❌ Debe tener 9 dígitos (ej: 912345678):")
        return E_TEL1
    configs[update.effective_user.id]["r1"]["tel"] = t
    await update.message.reply_text(f"✅ Tel R1: {t}\n\nRUT Reserva 2:")
    return E_RUT2

async def cfg_rut2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rut = validar_rut(update.message.text)
    if not rut:
        await update.message.reply_text("❌ RUT inválido:")
        return E_RUT2
    configs[update.effective_user.id]["r2"]["rut"] = rut
    await update.message.reply_text(f"✅ RUT2: {fmt(rut)}\n\nHora preferida R2:")
    return E_HORA2

async def cfg_h2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = int(update.message.text.strip())
        configs[update.effective_user.id]["r2"]["pref"] = h
        await update.message.reply_text(f"✅ {h}:00\n\nHora alternativa R2:")
        return E_HORA2_ALT
    except:
        await update.message.reply_text("❌ Número inválido")
        return E_HORA2

async def cfg_h2a(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = int(update.message.text.strip())
        configs[update.effective_user.id]["r2"]["alt"] = h
        await update.message.reply_text(f"✅ {h}:00\n\nTeléfono R2 (9 dígitos):")
        return E_TEL2
    except:
        await update.message.reply_text("❌ Número inválido")
        return E_HORA2_ALT

async def cfg_t2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip().replace(" ", "").replace("+56", "")
    if len(t) != 9 or not t.isdigit():
        await update.message.reply_text("❌ Debe tener 9 dígitos:")
        return E_TEL2
    uid = update.effective_user.id
    configs[uid]["r2"]["tel"] = t
    r1, r2 = configs[uid]["r1"], configs[uid]["r2"]
    await update.message.reply_text(
        "✅ CONFIGURACIÓN COMPLETA\n\n"
        f"R1 → {r1['pref']}:00 / {r1['alt']}:00 | {fmt(r1['rut'])}\n"
        f"R2 → {r2['pref']}:00 / {r2['alt']}:00 | {fmt(r2['rut'])}\n\n"
        "Usa /test1 o /test2 para probar\n"
        "Usa /auto para el día real"
    )
    return ConversationHandler.END


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
        fallbacks=[],
        per_message=False,
    )
    app.add_handler(conv)

    logger.info("Bot Reserva Doble v4.5 — LISTO")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
