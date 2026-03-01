import asyncio
import datetime
import os
import sys
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from playwright.async_api import async_playwright
import logging

# ============================================
# 🔧 CONFIGURACIÓN
# ============================================
TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    print("⚠️ ADVERTENCIA: Variable BOT_TOKEN no configurada.")
# ============================================

ESPERANDO_USUARIO, ESPERANDO_CANCHA, ESPERANDO_HORA = range(3)

user_data = {}
reserva_en_proceso = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def install_chromium():
    import subprocess
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    except: pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎾 *Bot Reserva Las Condes - VALIDAR*\n\n"
        "Comandos:\n/config - Configurar\n/test - Probar conexión\n/auto - Activar reserva\n/detener - Parar",
        parse_mode='Markdown'
    )

async def config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Envíame tu RUT (ej: 12345678):")
    return ESPERANDO_USUARIO

async def guardar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data: user_data[user_id] = {}
    rut = update.message.text.strip().replace(".", "").replace("-", "")
    user_data[user_id]['rut'] = rut
    await update.message.reply_text("✅ RUT guardado. ¿Qué cancha? (1-12):")
    return ESPERANDO_CANCHA

async def guardar_cancha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id]['cancha'] = int(update.message.text.strip())
    await update.message.reply_text("✅ Cancha guardada. ¿Hora? (6-23):")
    return ESPERANDO_HORA

async def guardar_hora(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id]['hora'] = int(update.message.text.strip())
    await update.message.reply_text("✅ Configuración lista. Usa /test para probar.")
    return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelado.")
    return ConversationHandler.END

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        await update.message.reply_text("Sin configuración.")
        return
    c = user_data[user_id]
    await update.message.reply_text(f"RUT: {c.get('rut')}\nCancha: {c.get('cancha')}\nHora: {c.get('hora')}")

# ==============================================================================
# 🎯 FUNCIÓN CRÍTICA DE RESERVA
# ==============================================================================
async def intentar_reserva(rut, cancha, hora, chat_id, context, modo_test=False):
    async with async_playwright() as p:
        # Lanzar navegador
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
        )
        
        # Contexto con tamaño de pantalla real
        context_browser = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        page = await context_browser.new_page()
        
        try:
            logging.info("📍 Navegando...")
            await page.goto("https://reservadehoras.lascondes.cl/#/agenda/28/agendar", timeout=30000, wait_until="networkidle")
            
            # 1. Esperar y llenar RUT
            logging.info("🔍 Buscando input RUT...")
            # Selector específico para Ant Design
            await page.wait_for_selector('input.ant-input', timeout=15000)
            await page.fill('input.ant-input', rut)
            
            # 2. BUSCAR BOTÓN VALIDAR (Aquí estaba el error antes)
            logging.info("🔍 Buscando botón Validar...")
            
            # Hacemos click específicamente en el botón que contiene "Validar"
            # Usamos un selector robusto de Playwright
            btn_validar = page.locator("button", has_text="Validar")
            
            if await btn_validar.count() > 0:
                await btn_validar.first.click()
                logging.info("✅ Click en Validar")
            else:
                # Fallback: intentar Enter
                logging.warning("⚠️ No encontré botón Validar, probando Enter...")
                await page.press('input.ant-input', 'Enter')

            await page.wait_for_timeout(3000)

            # MODO TEST: Verificar si pasamos el login
            if modo_test:
                # Si todavía vemos el botón Validar, es que falló
                if await btn_validar.count() > 0 and await btn_validar.first.is_visible():
                    msg = "⚠️ El botón se clickeó pero la página no avanzó. ¿RUT válido?"
                else:
                    msg = "✅ **TEST EXITOSO**\n\nLogin superado (botón Validar funcionó).\nEl bot está listo para reservar."
                
                await browser.close()
                return msg

            # 3. BUSCAR HORA Y RESERVAR
            logging.info(f"🔍 Buscando hora {hora}:00...")
            
            # Selector para el botón de la hora específica
            hora_btn = page.locator(f"button", has_text=f"{hora}:00")
            
            if await hora_btn.count() > 0:
                await hora_btn.first.click()
                
                # Confirmar
                await page.wait_for_timeout(500)
                await page.click('button:has-text("Confirmar"), button:has-text("Reservar")')
                
                await browser.close()
                return f"🎉 **RESERVADA**\nCancha: {cancha}\nHora: {hora}:00"
            
            await browser.close()
            return "NO_DISPONIBLE"

        except Exception as e:
            await browser.close()
            raise e

