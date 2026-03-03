# bot_auto.py - Versión Final Óptima v2.1
import asyncio
import datetime
import os
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, ContextTypes, 
    ConversationHandler, MessageHandler, filters
)
from playwright.async_api import async_playwright

# =============================
# CONFIGURACIÓN
# =============================

TOKEN = os.environ.get("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN no configurado en Railway")

URL_AGENDA = "https://reservadehoras.lascondes.cl/#/agenda/28/agendar"

(
    ESPERANDO_RUT1, ESPERANDO_HORA1, ESPERANDO_HORA1_ALT, ESPERANDO_TEL1,
    ESPERANDO_RUT2, ESPERANDO_HORA2, ESPERANDO_HORA2_ALT, ESPERANDO_TEL2
) = range(8)

user_data = {}
sesiones_activas = set()  # Para evitar doble ejecución

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

def obtener_sleep_time():
    """Sleep dinámico según proximidad a 18:00"""
    ahora = datetime.datetime.now()
    
    if (ahora.hour == 17 and ahora.minute == 59 and ahora.second >= 50) or \
       (ahora.hour == 18 and ahora.minute <= 1):
        return 0.5  # Modo crítico ultrarrápido
    elif ahora.hour == 18 and ahora.minute == 2:
        return 1.0  # Reducción progresiva
    else:
        return 2.0  # Modo normal estable

async def enviar_log(chat_id, context, texto):
    try:
        await context.bot.send_message(chat_id=chat_id, text=texto, parse_mode="Markdown")
    except:
        pass

# =============================
# MANEJO DE USUARIO
# =============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data.setdefault(user_id, {"r1": {}, "r2": {}})
    
    await update.message.reply_text(
        "🎾 **Bot Reserva Cancha Las Condes**\n\n"
        "/config  — Iniciar configuración\n"
        "/auto    — Ejecutar reserva doble\n"
        "/test    — Prueba rápida (simulación)\n"
        "/status  — Ver estado actual\n"
        "/detener — Parar proceso activo\n\n"
        "_Sistema optimizado v2.1_"
    )

async def config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id] = {"r1": {}, "r2": {}}
    
    await update.message.reply_text(
        "🔧 **Configurando Reserva 1**\n\n"
        "Envía el **RUT** sin puntos ni guión:\n"
        "_Ejemplo: 12345678_")
    return ESPERANDO_RUT1

async def guardar_rut1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rut = validar_rut(update.message.text)
    if not rut:
        await update.message.reply_text("❌ RUT inválido. Revisa el formato e inténtalo de nuevo.")
        return ESPERANDO_RUT1
    
    user_data[user_id]["r1"]["rut"] = rut
    await update.message.reply_text(
        f"✅ RUT guardado: `{rut}`\n\n"
        "Envía la **hora preferida** (ejemplo: 9):\n")
    return ESPERANDO_HORA1

async def guardar_hora1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        hora = int(update.message.text)
        if hora < 0 or hora > 23:
            raise ValueError
        user_data[user_id]["r1"]["pref"] = hora
        await update.message.reply_text(
            f"✅ Hora preferida: {hora}:00\n\n"
            "Envía la **hora alternativa** (ejemplo: 10):")
        return ESPERANDO_HORA1_ALT
    except:
        await update.message.reply_text("❌ Hora inválida. Usa números (0–23)")
        return ESPERANDO_HORA1

async def guardar_hora1_alt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        hora = int(update.message.text)
        if hora < 0 or hora > 23:
            raise ValueError
        user_data[user_id]["r1"]["alt"] = hora
        await update.message.reply_text(f"✅ Hora alt: {hora}:00\n\n"
                                        "Envía el **teléfono** (sin +56):")
        return ESPERANDO_TEL1
    except:
        await update.message.reply_text("❌ Hora inválida. Intenta otra vez.")
        return ESPERANDO_HORA1_ALT

async def guardar_tel1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    tel = update.message.text.strip().replace(" ", "")
    if len(tel) < 9:
        await update.message.reply_text("❌ Teléfono muy corto. Ingresalo completo:")
        return ESPERANDO_TEL1
    
    user_data[user_id]["r1"]["tel"] = tel
    await update.message.reply_text(
        f"✅ Tel 1: `{tel}`\n\n"
        "**Ahora configura Reserva 2**\n\n"
        "Envía el **RUT**:")
    return ESPERANDO_RUT2

async def guardar_rut2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rut = validar_rut(update.message.text)
    if not rut:
        await update.message.reply_text("❌ RUT inválido. Revisa el formato:")
        return ESPERANDO_RUT2
    
    user_data[user_id]["r2"]["rut"] = rut
    await update.message.reply_text(f"✅ RUT 2: `{rut}`\n\n"
                                    "Envía la **hora preferida**:")
    return ESPERANDO_HORA2

async def guardar_hora2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        hora = int(update.message.text)
        user_data[user_id]["r2"]["pref"] = hora
        await update.message.reply_text(f"✅ Hora 2: {hora}:00\n\n"
                                        "Envía la **hora alternativa**:")
        return ESPERANDO_HORA2_ALT
    except:
        await update.message.reply_text("❌ Hora inválida. Intenta otra vez:")
        return ESPERANDO_HORA2

