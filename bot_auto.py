import asyncio
import datetime
import os
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

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN no configurado")

(
    ESPERANDO_RUT1,
    ESPERANDO_HORA1,
    ESPERANDO_HORA1_ALT,
    ESPERANDO_TEL1,
    ESPERANDO_RUT2,
    ESPERANDO_HORA2,
    ESPERANDO_HORA2_ALT,
    ESPERANDO_TEL2,
) = range(8)

user_data = {}
reserva_en_proceso = {}

# =============================
# UTILIDADES
# =============================


def calcular_dv(rut_sin_dv):
    suma = 0
    multip = 2
    for d in reversed(str(rut_sin_dv)):
        suma += int(d) * multip
        multip = 2 if multip == 7 else multip + 1
    dv = 11 - (suma % 11)
    return "0" if dv == 11 else "K" if dv == 10 else str(dv)


def validar_rut(rut):
    rut = rut.replace(".", "").replace("-", "").upper()
    cuerpo, dv = rut[:-1], rut[-1]
    if calcular_dv(cuerpo) != dv:
        return None
    return rut


# =============================
# CONFIGURACION
# =============================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Bot Reserva Doble\n\n"
        "/config\n"
        "/auto\n"
        "/status\n"
        "/detener"
    )


async def config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("RUT Reserva 1:")
    return ESPERANDO_RUT1


async def guardar_rut1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rut = validar_rut(update.message.text)
    if not rut:
        await update.message.reply_text("RUT inválido")
        return ESPERANDO_RUT1

    user_data[update.effective_user.id] = {"r1": {}, "r2": {}}
    user_data[update.effective_user.id]["r1"]["rut"] = rut
    await update.message.reply_text("Hora preferida R1:")
    return ESPERANDO_HORA1


async def guardar_hora1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data[update.effective_user.id]["r1"]["pref"] = int(update.message.text)
    await update.message.reply_text("Hora alternativa R1:")
    return ESPERANDO_HORA1_ALT


async def guardar_hora1_alt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data[update.effective_user.id]["r1"]["alt"] = int(update.message.text)
    await update.message.reply_text("Teléfono R1:")
    return ESPERANDO_TEL1


async def guardar_tel1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data[update.effective_user.id]["r1"]["tel"] = update.message.text
    await update.message.reply_text("RUT Reserva 2:")
    return ESPERANDO_RUT2


async def guardar_rut2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rut = validar_rut(update.message.text)
    if not rut:
        await update.message.reply_text("RUT inválido")
        return ESPERANDO_RUT2

    user_data[update.effective_user.id]["r2"]["rut"] = rut
    await update.message.reply_text("Hora preferida R2:")
    return ESPERANDO_HORA2


async def guardar_hora2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data[update.effective_user.id]["r2"]["pref"] = int(update.message.text)
    await update.message.reply_text("Hora alternativa R2:")
    return ESPERANDO_HORA2_ALT


async def guardar_hora2_alt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data[update.effective_user.id]["r2"]["alt"] = int(update.message.text)
    await update.message.reply_text("Teléfono R2:")
    return ESPERANDO_TEL2


async def guardar_tel2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data[update.effective_user.id]["r2"]["tel"] = update.message.text
    await update.message.reply_text("✅ Configuración completa.\nUsa /auto")
    return ConversationHandler.END


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        await update.message.reply_text("Sin configuración")
        return

    r1 = user_data[user_id]["r1"]
    r2 = user_data[user_id]["r2"]

    await update.message.reply_text(
        f"R1: {r1['rut']} {r1['pref']}:00 (alt {r1['alt']}:00)\n"
        f"R2: {r2['rut']} {r2['pref']}:00 (alt {r2['alt']}:00)"
    )


# =============================
# RESERVA ULTRA OPTIMIZADA
# =============================


async def ejecutar_reserva(reserva, numero, chat_id, context):
    rut = reserva["rut"]
    pref = f"{reserva['pref']}:00"
    alt = f"{reserva['alt']}:00"
    tel = reserva["tel"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()

        await page.goto("https://reservadehoras.lascondes.cl/#/agenda/28/agendar")
        await page.wait_for_selector('input[name="rut"]')

        # Validar RUT inicial
        await page.fill('input[name="rut"]', rut)
        await page.click('button:has-text("Validar")')
        await page.wait_for_timeout(1500)

        fin = datetime.datetime.now().replace(hour=18, minute=10, second=0)
        if fin < datetime.datetime.now():
            fin += datetime.timedelta(days=1)

        intentos = 0

        while datetime.datetime.now() < fin:

            intentos += 1
            ahora = datetime.datetime.now()

            # 🔥 MODO APERTURA CON RELOAD
            modo_apertura = (
                (ahora.hour == 17 and ahora.minute == 59 and ahora.second >= 45)
                or (ahora.hour == 18 and ahora.minute <= 1)
            )

            if modo_apertura:
                await page.reload()
                await page.wait_for_timeout(400)

                rut_input = await page.query_selector('input[name="rut"]')
                if rut_input:
                    await rut_input.fill("")
                    await rut_input.type(rut, delay=20)
                    await page.click('button:has-text("Validar")')
                    await page.wait_for_timeout(600)

            for hora in [pref, alt]:
                btn = await page.query_selector(f'button:has-text("{hora}")')
                if btn and not await btn.is_disabled():

                    await btn.click()
                    await page.wait_for_timeout(300)

                    await page.click('button:has-text("Ok, programar")')
                    await page.wait_for_timeout(800)

                    campo = await page.query_selector(
                        'input[type="tel"], input[name*="tel"], input[name*="fono"]'
                    )
                    if campo:
                        await campo.fill(tel)

                    await page.click('button:has-text("Reservar")')
                    await page.wait_for_timeout(1200)

                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"[R{numero}] ✅ RESERVA EXITOSA {hora} (intento {intentos})",
                    )

                    await browser.close()
                    return

            # Sleep 
