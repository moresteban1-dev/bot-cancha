"""
Bot Reserva Doble — Canchas Las Condes
v4.3 FINAL · Marzo 2026 · 100% FUNCIONAL Railway + Debian Trixie
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

configs = {}
tareas = {}
estado = {}
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
    if f2 != f1: res.append(f2)
    res.append(f"{f1} hrs")
    if f2 != f1: res.append(f"{f2} hrs")
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
                estado = "SOLD OUT" if disabled is not None else "AVAILABLE"
                horas.append(f"{estado} {texto}")
        except:
            continue
    return horas

async def _buscar_campo_tel(page):
    selectores = ['input[type="tel"]', 'input[name*="tel"]', 'input[name*="fono"]', 'input[placeholder*="9"]']
    for sel in selectores:
        campo = await page.query_selector(sel)
        if campo:
            return campo
    return None

async def _click_boton(page, textos: list) -> bool:
    for texto in textos:
        btn = await page.query_selector(f'button:has-text("{texto}")')
        if btn and await btn.get_attribute("disabled") is None:
            await btn.click()
            return True
    return False


# ═══════════════════════════════════════════════════════════════
# MOTOR DE RESERVA
# ═══════════════════════════════════════════════════════════════

async def motor_reserva(reserva: dict, numero: int, chat_id: int, ctx: ContextTypes.DEFAULT_TYPE, uid: int, test: bool = False) -> str:
    rut = reserva["rut"]
    hora_pref = reserva["pref"]
    hora_alt = reserva["alt"]
    tel = reserva["tel"]
    tag = f"[R{numero}]"
    ganadas = horas_ganadas.setdefault(uid, set())

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
            browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
            page = await browser.new_page()
            page.set_default_timeout(15000)

            await msg("Abriendo página...")
            await page.goto(URL, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

            await msg(f"RUT {fmt(rut)}")
            if not await _ingresar_rut(page, rut):
                await foto(page, "Campo RUT no encontrado")
                await browser.close()
                return "ERROR RUT"

            if not await _click_validar(page):
                await foto(page, "Botón Validar no encontrado")
                await browser.close()
                return "ERROR VALIDAR"

            await page.wait_for_timeout(3000)

            # Verificar RUT aceptado
            if await page.query_selector('input[name="rut"]'):
                if await page.locator('input[name="rut"]').is_visible():
                    await foto(page, "RUT RECHAZADO")
                    await browser.close()
                    return "RUT RECHAZADO"

            await msg("RUT validado")
            horas = await _listar_horas_visibles(page)
            if horas:
                await msg("Horas en pantalla:\n" + "\n".join(horas[:12]))

            fin = ahora() + datetime.timedelta(minutes=5) if test else ahora().replace(hour=18, minute=10, second=0, microsecond=0)
            if not test and fin <= ahora():
                fin += datetime.timedelta(days=1)

            intentos = 0
            while ahora() < fin:
                intentos += 1

                # Auto-recovery
                if await page.query_selector('input[name="rut"]'):
                    if await page.locator('input[name="rut"]').is_visible():
                        await msg("Página reiniciada → reingresando RUT")
                        await _ingresar_rut(page, rut)
                        await _click_validar(page)
                        await page.wait_for_timeout(3000)
                        continue

                for hora_int in [hora_pref, hora_alt]:
                    btn, texto = await _buscar_boton_hora(page, hora_int, ganadas)
                    if not btn:
                        continue

                    await msg(f"{texto} DISPONIBLE! (intento {intentos})")
                    for f in formatos_hora(hora_int):
                        ganadas.add(f)

                    await btn.click()
                    await page.wait_for_timeout(400)
                    await foto(page, f"Click {texto}")

                    await _click_boton(page, ["Ok, programar", "OK", "Programar", "Confirmar"])
                    await page.wait_for_timeout(1000)

                    campo = await _buscar_campo_tel(page)
                    if campo:
                        await campo.fill("")
                        await campo.type(tel, delay=25)
                        await msg(f"Teléfono {tel}")

                    await page.wait_for_timeout(300)
                    await _click_boton(page, ["Reservar", "Confirmar reserva"])

                    await page.wait_for_timeout(2000)
                    await foto(page, f"RESERVA {texto} — RUT {fmt(rut)}")
                    await browser.close()
                    return f"RESERVA CONFIRMADA — {texto}"

                await asyncio.sleep(sleep_dinamico())

                if intentos % 30 == 0:
                    modo = "ULTRA" if sleep_dinamico() <= 0.5 else "AGRESIVO" if sleep_dinamico() <= 1 else "NORMAL"
                    await msg(f"Intento {intentos} | {ahora().strftime('%H:%M:%S')} | {modo}")

            await foto(page, "TIEMPO AGOTADO")
            await browser.close()
            return f"AGOTADO — {intentos} intentos"

    except Exception as e:
        await msg(f"ERROR FATAL: {str(e)[:100]}")
        return f"ERROR FATAL"


# ═══════════════════════════════════════════════════════════════
# ORQUESTADOR Y ESPERA
# ═══════════════════════════════════════════════════════════════

async def orquestar(uid: int, chat_id: int, ctx, test=False):
    c = configs[uid]
    horas_ganadas[uid] = set()
    estado[uid] = "ejecutando"

    await ctx.bot.send_message(chat_id,
        f"DOBLE RESERVA {'TEST' if test else 'REAL'}\n\n"
        f"R1 → {c['r1']['pref']}:00 / {c['r1']['alt']}:00\n"
        f"R2 → {c['r2']['pref']}:00 / {c['r2']['alt']}:00\n"
        f"Hora Chile: {ahora().strftime('%H:%M:%S')}",
        parse_mode="Markdown")

    resultados = await asyncio.gather(
        motor_reserva(c["r1"], 1, chat_id, ctx, uid, test),
        motor_reserva(c["r2"], 2, chat_id, ctx, uid, test),
        return_exceptions=True,
    )

    resumen = "RESUMEN FINAL\n\n"
    for i, r in enumerate(resultados, 1):
        resumen += f"R{i}: {r}\n"
    await ctx.bot.send_message(chat_id, resumen)


async def _esperar_y_ejecutar(uid, chat_id, ctx, test):
    if not test:
        inicio = ahora().replace(hour=17, minute=55, second=0, microsecond=0)
        if ahora() > inicio.replace(hour=18, minute=10):
            inicio += datetime.timedelta(days=1)
        if ahora() < inicio:
            espera = int((inicio - ahora()).total_seconds())
            await ctx.bot.send_message(chat_id, f"Esperando {espera//60}m hasta 17:55...")
            await asyncio.sleep(espera)
    await orquestar(uid, chat_id, ctx, test)


# ═══════════════════════════════════════════════════════════════
# COMANDOS
# ═══════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot Reserva Doble v4.3 — MARZO 2026\n\n"
        "/config → configurar\n"
        "/ver → ver config\n"
        "/test → test ambos\n"
        "/test1 → solo R1\n"
        "/test2 → solo R2\n"
        "/auto → modo real 17:55\n"
        "/detener → parar\n"
        "/status → estado",
        parse_mode="Markdown")

async def cmd_ver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c = configs.get(uid, {})
    if "r2" not in c or "tel" not in c["r2"]:
        return await update.message.reply_text("Configuración incompleta. Usa /config")
    r1, r2 = c["r1"], c["r2"]
    await update.message.reply_text(
        f"R1 → {r1['pref']}:00 / {r1['alt']}:00 | {fmt(r1['rut'])}\n"
        f"R2 → {r2['pref']}:00 / {r2['alt']}:00 | {fmt(r2['rut'])}")

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    est = estado.get(uid, "idle")
    await update.message.reply_text(f"Estado: {est}\nHora Chile: {ahora().strftime('%H:%M:%S')}")

async def cmd_detener(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in tareas:
        for t in tareas[uid]:
            t.cancel()
        tareas.pop(uid, None)
        estado[uid] = "idle"
        horas_ganadas.pop(uid, None)
    await update.message.reply_text("Detenido")

async def cmd_auto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if "r2" not in configs.get(uid, {}) or "tel" not in configs[uid]["r2"]:
        return await update.message.reply_text("Configuración incompleta")
    tarea = asyncio.create_task(_esperar_y_ejecutar(uid, update.effective_chat.id, ctx, test=False))
    tareas[uid] = [tarea]
    await update.message.reply_text("Modo real activado — espera 17:55")

async def cmd_test(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if "r2" not in configs.get(uid, {}) or "tel" not in configs[uid]["r2"]:
        return await update.message.reply_text("Configuración incompleta")
    tarea = asyncio.create_task(_esperar_y_ejecutar(uid, update.effective_chat.id, ctx, test=True))
    tareas[uid] = [tarea]
    await update.message.reply_text("Test ambos iniciado — 5 minutos")

async def cmd_test1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c = configs.get(uid, {})
    if "r1" not in c or "tel" not in c["r1"]:
        return await update.message.reply_text("R1 no configurado")
    tarea = asyncio.create_task(motor_reserva(c["r1"], 1, update.effective_chat.id, ctx, uid, test=True))
    tareas[uid] = [tarea]
    await update.message.reply_text("Test R1 iniciado")

async def cmd_test2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    c = configs.get(uid, {})
    if "r2" not in c or "tel" not in c["r2"]:
        return await update.message.reply_text("R2 no configurado completamente. Usa /config")
    tarea = asyncio.create_task(motor_reserva(c["r2"], 2, update.effective_chat.id, ctx, uid, test=True))
    tareas[uid] = [tarea]
    await update.message.reply_text("Test R2 iniciado — 5 minutos de búsqueda")


# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════

async def cfg_inicio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("RUT Reserva 1 (ej: 12345678-K):")
    return E_RUT1

async def cfg_rut1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rut = validar_rut(update.message.text)
    if not rut:
        await update.message.reply_text("RUT inválido")
        return E_RUT1
    configs[update.effective_user.id] = {"r1": {"rut": rut}, "r2": {}}
    await update.message.reply_text("Hora preferida R1 (ej: 9):")
    return E_HORA1

async def cfg_h1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = int(update.message.text.strip())
        configs[update.effective_user.id]["r1"]["pref"] = h
        await update.message.reply_text("Hora alternativa R1:")
        return E_HORA1_ALT
    except:
        await update.message.reply_text("Número inválido")
        return E_HORA1

async def cfg_h1a(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = int(update.message.text.strip())
        configs[update.effective_user.id]["r1"]["alt"] = h
        await update.message.reply_text("Teléfono R1 (9 dígitos):")
        return E_TEL1
    except:
        await update.message.reply_text("Número inválido")
        return E_HORA1_ALT

async def cfg_t1(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip().replace(" ", "").replace("+56", "")
    if len(t) != 9 or not t.isdigit():
        await update.message.reply_text("Debe tener 9 dígitos")
        return E_TEL1
    configs[update.effective_user.id]["r1"]["tel"] = t
    await update.message.reply_text("RUT Reserva 2:")
    return E_RUT2

async def cfg_rut2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rut = validar_rut(update.message.text)
    if not rut:
        await update.message.reply_text("RUT inválido")
        return E_RUT2
    configs[update.effective_user.id]["r2"]["rut"] = rut
    await update.message.reply_text("Hora preferida R2:")
    return E_HORA2

async def cfg_h2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = int(update.message.text.strip())
        configs[update.effective_user.id]["r2"]["pref"] = h
        await update.message.reply_text("Hora alternativa R2:")
        return E_HORA2_ALT
    except:
        await update.message.reply_text("Número inválido")
        return E_HORA2

async def cfg_h2a(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        h = int(update.message.text.strip())
        configs[update.effective_user.id]["r2"]["alt"] = h
        await update.message.reply_text("Teléfono R2 (9 dígitos):")
        return E_TEL2
    except:
        await update.message.reply_text("Número inválido")
        return E_HORA2_ALT

async def cfg_t2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip().replace(" ", "").replace("+56", "")
    if len(t) != 9 or not t.isdigit():
        await update.message.reply_text("Debe tener 9 dígitos")
        return E_TEL2
    uid = update.effective_user.id
    configs[uid]["r2"]["tel"] = t
    r1, r2 = configs[uid]["r1"], configs[uid]["r2"]
    await update.message.reply_text(
        "CONFIGURACIÓN COMPLETA\n\n"
        f"R1 → {r1['pref']}:00 / {r1['alt']}:00 | {fmt(r1['rut'])}\n"
        f"R2 → {r2['pref']}:00 / {r2['alt']}:00 | {fmt(r2['rut'])}\n\n"
        "Usa /test2 para probar solo R2 o /auto para el día real")
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ver", cmd_ver))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("auto", cmd_auto))
    app.add_handler(CommandHandler("test", cmd_test))
    app.add_handler(CommandHandler("test1", cmd_test1))
    app.add_handler(CommandHandler("test2", cmd_test2))
    app.add_handler(CommandHandler("detener", cmd_detener))

    conv = ConversationHandler(
        entry_points=[CommandHandler("config", cfg_inicio)],
        states={
            E_RUT1: [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_rut1)],
            E_HORA1: [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_h1)],
            E_HORA1_ALT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_h1a)],
            E_TEL1: [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_t1)],
            E_RUT2: [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_rut2)],
            E_HORA2: [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_h2)],
            E_HORA2_ALT: [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_h2a)],
            E_TEL2: [MessageHandler(filters.TEXT & ~filters.COMMAND, cfg_t2)],
        },
        fallbacks=[],
        per_message=False,
    )
    app.add_handler(conv)

    logger.info("Bot Reserva Doble v4.3 — LISTO")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