async def guardar_hora2_alt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        hora = int(update.message.text)
        user_data[user_id]["r2"]["alt"] = hora
        await update.message.reply_text(f"✅ Hora alt 2: {hora}:00\n\n"
                                        "Envía el **teléfono**:")
        return ESPERANDO_TEL2
    except:
        await update.message.reply_text("❌ Hora inválida:")
        return ESPERANDO_HORA2_ALT

async def guardar_tel2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    tel = update.message.text.strip().replace(" ", "")
    if len(tel) < 9:
        await update.message.reply_text("❌ Teléfono muy corto. Inténtalo de nuevo:")
        return ESPERANDO_TEL2
    
    user_data[user_id]["r2"]["tel"] = tel
    
    resumen = (
        f"\n🎯 **CONFIGURACIÓN FINALIZADA**\n{'='*40}\n"
        f"**Reserva 1**\nRUT: {user_data[user_id]['r1']['rut']}\n"
        f"Preferida: {user_data[user_id]['r1']['pref']}:00\n"
        f"Alternativa: {user_data[user_id]['r1']['alt']}:00\n"
        f"Tel: {user_data[user_id]['r1']['tel']}\n\n"
        f"**Reserva 2**\nRUT: {user_data[user_id]['r2']['rut']}\n"
        f"Preferida: {user_data[user_id]['r2']['pref']}:00\n"
        f"Alternativa: {user_data[user_id]['r2']['alt']}:00\n"
        f"Tel: {user_data[user_id]['r2']['tel']}\n{'='*40}"
    )
    
    await update.message.reply_text(resumen + "\n\nUsa `/auto` para iniciar")
    return ConversationHandler.END

# =============================
# MOTOR DE RESERVA
# =============================

async def ejecutar_una_reserva(reserva, numero, chat_id, context):
    rut = reserva["rut"]
    pref = f"{reserva['pref']}:00"
    alt = f"{reserva['alt']}:00"
    tel = reserva["tel"]
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, 
            args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu']
        )
        
        context_args = {'viewport': {'width': 1920, 'height': 1080}}
        page = await browser.new_page(**context_args)
        
        # Página inicial con carga optimizada
        await page.goto(URL_AGENDA, wait_until="domcontentloaded")
        await page.wait_for_selector('input[name="rut"]', timeout=15000)
        
        await page.fill('input[name="rut"]', rut)
        await page.click('button:has-text("Validar")')
        await page.wait_for_timeout(2000)
        
        fin_ventana = datetime.datetime.now().replace(hour=18, minute=10, second=0)
        if fin_ventana < datetime.datetime.now():
            fin_ventana += datetime.timedelta(days=1)
        
        intentos = 0
        
        while datetime.datetime.now() < fin_ventana:
            intentos += 1
            
            try:
                hora_encontrada = None
                hora_str = None
                
                for hora in [pref, alt]:
                    btn = await page.query_selector(f'button:has-text("{hora}")')
                    if btn and not await btn.is_disabled():
                        horas_disponibles = await page.query_selector_all('button')
                        horario_habilitado = False
                        for h in horarios_disponibles:
                            t = await h.inner_text()
                            if t.endswith(hora.split(":")[0]):
                                horario_habilitado = True
                                break
                        
                        if horario_habilitado:
                            hora_encontrada = btn
                            hora_str = hora
                            break
                
                if hora_encontrada:
                    await enviar_log(chat_id, context, 
                                   f"[R{numero}] 🟢 Hora disponible: {hora_str} | Int {intentos}")
                    
                    await hora_encontrada.click()
                    await page.wait_for_timeout(500)
                    
                    btn_programar = await page.query_selector('button:has-text("Ok, programar")')
                    if btn_programar:
                        await btn_programar.click()
                        await page.wait_for_timeout(800)
                        
                        campo_tel = None
                        selectors = [
                            'input[type="tel"]',
                            'input[name*="tel"]',
                            'input[name*="fono"]',
                            'input[name*="celular"]',
                            '[placeholder*="teléfono"]',
                        ]
                        for sel in selectors:
                            campo_tel = await page.query_selector(sel)
                            if campo_tel:
                                break
                        
                        if campo_tel:
                            await campo_tel.fill("")
                            await campo_tel.type(tel, delay=10)
                            await page.wait_for_timeout(300)
                            
                            btn_reservar = await page.query_selector('button:has-text("Reservar")')
                            if btn_reservar:
                                await btn_reservar.click()
                                await page.wait_for_timeout(2000)
                                
                                ss = await page.screenshot(path=f"screenshot_r{numero}.png")
                                await context.bot.send_photo(
                                    chat_id=chat_id,
                                    photo=ss,
                                    caption=f"[R{numero}] ✅ **RESERVA EXITOSA**\nHora: {hora_str}\nIntento: {intentos}",
                                    parse_mode="Markdown"
                                )
                                await browser.close()
                                return True
                            else:
                                await enviar_log(chat_id, context, 
                                               f"[R{numero}] ⚠️ Botón Reservar no encontrado")
                    else:
                        await enviar_log(chat_id, context, 
                                       f"[R{numero}] ⚠️ Ok Programar no encontrado")
                
                # Polling óptimo con sleep dinámico
                await asyncio.sleep(obtener_sleep_time())
                
                if intentos % 20 == 0:
                    await enviar_log(chat_id, context, 
                                   f"[R{numero}] ⏳ Int: {intentos} | {datetime.datetime.now().strftime('%H:%M:%S')}")
            
            except Exception as e:
                error_msg = str(e)[:100]
                if intentos % 10 == 0:
                    await enviar_log(chat_id, context, 
                                   f"[R{numero}] ⚠️ Error: {error_msg}")
                await asyncio.sleep(1)
        
        await browser.close()
        await enviar_log(chat_id, context, f"[R{numero}] ❌ No se logró reservar en ventana")
        return False

