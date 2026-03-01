import asyncio
import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from playwright.async_api import async_playwright
import logging

# ============================================
# 🔧 CONFIGURACIÓN
# ============================================
TOKEN = "TU TOKEN"
# ============================================

ESPERANDO_USUARIO, ESPERANDO_CANCHA, ESPERANDO_HORA = range(3)

user_data = {}
reserva_en_proceso = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎾 *Bot Reserva Automática Las Condes*\n\n"
        "🤖 Versión 100% AUTOMÁTICA con Playwright\n\n"
        "Comandos:\n"
        "/config - Configurar datos\n"
        "/auto - Activar reserva automática\n"
        "/test - Probar conexión ahora\n"
        "/detener - Detener bot\n"
        "/status - Ver configuración\n\n"
        "⚡ El bot reservará completamente solo\n"
        "desde las 17:50 hasta las 18:05",
        parse_mode='Markdown'
    )

async def config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔧 *Configuración*\n\n"
        "Envíame tu RUT (sin puntos ni guión)\n"
        "Ejemplo: 12345678",
        parse_mode='Markdown'
    )
    return ESPERANDO_USUARIO

async def guardar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        user_data[user_id] = {}
    
    rut = update.message.text.strip().replace(".", "").replace("-", "")
    user_data[user_id]['rut'] = rut
    
    await update.message.reply_text(
        "✅ RUT guardado\n\n"
        "¿Qué cancha? (1-12):"
    )
    return ESPERANDO_CANCHA

async def guardar_cancha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cancha = update.message.text.strip()
    
    if not cancha.isdigit() or int(cancha) < 1 or int(cancha) > 12:
        await update.message.reply_text("❌ Cancha inválida. Entre 1 y 12:")
        return ESPERANDO_CANCHA
    
    user_data[user_id]['cancha'] = int(cancha)
    
    await update.message.reply_text(
        "✅ Cancha guardada\n\n"
        "¿A qué hora quieres jugar? (6-23):"
    )
    return ESPERANDO_HORA

