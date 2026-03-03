"""
Bot Reserva Doble — Canchas Las Condes
v3.0 Final · Arquitectura: Telegram + Playwright (2 browsers paralelos)
Deploy: Railway + Docker
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

# Estados de conversación /config
(
    E_RUT1, E_HORA1, E_HORA1_ALT, E_TEL1,
    E_RUT2, E_HORA2, E_HORA2_ALT, E_TEL2,
) = range(8)

# Estado global en memoria
configs = {}          # user_id → {"r1": {...}, "r2": {...}}
tareas = {}           # user_id → [asyncio.Task]
estado = {}           # user_id → "idle" | "esperando" | "ejecutando" | "completado"
horas_ganadas = {}    # user_id → set() — coordinación entre R1 y R2


# ═══════════════════════════════════════════════════════════════
# UTILIDADES
# ═══════════════════════════════════════════════════════════════

def ahora():
    """Hora actual en Chile (independiente del server de Railway)."""
    return datetime.datetime.now(CHILE_TZ)


def calcular_dv(cuerpo: str) -> str:
    """Dígito verificador de RUT chileno."""
    s, m = 0, 2
    for d in reversed(cuerpo):
        s += int(d) * m
        m = m + 1 if m < 7 else 2
    r = 11 - (s % 11)
    return {11: "0", 10: "K"}.get(r, str(r))


def validar_rut(texto: str):
    """Valida RUT. Retorna string limpio o None."""
    rut = texto.replace(".", "").replace("-", "").replace(" ", "").upper()
    if len(rut) < 2 or not rut[:-1].isdigit():
        return None
    return rut if calcular_dv(rut[:-1]) == rut[-1] else None


def fmt(rut: str) -> str:
    """Formatea RUT para mostrar: 12345678-K"""
    return f"{rut[:-1]}-{rut[-1]}"


def sleep_dinamico() -> float:
    """
    Retorna segundos de espera según la hora actual.
    Más agresivo entre 17:59:45 y 18:01:30.
    """
    h = ahora()
    hr, mn, sg = h.hour, h.minute, h.second

    # 🔥 ULTRA AGRESIVO: 17:59:45 → 18:01:30  (0.5s = 2 intentos/seg)
    if (hr == 17 and mn == 59 and sg >= 45) or \
       (hr == 18 and mn == 0) or \
       (hr == 18 and mn == 1 and sg <= 30):
        return 0.5

    # ⚡ AGRESIVO: 17:59:00 → 17:59:45 y 18:01:30 → 18:05:00  (1s)
    if (hr == 17 and mn == 59) or (hr == 18 and mn <= 5):
        return 1.0

    # ⏳ NORMAL: resto del tiempo (2s)
    return 2.0


# ═══════════════════════════════════════════════════════════════
# HELPERS PLAYWRIGHT (selectores robustos)
# ═══════════════════════════════════════════════════════════════

async def _ingresar_rut(page, rut_raw: str) -> bool:
    """Busca campo RUT e ingresa el valor. Retorna True si lo encontró."""
    selectores = [
        'input[name="rut"]',
        'input[placeholder*="RUT"]',
        'input[placeholder*="rut"]',
        'input[placeholder*="Rut"]',
        '#rut',
        'input[type="text"]',
    ]
    for sel in selectores:
        campo = await page.query_selector(sel)
        if campo:
            await campo.fill("")
            await campo.type(rut_raw, delay=30)
            return True
    return False


async def _click_validar(page) -> bool:
    """Busca y clickea botón Validar."""
    selectores = [
        'button:has-text("Validar")',
        'button:has-text("VALIDAR")',
        'button:has-text("validar")',
        'button[type="submit"]',
        'input[type="submit"]',
    ]
    for sel in selectores:
        btn = await page.query_selector(sel)
        if btn:
            await btn.click()
            return True
    return False


async def _buscar_campo_tel(page):
    """Busca campo de teléfono con múltiples selectores."""
    selectores = [
        'input[type="tel"]',
        'input[name*="tel"]',
        'input[name*="fono"]',
        'input[name*="cel"]',
        'input[name*="phone"]',
        'input[placeholder*="9"]',
        'input[placeholder*="Tel"]',
        'input[placeholder*="tel"]',
        'input[placeholder*="Cel"]',
        'input[placeholder*="fono"]',
    ]
    for sel in selectores:
        campo = await page.query_selector(sel)
        if campo:
            return campo

    # Fallback: primer input visible que no sea hidden/submit
    try:
        inputs = await page.query_selector_all("input:visible")
        for inp in inputs:
            tipo = (await inp.get_attribute("type") or "").lower()
            if tipo not in ("hidden", "submit", "button", "checkbox", "radio"):
                return inp
    except Exception:
        pass
    return None


async def _click_boton(page, textos: list) -> bool:
    """Busca y clickea el primer botón que coincida."""
    for texto in textos:
        btn = await page.query_selector(f'button:has-text("{texto}")')
        if btn:
            dis = await btn.get_attribute("disabled")
            if dis is None:
                await btn.click()
                return True
    return False


# ═══════════════════════════════════════════════════════════════
# MOTOR DE RESERVA (1 navegador = 1 reserva)
# ═══════════════════════════════════════════════════════════════

async def motor_reserva(
    reserva: dict,
    numero: int,
    chat_id: int,
    ctx: ContextTypes.DEFAULT_TYPE,
    uid: int,
    test: bool = False,
) -> str:
    """
    Abre UN navegador, valida UN RUT, y busca UNA hora.
    Se ejecutan 2 instancias en paralelo (R1 + R2).
    """

    rut = reserva["rut"]
    h_pref = f"{reserva['pref']}:00"
    h_alt = f"{reserva['alt']}:00"
    tel = reserva["tel"]
    tag = f"[R{numero}]"
    ganadas = horas_ganadas.setdefault(uid, set())

    # Helpers internos
    async def msg(texto):
        try:
            await ctx.bot.send_message(chat_id, f"{tag} {texto}")
        except Exception:
            pass

    async def foto(page, caption):
        try:
            ss = await page.screenshot()
            await ctx.bot.send_photo(chat_id, ss, caption=f"{tag} {caption}")
        except Exception:
            pass

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            page = await browser.new_page()
            page.set_default_timeout(15000)

            # ────────────────────────────
            # PASO 1: ABRIR PÁGINA
            # ────────────────────────────
            await msg("🌐 Abriendo página...")
            await page.goto(URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            # ────────────────────────────
            # PASO 2: INGRESAR RUT
            # ────────────────────────────
            await msg(f"📝 RUT {fmt(rut)}")
            if not await _ingresar_rut(page, rut):
                await foto(page, "❌ Campo RUT no encontrado")
                await browser.close()
                return "ERROR — Campo RUT no encontrado"

            # ────────────────────────────
            # PASO 3: VALIDAR
            # ────────────────────────────
            if not await _click_validar(page):
                await foto(page, "❌ Botón Validar no encontrado")
                await browser.close()
                return "ERROR — Botón Validar no encontrado"

            await page.wait_for_timeout(3000)
            await msg("✅ RUT validado — pantalla de horas")
            await foto(page, "Pantalla post-validación")

            # ────────────────────────────
            # PASO 4: DEFINIR VENTANA
            # ────────────────────────────
            if test:
                fin = ahora() + datetime.timedelta(minutes=5)
                await msg("🧪 TEST — buscando 5 minutos")
            else:
                h = ahora()
                fin = h.replace(hour=18, minute=10, second=0, microsecond=0)
                if fin <= h:
                    fin += datetime.timedelta(days=1)
                await msg(f"⏳ Buscando {h_pref} / {h_alt} hasta {fin.strftime('%H:%M:%S')}")

            # ────────────────────────────────────────────────
            # PASO 5: LOOP DE BÚSQUEDA (SIN RECARGAR PÁGINA)
            # ────────────────────────────────────────────────
            intentos = 0

            while ahora() < fin:
                intentos += 1

                try:
                    # ══ AUTO-RECOVERY ══
                    # Si la página se reinició sola, el campo RUT reaparece
                    campo_rut = await page.query_selector('input[name="rut"]')
                    if campo_rut:
                        try:
                            visible = await campo_rut.is_visible()
                        except Exception:
                            visible = False
                        if visible:
                            await msg("🔄 Página reiniciada — re-ingresando RUT...")
                            await _ingresar_rut(page, rut)
                            await _click_validar(page)
                            await page.wait_for_timeout(3000)
                            await msg("✅ RUT re-validado")
                            continue

                    # ══ BUSCAR HORA DISPONIBLE ══
                    for hora_str in [h_pref, h_alt]:
                        # Saltar si el otro navegador ya ganó esta hora
                        if hora_str in ganadas:
                            continue

                        btn = await page.query_selector(f'button:has-text("{hora_str}")')
                        if not btn:
                            continue

                        # Verificar que NO esté deshabilitado
                        disabled_attr = await btn.get_attribute("disabled")
                        class_attr = (await btn.get_attribute("class") or "").lower()
                        aria = (await btn.get_attribute("aria-disabled") or "").lower()

                        if disabled_attr is not None:
                            continue
                        if "disabled" in class_attr or "disabled" in aria:
                            continue

                        # ═══════════════════════════════════
                        # 🎯 ¡HORA DISPONIBLE — ATACAR!
                        # ═══════════════════════════════════
                        await msg(f"🎯 ¡{hora_str} DISPONIBLE! (intento {intentos})")
                        ganadas.add(hora_str)  # Marcar para que el otro navegador no pelee

                        # Click hora
                        await btn.click()
                        await page.wait_for_timeout(400)
                        await foto(page, f"Click {hora_str}")

                        # Click "Ok, programar"
                        ok = await _click_boton(page, [
                            "Ok, programar", "OK", "Programar",
                            "Confirmar", "Aceptar",
                        ])
                        if not ok:
                            await foto(page, "⚠️ Botón Ok no encontrado — continuando")

                        await page.wait_for_timeout(1000)

                        # Ingresar teléfono
                        campo_tel = await _buscar_campo_tel(page)
                        if campo_tel:
                            await campo_tel.fill("")
                            await campo_tel.type(tel, delay=25)
                            await msg(f"📱 Teléfono {tel} ingresado")
                        else:
                            await foto(page, "⚠️ Campo teléfono no encontrado")

                        await page.wait_for_timeout(300)

                        # Click "Reservar"
                        reservado = await _click_boton(page, [
                            "Reservar", "RESERVAR", "Confirmar reserva",
                        ])

                        if not reservado:
                            # Fallback: submit button
                            sub = await page.query_selector('button[type="submit"]')
                            if sub:
                                await sub.click()
                                reservado = True

                        await page.wait_for_timeout(2000)

                        # Screenshot final
                        await foto(
                            page,
                            f"✅ RESERVA {'ENVIADA' if reservado else 'INTENTADA'}\n"
                            f"Hora: {hora_str}\n"
                            f"RUT: {fmt(rut)}\n"
                            f"Tel: {tel}\n"
                            f"Intento: {intentos}"
                        )

                        await browser.close()
                        return f"EXITOSA — {hora_str} (intento {intentos})"

                    # ══ SLEEP DINÁMICO ══
                    sl = sleep_dinamico()
                    await asyncio.sleep(sl)

                    # ══ REPORTE PERIÓDICO (cada ~60 seg) ══
                    reportar_cada = max(1, int(30 / max(sl, 0.1)))
                    if intentos % reportar_cada == 0:
                        modo = "🔥ULTRA" if sl <= 0.5 else "⚡AGRESIVO" if sl <= 1 else "⏳Normal"
                        await msg(
                            f"Intento {intentos} | "
                            f"{ahora().strftime('%H:%M:%S')} | "
                            f"{modo} ({sl}s)"
                        )

                except asyncio.CancelledError:
                    await msg("🛑 Cancelado por usuario")
                    await browser.close()
                    return "CANCELADO"

                except Exception as e:
                    logger.warning(f"{tag} Error intento {intentos}: {e}")
                    if intentos % 20 == 0:
                        await msg(f"⚠️ Error #{intentos}: {str(e)[:80]}")
                    await asyncio.sleep(2)

            # ── Tiempo agotado ──
            await foto(page, f"⏰ Ventana cerrada — {intentos} intentos realizados")
            await browser.close()
            return f"TIEMPO_AGOTADO — {intentos} intentos"

    except Exception as e:
        logger.error(f"{tag} Error fatal: {e}")
        await msg(f"💥 Error fatal: {str(e)[:200]}")
        return f"ERROR_FATAL — {str(e)[:80]}"


# ═══════════════════════════════════════════════════════════════
# ORQUESTADOR (lanza 2 motores en paralelo)
# ═══════════════════════════════════════════════════════════════

async def orquestar(uid: int, chat_id: int, ctx, test=False):
    """Lanza R1 y R2 en paralelo con asyncio.gather."""
    c = configs[uid]
    horas_ganadas[uid] = set()
    estado[uid] = "ejecutando"

    modo = "🧪 TEST" if test else "🔥 AUTO"
    await ctx.bot.send_message(
        chat_id,
        f"🚀 *DOBLE RESERVA — {modo}*\n\n"
        f"R1: `{fmt(c['r1']['rut'])}` → {c['r1']['pref']}:00 / {c['r1']['alt']}:00\n"
        f"R2: `{fmt(c['r2']['rut'])}` → {c['r2']['pref']}:00 / {c['r2']['alt']}:00\n\n"
        f"🕐 Hora Chile: {ahora().strftime('%H:%M:%S')}",
        parse_mode="Markdown",
    )

    resultados = await asyncio.gather(
        motor_reserva(c["r1"], 1, chat_id, ctx, uid, test),
        motor_reserva(c["r2"], 2, chat_id, ctx, uid, test),
        return_exceptions=True,
    )

    estado[uid] = "completado"

    # Resumen final
    resumen = "📊 *RESUMEN FINAL*\n\n"
    for i, r in enumerate(resultados, 1):
        if isinstance(r, Exception):
            resumen += f"R{i}: ❌ Error — {str(r)[:100]}\n"
        else:
            icono = "✅" if "EXITOSA" in str(r) else "❌"
            resumen += f"R{i}: {icono} {r}\n"
    resumen += f"\n🕐 {ahora().strftime('%H:%M:%S')} Chile"

    await ctx.bot.send_message(chat_id, resumen, parse_mode="Markdown")


async def _esperar_y_ejecutar(uid, chat_id, ctx, test):
    """
    Espera hasta las 17:55 (en background, sin bloquear el bot)
    y después ejecuta la doble reserva.
    """
    if not test:
        h = ahora()
        inicio = h.replace(hour=17, minute=55, second=0, microsecond=0)

        # Si ya pasaron las 18:10, programar para mañana
        limite = h.replace(hour=18, minute=10, second=0, microsecond=0)
        if h > limite:
            inicio += datetime.timedelta(days=1)

        if h < inicio:
            espera = (inicio - h).total_seconds()
            await ctx.bot.send_message(
                chat_id,
                f"⏳ Esperando {int(espera // 60)}m {int(espera % 60)}s hasta 17:55...\n"
                f"Hora Chile actual: {h.strftime('%H:%M:%S')}\n"
                "El bot arrancará solo. No necesitas hacer nada."
            )
            estado[uid] = "esperando"
            await asyncio.sleep(espera)
            await ctx.bot.send_message(chat_id, "🔔 ¡17:55! Arrancando navegadores...")

    await orquestar(uid, chat_id, ctx, test)


# ═══════════════════════════════════════════════════════════════
# COMANDOS TELEGRAM
# ═══════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚽ *Bot Reserva Doble v3.0*\n\n"
        "📋 Comandos:\n"
        "  /config — Configurar 2 reservas\n"
        "  /ver — Ver configuración actual\n"
        "  /auto — Reserva automática (17:55→18:10)\n"
        "  /test — Test inmediato (5 min)\n"
        "  /detener — Detener proceso\n"
        "  /status — Estado actual\n"
        "  /cancelar — Cancelar configuración\n\n"
        "Paso 1: /config\n"
        "Paso 2: /auto (o /test para probar)",
        parse_mode="Markdown",
    )


async def cmd_ver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c = configs.get(uid)
    if not c or "tel" not in c.get("r2", {}):
        return await update.message.reply_text("⚠️ Sin config. Usa /config")
    r1, r2 = c["r1"], c["r2"]
    await update.message.reply_text(
        f"📋 *Configuración*\n\n"
        f"*R1:* `{fmt(r1['rut'])}` → {r1['pref']}:00 / {r1['alt']}:00 | Tel: {r1['tel']}\n"
        f"*R2:* `{fmt(r2['rut'])}` → {r2['pref']}:00 / {r2['alt']}:00 | Tel: {r2['tel']}",
        parse_mode="Markdown",
    )


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    est = estado.get(uid, "idle")
    ts = tareas.get(uid, [])
    activas = sum(1 for t in ts if not t.done())
    await update.message.reply_text(
        f"📊 *Estado:* `{est}`\n"
        f"Tareas activas: {activas}\n"
        f"Hora Chile: {ahora().strftime('%H:%M:%S')}\n"
        f"Config: {'✅' if uid in configs else '❌'}",
        parse_mode="Markdown",
    )


async def cmd_detener(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ts = tareas.pop(uid, [])
    n = 0
    for t in ts:
        if not t.done():
            t.cancel()
            n += 1
    estado[uid] = "idle"
    horas_ganadas.pop(uid, None)
    await update.message.reply_text(f"🛑 {n} tarea(s) detenida(s)")


async def cmd_auto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in configs or "tel" not in configs[uid].get("r2", {}):
        return await update.message.reply_text("⚠️ Usa /config primero")
    if uid in tareas and any(not t.done() for t in tareas[uid]):
        return await update.message.reply_text("⚠️ Ya hay tarea activa. /detener primero")

    tarea = asyncio.create_task(
        _esperar_y_ejecutar(uid, update.effective_chat.id, ctx, test=False)
    )
    tareas[uid] = [tarea]
    await update.message.reply_text(
        "✅ *Reserva automática programada*\n"
        f"Hora Chile: {ahora().strftime('%H:%M:%S')}\n"
        "El bot esperará hasta las 17:55 y arrancará solo.",
        parse_mode="Markdown",
    )


async def cmd_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in configs or "tel" not in configs[uid].get("r2", {}):
        return await update.message.reply_text("⚠️ Usa /config primero")
    if uid in tareas and any(not t.done() for t in tareas[uid]):
        return await update.message.reply_text("⚠️ Ya hay tarea activa. /detener primero")

    tarea = asyncio.create_task(
        _esperar_y_ejecutar(uid, update.effective_chat.id, ctx, test=True)
    )
    tareas[uid] = [tarea]
    await update.message.reply_text("🧪 Test iniciado — buscando 5 minutos")


# ═══════════════════════════════════════════════════════════════
# CONVERSACIÓN /config
# ═══════════════════════════════════════════════════════════════

async def cfg_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📝 *Configuración Reserva 1*\n\nIngresa RUT (ej: 12345678-9):",
        parse_mode="Markdown",
    )
    return E_RUT1


async def cfg_rut1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rut = validar_rut(update.message.text)
    if not rut:
        await update.message.reply_text("❌ RUT inválido. Intenta de nuevo:")
        return E_RUT1
    configs[update.effective_user.id] = {"r1": {"rut": rut}, "r2": {}}
    await update.message.reply_text(
        f"✅ RUT: `{fmt(rut)}`\n\nHora preferida R1 (ej: 9, 10, 20):",
        parse_mode="Markdown",
    )
    return E_HORA1


async def cfg_h1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = int(update.message.text.strip().replace(":00", ""))
        assert 0 <= h <= 23
    except (ValueError, AssertionError):
        await update.message.reply_text("❌ Hora inválida (0-23):")
        return E_HORA1
    configs[update.effective_user.id]["r1"]["pref"] = h
    await update.message.reply_text(f"✅ Preferida: {h}:00\n\nHora alternativa R1:")
    return E_HORA1_ALT


async def cfg_h1a(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = int(update.message.text.strip().replace(":00", ""))
        assert 0 <= h <= 23
    except (ValueError, AssertionError):
        await update.message.reply_text("❌ Hora inválida:")
        return E_HORA1_ALT
    configs[update.effective_user.id]["r1"]["alt"] = h
    await update.message.reply_text(f"✅ Alternativa: {h}:00\n\nTeléfono R1 (9 dígitos, ej: 912345678):")
    return E_TEL1


async def cfg_t1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip().replace("+56", "").replace(" ", "")
    if len(t) != 9 or not t.isdigit():
        await update.message.reply_text("❌ Debe tener 9 dígitos:")
        return E_TEL1
    configs[update.effective_user.id]["r1"]["tel"] = t
    await update.message.reply_text(
        "✅ R1 configurada.\n\n"
        "📝 *Configuración Reserva 2*\n\nIngresa RUT:",
        parse_mode="Markdown",
    )
    return E_RUT2


async def cfg_rut2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rut = validar_rut(update.message.text)
    if not rut:
        await update.message.reply_text("❌ RUT inválido:")
        return E_RUT2
    configs[update.effective_user.id]["r2"]["rut"] = rut
    await update.message.reply_text(
        f"✅ RUT: `{fmt(rut)}`\n\nHora preferida R2:",
        parse_mode="Markdown",
    )
    return E_HORA2


async def cfg_h2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = int(update.message.text.strip().replace(":00", ""))
        assert 0 <= h <= 23
    except (ValueError, AssertionError):
        await update.message.reply_text("❌ Hora inválida:")
        return E_HORA2
    configs[update.effective_user.id]["r2"]["pref"] = h
    await update.message.reply_text(f"✅ Preferida: {h}:00\n\nHora alternativa R2:")
    return E_HORA2_ALT


async def cfg_h2a(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = int(update.message.text.strip().replace(":00", ""))
        assert 0 <= h <= 23
    except (ValueError, AssertionError):
        await update.message.reply_text("❌ Hora inválida:")
        return E_HORA2_ALT
    configs[update.effective_user.id]["r2"]["alt"] = h
    await update.message.reply_text(f"✅ Alternativa: {h}:00\n\nTeléfono R2 (9 dígitos):")
    return E_TEL2


async def cfg_t2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip().replace("+56", "").replace(" ", "")
    if len(t) != 9 or not t.isdigit():
        await update.message.reply_text("❌ Debe tener 9 dígitos:")
        return E_TEL2
    uid = update.effective_user.id
    configs[uid]["r2"]["tel"] = t

    r1, r2 = configs[uid]["r1"], configs[uid]["r2"]
    await update.message.reply_text(
        "✅ *CONFIGURACIÓN COMPLETA*\n\n"
        f"*R1:* `{fmt(r1['rut'])}` → {r1['pref']}:00 / {r1['alt']}:00 | Tel: {r1['tel']}\n"
        f"*R2:* `{fmt(r2['rut'])}` → {r2['pref']}:00 / {r2['alt']}:00 | Tel: {r2['tel']}\n\n"
        "Usa /auto para activar o /test para probar",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def cfg_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Configuración cancelada")
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ver", cmd_ver))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("auto", cmd_auto))
    app.add_handler(CommandHandler("test", cmd_test))
    app.add_handler(CommandHandler("detener", cmd_detener))

    # Conversación /config
    conv = ConversationHandler(
        entry_points=[CommandHandler("config", cfg_inicio)],
        states={
            E_RUT1:     [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_rut1)],
            E_HORA1:    [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_h1)],
            E_HORA1_ALT:[MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_h1a)],
            E_TEL1:     [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_t1)],
            E_RUT2:     [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_rut2)],
            E_HORA2:    [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_h2)],
            E_HORA2_ALT:[MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_h2a)],
            E_TEL2:     [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_t2)],
        },
        fallbacks=[CommandHandler("cancelar", cfg_cancel)],
    )
    app.add_handler(conv)

    logger.info("Bot Reserva Doble v3.0 — LISTO")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