# ==============================================================================

async def test_reserva(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        await update.message.reply_text("Usa /config primero")
        return
    
    await update.message.reply_text("🧪 Probando conexión con botón 'Validar'...")
    config = user_data[user_id]
    
    try:
        res = await intentar_reserva(config['rut'], config['cancha'], config['hora'], update.effective_chat.id, context, modo_test=True)
        await update.message.reply_text(res, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {str(e)}")

async def reservar_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        await update.message.reply_text("Usa /config primero")
        return
    
    ahora = datetime.datetime.now()
    if ahora.hour >= 18 and ahora.minute > 5:
        inicio = ahora.replace(hour=17, minute=50, second=0) + datetime.timedelta(days=1)
    elif ahora.hour == 17 and ahora.minute >= 50:
        await iniciar_reserva_inmediata(update, context)
        return
    else:
        inicio = ahora.replace(hour=17, minute=50, second=0)
    
    secs = (inicio - ahora).total_seconds()
    await update.message.reply_text(f"⏰ Automático activado para las 17:50 ({int(secs//60)} min).")
    context.job_queue.run_once(callback_iniciar_reserva, when=secs, data={'user_id': user_id, 'chat_id': update.effective_chat.id}, name=f'inicio_{user_id}')

async def iniciar_reserva_inmediata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = user_data[update.effective_user.id]
    await update.message.reply_text("🔴 Iniciando...")
    asyncio.create_task(ejecutar_reserva_loop(config['rut'], config['cancha'], config['hora'], update.effective_chat.id, context, update.effective_user.id))

async def callback_iniciar_reserva(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    config = user_data[data['user_id']]
    await context.bot.send_message(data['chat_id'], "🔴 Iniciando...")
    asyncio.create_task(ejecutar_reserva_loop(config['rut'], config['cancha'], config['hora'], data['chat_id'], context, data['user_id']))

async def ejecutar_reserva_loop(rut, cancha, hora, chat_id, context, user_id):
    reserva_en_proceso[user_id] = True
    ahora = datetime.datetime.now()
    fin = ahora.replace(hour=18, minute=5, second=0)
    if fin < ahora: fin += datetime.timedelta(days=1)
    
    intentos = 0
    while datetime.datetime.now() < fin and reserva_en_proceso.get(user_id):
        intentos += 1
        try:
            res = await intentar_reserva(rut, cancha, hora, chat_id, context)
            if "RESERVADA" in res:
                await context.bot.send_message(chat_id, res)
                break
            if intentos % 30 == 0:
                await context.bot.send_message(chat_id, f"🔄 Buscando ({intentos})...")
            await asyncio.sleep(2)
        except:
            await asyncio.sleep(2)
    reserva_en_proceso[user_id] = False

async def detener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reserva_en_proceso[update.effective_user.id] = False
    await update.message.reply_text("Detenido.")

def main():
    install_chromium()
    if not TOKEN: return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("config", config))
    app.add_handler(CommandHandler("test", test_reserva))
    app.add_handler(CommandHandler("auto", reservar_auto))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("detener", detener))
    
    # Conversación
    conv = ConversationHandler(
        entry_points=[CommandHandler("config", config)],
        states={
            ESPERANDO_USUARIO: [MessageHandler(filters.TEXT, guardar_usuario)],
            ESPERANDO_CANCHA: [MessageHandler(filters.TEXT, guardar_cancha)],
            ESPERANDO_HORA: [MessageHandler(filters.TEXT, guardar_hora)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)]
    )
    app.add_handler(conv)
    
    print("Bot iniciado...")
    app.run_polling()

if __name__ == "__main__":
    main()