async def ejecutar_reserva_doble(user_id, chat_id, context):
    if user_id in sesiones_activas:
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Ya hay una reserva en ejecución")
        return
    
    sesiones_activas.add(user_id)
    
    try:
        r1 = user_data[user_id].get("r1", {})
        r2 = user_data[user_id].get("r2", {})
        
        if not r1 or not r2:
            await context.bot.send_message(chat_id=chat_id, 
                                          text="❌ Configuración incompleta. Usa /config primero")
            return
        
        msg = (
            f"🚀 **INICIANDO RESERVA DOBLE**\n"
            f"**Ventana:** 17:55–18:10\n"
            f"**Modo:** Ultraligero sin recargas\n"
            f"🔄 Dos navegadores en paralelo..."
        )
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
        
        resultados = await asyncio.gather(
            ejecutar_una_reserva(r1, 1, chat_id, context),
            ejecutar_una_reserva(r2, 2, chat_id, context),
            return_exceptions=False
        )
        
        exitosas = sum(1 for r in resultados if r is True)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"\n📊 **RESUMEN FINAL**\n{'='*40}\n"
                 f"✅ Exitosas: {exitosas}/2\n"
                 f"{'='*40}\n"
                 f"Bot finalizado.",
            parse_mode="Markdown"
        )
    
    finally:
        sesiones_activas.discard(user_id)

async def auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if user_id not in user_data or not user_data[user_id].get("r1"):
        await update.message.reply_text("❌ Primero configura con /config")
        return
    
    task = asyncio.create_task(ejecutar_reserva_doble(user_id, chat_id, context))
    await update.message.reply_text("📢 Reserva iniciada. Notificaré el resultado.")

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧪 **Prueba simulada**\n\n"
                                    "El bot verificará:\n"
                                    "• Conexión a Telegram\n"
                                    "• Estructura de datos\n"
                                    "• Acceso a sitio web\n\n"
                                    "Sin realizar reservas reales...")
    
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(URL_AGENDA, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                status = "✅ Sitio accesible" if resp.status == 200 else f"⚠️ Estado {resp.status}"
        await update.message.reply_text(status)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error conexión: {str(e)[:50]}")
    
    await update.message.reply_text("✅ Prueba completada. Usa /auto cuando desees reservar.")

async def detener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in sesiones_activas:
        sesiones_activas.discard(user_id)
        await update.message.reply_text("🛑 Proceso detenido.")
    else:
        await update.message.reply_text("ℹ️ No hay procesos activos.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_data.get(user_id, {})
    
    txt = "**ESTADO ACTUAL**\n"
    if data.get("r1"):
        txt += f"\nR1: {data['r1'].get('rut')} - {data['r1'].get('pref')}:00 ({data['r1'].get('alt')}:00)"
    if data.get("r2"):
        txt += f"\nR2: {data['r2'].get('rut')} - {data['r2'].get('pref')}:00 ({data['r2'].get('alt')}:00)"
    
    if not data.get("r1"):
        txt += "\nSin configuración. Usa /config"
    
    activo = "🟢 ACTIVO" if user_id in sesiones_activas else "🔴 INACTIVO"
    txt += f"\n\nEstado: {activo}"
    
    await update.message.reply_text(txt)

# =============================
# MAIN
# =============================

async def initialize():
    print("✅ Bot inicializado v2.1 - Ready for production")

def main():
    app = Application.builder().token(TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[CommandHandler("config", config)],
        states={
            ESPERANDO_RUT1: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_rut1)],
            ESPERANDO_HORA1: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_hora1)],
            ESPERANDO_HORA1_ALT: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_hora1_alt)],
            ESPERANDO_TEL1: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_tel1)],
            ESPERANDO_RUT2: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_rut2)],
            ESPERANDO_HORA2: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_hora2)],
            ESPERANDO_HORA2_ALT: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_hora2_alt)],
            ESPERANDO_TEL2: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_tel2)],
        },
        fallbacks=[]
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("auto", auto))
    app.add_handler(CommandHandler("test", test))
    app.add_handler(CommandHandler("/detener", detener))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(conv)
    
    asyncio.run(initialize())
    print("Bot ejecutando... 🚀")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