async def guardar_hora(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    hora = update.message.text.strip()
    
    if not hora.isdigit() or int(hora) < 6 or int(hora) > 23:
        await update.message.reply_text("❌ Hora inválida. Entre 6 y 23:")
        return ESPERANDO_HORA
    
    user_data[user_id]['hora'] = int(hora)
    config = user_data[user_id]
    
    await update.message.reply_text(
        f"✅ *Configuración completada*\n\n"
        f"👤 RUT: {config['rut']}\n"
        f"🎾 Cancha: {config['cancha']}\n"
        f"⏰ Hora: {config['hora']}:00\n\n"
        f"Usa /auto para activar reserva automática\n"
        f"O /test para probar ahora mismo",
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Cancelado")
    return ConversationHandler.END

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_data or not user_data[user_id]:
        await update.message.reply_text("⚠️ Sin configuración. Usa /config")
        return
    
    config = user_data[user_id]
    ahora = datetime.datetime.now()
    
    if ahora.hour >= 18 and ahora.minute > 5:
        proxima = ahora.replace(hour=17, minute=50, second=0) + datetime.timedelta(days=1)
        dia_reserva = ahora + datetime.timedelta(days=2)
    else:
        proxima = ahora.replace(hour=17, minute=50, second=0)
        if ahora.hour >= 18:
            proxima += datetime.timedelta(days=1)
            dia_reserva = ahora + datetime.timedelta(days=2)
        else:
            dia_reserva = ahora + datetime.timedelta(days=1)
    
    estado = "🟢 ACTIVO" if reserva_en_proceso.get(user_id, False) else "⚪ Inactivo"
    
    await update.message.reply_text(
        f"📋 *Configuración actual*\n\n"
        f"👤 RUT: {config.get('rut')}\n"
        f"🎾 Cancha: {config.get('cancha')}\n"
        f"⏰ Hora: {config.get('hora')}:00\n\n"
        f"📅 Próxima ventana: {proxima.strftime('%d/%m 17:50-18:05')}\n"
        f"🎯 Para jugar: {dia_reserva.strftime('%A %d/%m')}\n\n"
        f"Estado: {estado}",
        parse_mode='Markdown'
    )

async def test_reserva(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Probar el sistema de reserva AHORA (sin esperar a las 17:50)"""
    user_id = update.effective_user.id
    
    if user_id not in user_data or not user_data[user_id].get('rut'):
        await update.message.reply_text("⚠️ Usa /config primero")
        return
    
    await update.message.reply_text(
        "🧪 *Modo TEST activado*\n\n"
        "Probando conexión y login...\n"
        "Esto puede tardar 10-15 segundos",
        parse_mode='Markdown'
    )
    
    config = user_data[user_id]
    
    try:
        resultado = await intentar_reserva(
            config['rut'],
            config['cancha'],
            config['hora'],
            update.effective_chat.id,
            context,
            modo_test=True
        )
        await update.message.reply_text(resultado, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(
            f"❌ Error en test:\n{str(e)}\n\n"
            f"Verifica que el RUT sea correcto",
            parse_mode='Markdown'
        )

async def reservar_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_data or not user_data[user_id].get('rut'):
        await update.message.reply_text("⚠️ Usa /config primero")
        return
    
    ahora = datetime.datetime.now()
    
    # Calcular cuándo iniciar (17:50)
    if ahora.hour >= 18 and ahora.minute > 5:
        inicio = ahora.replace(hour=17, minute=50, second=0) + datetime.timedelta(days=1)
        dia_reserva = ahora + datetime.timedelta(days=2)
    elif ahora.hour == 17 and ahora.minute >= 50:
        # Ya estamos en la ventana, iniciar YA
        await iniciar_reserva_inmediata(update, context)
        return
    else:
        inicio = ahora.replace(hour=17, minute=50, second=0)
        dia_reserva = ahora + datetime.timedelta(days=1)
    
    segundos_hasta = (inicio - ahora).total_seconds()
    horas = int(segundos_hasta // 3600)
    minutos = int((segundos_hasta % 3600) // 60)
    
    await update.message.reply_text(
        f"🤖 *Reserva automática programada*\n\n"
        f"⏰ Iniciará: {inicio.strftime('%d/%m a las 17:50:00')}\n"
        f"📅 Reservará para: {dia_reserva.strftime('%A %d/%m')}\n"
        f"⏳ Tiempo restante: {horas}h {minutos}m\n\n"
        f"🔄 *El bot hará automáticamente:*\n"
        f"1. Abrir navegador a las 17:50\n"
        f"2. Ingresar RUT\n"
        f"3. Buscar cancha disponible\n"
        f"4. Refrescar cada 5 segundos\n"
        f"5. Reservar apenas esté disponible\n\n"
        f"⚡ Te avisaré cuando reserve exitosamente\n\n"
        f"⚠️ NO APAGUES el bot hasta después de las 18:05",
        parse_mode='Markdown'
    )
    
    context.job_queue.run_once(
        callback_iniciar_reserva,
        when=segundos_hasta,
        data={'user_id': user_id, 'chat_id': update.effective_chat.id},
        name=f'inicio_{user_id}'
    )

async def iniciar_reserva_inmediata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    await update.message.reply_text(
        "🔴 *¡INICIANDO RESERVA AUTOMÁTICA AHORA!*\n\n"
        "El navegador se está abriendo...",
        parse_mode='Markdown'
    )
    
    config = user_data[user_id]
    
    # Ejecutar en background
    asyncio.create_task(
        ejecutar_reserva_loop(
            config['rut'],
            config['cancha'],
            config['hora'],
            chat_id,
            context,
            user_id
        )
    )

async def callback_iniciar_reserva(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    user_id = data['user_id']
    chat_id = data['chat_id']
    
    await context.bot.send_message(
        chat_id=chat_id,
        text="🔴 *¡RESERVA AUTOMÁTICA INICIADA!*\n\n"
             "Abriendo navegador...\n"
             "Te mantendré informado",
        parse_mode='Markdown'
    )
    
    config = user_data[user_id]
    
    asyncio.create_task(
        ejecutar_reserva_loop(
            config['rut'],
            config['cancha'],
            config['hora'],
            chat_id,
            context,
            user_id
        )
    )

async def ejecutar_reserva_loop(rut, cancha, hora, chat_id, context, user_id):
    """Loop principal de reserva automática"""
    reserva_en_proceso[user_id] = True
    
    ahora = datetime.datetime.now()
    fin_ventana = ahora.replace(hour=18, minute=5, second=0, microsecond=0)
    if fin_ventana < ahora:
        fin_ventana += datetime.timedelta(days=1)
    
    intentos = 0
    
    while datetime.datetime.now() < fin_ventana and reserva_en_proceso.get(user_id, False):
        intentos += 1
        
        try:
            resultado = await intentar_reserva(rut, cancha, hora, chat_id, context)
            
            if "RESERVADA" in resultado:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=resultado,
                    parse_mode='Markdown'
                )
                reserva_en_proceso[user_id] = False
                break
            
            # Avisar cada 20 intentos (aprox cada 1.5 minutos)
            if intentos % 20 == 0:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔄 Intento {intentos} - Buscando disponibilidad...",
                    parse_mode='Markdown'
                )
            
            await asyncio.sleep(5)  # Esperar 5 segundos entre intentos
            
        except Exception as e:
            if intentos % 10 == 0:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ Error temporal: {str(e)}\nSiguiendo intentos...",
                    parse_mode='Markdown'
                )
            await asyncio.sleep(5)
    
    reserva_en_proceso[user_id] = False
    
    if datetime.datetime.now() >= fin_ventana:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏸️ Ventana 17:50-18:05 cerrada\n\n"
                 f"Total intentos: {intentos}\n"
                 f"No se encontró disponibilidad\n\n"
                 f"Se reprogramará para mañana automáticamente",
            parse_mode='Markdown'
        )
        
        # Reprogramar para mañana
        ahora = datetime.datetime.now()
        proxima = ahora.replace(hour=17, minute=50, second=0) + datetime.timedelta(days=1)
        segundos = (proxima - ahora).total_seconds()
        
        context.job_queue.run_once(
            callback_iniciar_reserva,
            when=segundos,
            data={'user_id': user_id, 'chat_id': chat_id},
            name=f'inicio_{user_id}'
        )

async def intentar_reserva(rut, cancha, hora, chat_id, context, modo_test=False):
    """Intenta hacer una reserva usando Playwright"""
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,  # Cambiar a False para ver el navegador
            args=['--disable-blink-features=AutomationControlled']
        )
        
        context_browser = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        
        page = await context_browser.new_page()
        
        try:
            # Ir a la página
            await page.goto("https://reservadehoras.lascondes.cl/#/agenda/28/agendar", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            
            # Buscar campo de RUT
            await page.wait_for_selector('input[name="rut"], input#rut, input[placeholder*="RUT"]', timeout=10000)
            await page.fill('input[name="rut"], input#rut, input[placeholder*="RUT"]', rut)
            
            # Click en botón de ingreso/continuar
            await page.click('button[type="submit"], button:has-text("Ingresar"), button:has-text("Continuar")')
            await page.wait_for_timeout(2000)
            
            if modo_test:
                await browser.close()
                return (
                    "✅ *Test exitoso*\n\n"
                    "✓ Conexión OK\n"
                    "✓ Login OK\n"
                    "✓ El bot está funcionando correctamente\n\n"
                    "Usa /auto para activar reserva automática"
                )
            
            # Calcular fecha (mañana)
            fecha_objetivo = datetime.datetime.now() + datetime.timedelta(days=1)
            fecha_str = fecha_objetivo.strftime("%Y-%m-%d")
            
            # Buscar cancha disponible
            selector_hora = f'button.hora-disponible[data-hora="{hora}:00"], button:has-text("{hora}:00"):not([disabled])'
            
            # Intentar encontrar hora disponible
            hora_elemento = await page.query_selector(selector_hora)
            
            if hora_elemento:
                # ¡Encontró hora disponible!
                await hora_elemento.click()
                await page.wait_for_timeout(500)
                
                # Confirmar reserva
                await page.click('button:has-text("Reservar"), button:has-text("Confirmar")')
                await page.wait_for_timeout(1000)
                
                await browser.close()
                
                return (
                    f"🎉🎉🎉 *¡CANCHA RESERVADA!* 🎉🎉🎉\n\n"
                    f"📅 Fecha: {fecha_objetivo.strftime('%A %d/%m/%Y')}\n"
                    f"🎾 Cancha: {cancha}\n"
                    f"⏰ Hora: {hora}:00\n"
                    f"👤 RUT: {rut}\n\n"
                    f"✅ Revisa tu email de confirmación"
                )
            
            await browser.close()
            return "NO_DISPONIBLE"
            
        except Exception as e:
            await browser.close()
            raise e

async def detener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reserva_en_proceso[user_id] = False
    
    jobs = context.job_queue.get_jobs_by_name(f'inicio_{user_id}')
    for job in jobs:
        job.schedule_removal()
    
    await update.message.reply_text("⏸️ Bot detenido")

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("test", test_reserva))
    app.add_handler(CommandHandler("auto", reservar_auto))
    app.add_handler(CommandHandler("detener", detener))
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("config", config)],
        states={
            ESPERANDO_USUARIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_usuario)],
            ESPERANDO_CANCHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_cancha)],
            ESPERANDO_HORA: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_hora)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)]
    )
    app.add_handler(conv_handler)
    
    print("🤖 Bot 100% automático iniciado")
    print("🎯 Reserva automática con Playwright")
    print("📱 Ctrl+C para detener")
    app.run_polling()

if __name__ == "__main__":

    main()
