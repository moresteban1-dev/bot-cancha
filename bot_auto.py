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
    # Fallback para pruebas locales si tienes el token
    # TOKEN = "TU_TOKEN_AQUI" 
    pass

if not TOKEN:
    print("❌ ERROR: Variable BOT_TOKEN no encontrada.")
    print("Por favor configúrala en Railway > Variables")
# ============================================

ESPERANDO_USUARIO, ESPERANDO_CANCHA, ESPERANDO_HORA = range(3)

user_data = {}
reserva_en_proceso = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Instalar Chromium si es necesario
def install_chromium():
    import subprocess
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    except:
        pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎾 *Bot Reserva Automática Las Condes*\n\n"
        "🤖 Versión FINAL (Botón Validar)\n\n"
        "Comandos:\n"
        "/config - Configurar datos\n"
        "/auto - Activar reserva automática\n"
        "/test - Probar conexión ahora\n"
        "/detener - Detener bot\n"
        "/status - Ver configuración",
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
    config = user_data[user_id]
    await update.message.reply_text(
        f"✅ *Listo*\nRUT: {config['rut']}\nCancha: {config['cancha']}\nHora: {config['hora']}:00\n\nUsa /test para probar.",
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelado.")
    return ConversationHandler.END

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        await update.message.reply_text("No configurado.")
        return
    c = user_data[user_id]
    await update.message.reply_text(
        f"📋 RUT: {c.get('rut')}\nCancha: {c.get('cancha')}\nHora: {c.get('hora')}:00"
    )

async def test_reserva(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        await update.message.reply_text("Usa /config primero")
        return
    
    await update.message.reply_text("🧪 Probando 'Validar'...")
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
    
    # Lógica de horario (17:50)
    if ahora.hour >= 18 and ahora.minute > 5:
        inicio = ahora.replace(hour=17, minute=50, second=0) + datetime.timedelta(days=1)
    elif ahora.hour == 17 and ahora.minute >= 50:
        await iniciar_reserva_inmediata(update, context)
        return
    else:
        inicio = ahora.replace(hour=17, minute=50, second=0)

    secs = (inicio - ahora).total_seconds()
    
    await update.message.reply_text(f"⏰ Programado para las 17:50 ({int(secs//60)} min).")
    context.job_queue.run_once(callback_iniciar_reserva, when=secs, data={'user_id': user_id, 'chat_id': update.effective_chat.id}, name=f'inicio_{user_id}')

async def iniciar_reserva_inmediata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    config = user_data[update.effective_user.id]
    await update.message.reply_text("🔴 Iniciando...")
    asyncio.create_task(ejecutar_reserva_loop(config['rut'], config['cancha'], config['hora'], update.effective_chat.id, context, update.effective_user.id))

async def callback_iniciar_reserva(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    config = user_data[data['user_id']]
    await context.bot.send_message(data['chat_id'], "🔴 Iniciando automátic...")
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
                await context.bot.send_message(chat_id, f"🔄 Intento {intentos}...")
            await asyncio.sleep(2)
        except:
            await asyncio.sleep(2)
    
    reserva_en_proceso[user_id] = False

async def intentar_reserva(rut, cancha, hora, chat_id, context, modo_test=False):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
        )
        context_browser = await browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context_browser.new_page()
        
        try:
            logging.info("Navegando...")
            await page.goto("https://reservadehoras.lascondes.cl/#/agenda/28/agendar", timeout=60000)
            
            # 1. Esperar campo RUT
            logging.info("Buscando RUT...")
            await page.wait_for_selector('input[name="rut"]', state='visible', timeout=20000)
            
            # 2. Llenar RUT
            await page.fill('input[name="rut"]', rut)
            
            # 3. CLICK EN "VALIDAR" (La clave de todo)
            logging.info("Buscando botón Validar...")
            
            # Probamos varios selectores para asegurar el click en "Validar"
            boton_encontrado = False
            
            # Estrategia A: Texto exacto
            if not boton_encontrado:
                try:
                    await page.click('text="Validar"', timeout=3000)
                    boton_encontrado = True
                except: pass

            # Estrategia B: Botón que contiene texto
            if not boton_encontrado:
                try:
                    await page.click('button:has-text("Validar")', timeout=3000)
                    boton_encontrado = True
                except: pass
                
            # Estrategia C: Enter en el input (a veces activa el form)
            if not boton_encontrado:
                await page.press('input[name="rut"]', 'Enter')
                boton_encontrado = True

            if modo_test:
                await page.wait_for_timeout(3000)
                # Verificar si pasó
                if await page.query_selector('text=Validar') and not await page.query_selector('.hora-disponible'):
                     msg = "⚠️ Botón clickeado, pero parece que seguimos en el login. Verifica RUT."
                else:
                     msg = "✅ **TEST EXITOSO**\n\nBotón 'Validar' presionado correctamente.\nLogin superado."
                
                await browser.close()
                return msg

            # === FASE DE RESERVA ===
            
            # Esperar a que carguen las horas (puede tardar un poco tras validar)
            await page.wait_for_timeout(2000)
            
            # Buscar hora específica
            try:
                # Selector genérico para cualquier botón que tenga la hora (ej: "19:00")
                await page.click(f'button:has-text("{hora}:00")', timeout=2000)
                
                # Confirmar
                await page.wait_for_timeout(500)
                await page.click('button:has-text("Confirmar"), button:has-text("Reservar")', timeout=5000)
                
                await browser.close()
                return f"🎉🎉🎉 **CANCHA RESERVADA** 🎉🎉🎉\n\nCancha: {cancha}\nHora: {hora}:00"
            except:
                await browser.close()
                return "NO_DISPONIBLE"

        except Exception as e:
            await browser.close()
            raise e

async def detener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reserva_en_proceso[update.effective_user.id] = False
    await update.message.reply_text("Detenido.")

def main():
    install_chromium()
    if not TOKEN: return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("config", config))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("test", test_reserva))
    app.add_handler(CommandHandler("auto", reservar_auto))
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
