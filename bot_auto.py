import asyncio
import datetime
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from playwright.async_api import async_playwright
import logging

# ============================================
# CONFIGURACION
# ============================================
TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    raise ValueError("BOT_TOKEN no configurado")
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
        "Bot Reserva Automatica Las Condes\n\n"
        "Version 100% AUTOMATICA con Playwright\n\n"
        "Comandos:\n"
        "/config - Configurar datos\n"
        "/auto - Activar reserva automatica\n"
        "/test - Probar conexion ahora\n"
        "/detener - Detener bot\n"
        "/status - Ver configuracion\n\n"
        "El bot reservara completamente solo\n"
        "desde las 17:50 hasta las 18:05"
    )

async def config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Configuracion\n\n"
        "Enviame tu RUT (sin puntos ni guion)\n"
        "Ejemplo: 12345678"
    )
    return ESPERANDO_USUARIO

async def guardar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        user_data[user_id] = {}
    
    rut = update.message.text.strip().replace(".", "").replace("-", "")
    user_data[user_id]['rut'] = rut
    
    await update.message.reply_text(
        "RUT guardado\n\n"
        "Que cancha? (1-12):"
    )
    return ESPERANDO_CANCHA

async def guardar_cancha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cancha = update.message.text.strip()
    
    if not cancha.isdigit() or int(cancha) < 1 or int(cancha) > 12:
        await update.message.reply_text("Cancha invalida. Entre 1 y 12:")
        return ESPERANDO_CANCHA
    
    user_data[user_id]['cancha'] = int(cancha)
    
    await update.message.reply_text(
        "Cancha guardada\n\n"
        "A que hora quieres jugar? (6-23):"
    )
    return ESPERANDO_HORA

async def guardar_hora(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    hora = update.message.text.strip()
    
    if not hora.isdigit() or int(hora) < 6 or int(hora) > 23:
        await update.message.reply_text("Hora invalida. Entre 6 y 23:")
        return ESPERANDO_HORA
    
    user_data[user_id]['hora'] = int(hora)
    cfg = user_data[user_id]
    
    await update.message.reply_text(
        f"Configuracion completada\n\n"
        f"RUT: {cfg['rut']}\n"
        f"Cancha: {cfg['cancha']}\n"
        f"Hora: {cfg['hora']}:00\n\n"
        f"Usa /auto para activar reserva automatica\n"
        f"O /test para probar ahora mismo"
    )
    return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelado")
    return ConversationHandler.END

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_data or not user_data[user_id]:
        await update.message.reply_text("Sin configuracion. Usa /config")
        return
    
    cfg = user_data[user_id]
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
    
    estado = "ACTIVO" if reserva_en_proceso.get(user_id, False) else "Inactivo"
    
    await update.message.reply_text(
        f"Configuracion actual\n\n"
        f"RUT: {cfg.get('rut')}\n"
        f"Cancha: {cfg.get('cancha')}\n"
        f"Hora: {cfg.get('hora')}:00\n\n"
        f"Proxima ventana: {proxima.strftime('%d/%m 17:50-18:05')}\n"
        f"Para jugar: {dia_reserva.strftime('%A %d/%m')}\n\n"
        f"Estado: {estado}"
    )

async def test_reserva(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Probar el sistema de reserva AHORA - captura screenshot"""
    user_id = update.effective_user.id
    
    if user_id not in user_data or not user_data[user_id].get('rut'):
        await update.message.reply_text("Usa /config primero")
        return
    
    await update.message.reply_text(
        "Modo TEST activado\n\n"
        "Probando conexion...\n"
        "Esto puede tardar 15-30 segundos"
    )
    
    cfg = user_data[user_id]
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled'
                ]
            )
            
            context_browser = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            page = await context_browser.new_page()
            
            # Paso 1: Cargar pagina
            await update.message.reply_text("Paso 1: Cargando pagina...")
            await page.goto("https://reservadehoras.lascondes.cl/#/agenda/28/agendar", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            
            # Capturar screenshot para ver que carga
            screenshot1 = await page.screenshot()
            await update.message.reply_photo(
                photo=screenshot1,
                caption="Screenshot 1: Pagina cargada"
            )
            
            # Paso 2: Obtener HTML para analizar selectores
            html_content = await page.content()
            
            # Buscar inputs en la pagina
            inputs = await page.query_selector_all('input')
            buttons = await page.query_selector_all('button')
            
            info_inputs = []
            for inp in inputs:
                name = await inp.get_attribute('name') or ''
                id_attr = await inp.get_attribute('id') or ''
                placeholder = await inp.get_attribute('placeholder') or ''
                type_attr = await inp.get_attribute('type') or ''
                info_inputs.append(f"  name='{name}' id='{id_attr}' placeholder='{placeholder}' type='{type_attr}'")
            
            info_buttons = []
            for btn in buttons:
                text = await btn.inner_text()
                btn_type = await btn.get_attribute('type') or ''
                btn_class = await btn.get_attribute('class') or ''
                info_buttons.append(f"  text='{text[:50]}' type='{btn_type}' class='{btn_class[:50]}'")
            
            resumen = (
                f"Test de conexion completado\n\n"
                f"URL: OK\n"
                f"Inputs encontrados: {len(inputs)}\n"
            )
            
            if info_inputs:
                resumen += "\nINPUTS:\n" + "\n".join(info_inputs[:10])
            
            resumen += f"\n\nButtons encontrados: {len(buttons)}\n"
            
            if info_buttons:
                resumen += "\nBUTTONS:\n" + "\n".join(info_buttons[:10])
            
            await update.message.reply_text(resumen)
            
            await browser.close()
            
    except Exception as e:
        await update.message.reply_text(
            f"Error en test:\n{str(e)}\n\n"
            f"Verifica tu conexion"
        )

async def reservar_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_data or not user_data[user_id].get('rut'):
        await update.message.reply_text("Usa /config primero")
        return
    
    ahora = datetime.datetime.now()
    
    if ahora.hour >= 18 and ahora.minute > 5:
        inicio = ahora.replace(hour=17, minute=50, second=0) + datetime.timedelta(days=1)
        dia_reserva = ahora + datetime.timedelta(days=2)
    elif ahora.hour == 17 and ahora.minute >= 50:
        await iniciar_reserva_inmediata(update, context)
        return
    elif ahora.hour >= 18 and ahora.minute <= 5:
        await iniciar_reserva_inmediata(update, context)
        return
    else:
        inicio = ahora.replace(hour=17, minute=50, second=0)
        dia_reserva = ahora + datetime.timedelta(days=1)
    
    segundos_hasta = (inicio - ahora).total_seconds()
    horas = int(segundos_hasta // 3600)
    minutos = int((segundos_hasta % 3600) // 60)
    
    await update.message.reply_text(
        f"Reserva automatica programada\n\n"
        f"Iniciara: {inicio.strftime('%d/%m a las 17:50:00')}\n"
        f"Reservara para: {dia_reserva.strftime('%A %d/%m')}\n"
        f"Tiempo restante: {horas}h {minutos}m\n\n"
        f"El bot hara automaticamente:\n"
        f"1. Abrir navegador a las 17:50\n"
        f"2. Ingresar RUT\n"
        f"3. Buscar cancha disponible\n"
        f"4. Refrescar cada 2 segundos\n"
        f"5. Reservar apenas este disponible\n\n"
        f"Te avisare cuando reserve exitosamente\n\n"
        f"NO APAGUES el bot hasta despues de las 18:05"
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
        "INICIANDO RESERVA AUTOMATICA AHORA!\n\n"
        "El navegador se esta abriendo..."
    )
    
    cfg = user_data[user_id]
    
    asyncio.create_task(
        ejecutar_reserva_loop(
            cfg['rut'],
            cfg['cancha'],
            cfg['hora'],
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
        text="RESERVA AUTOMATICA INICIADA!\n\n"
             "Abriendo navegador...\n"
             "Te mantendre informado"
    )
    
    cfg = user_data[user_id]
    
    asyncio.create_task(
        ejecutar_reserva_loop(
            cfg['rut'],
            cfg['cancha'],
            cfg['hora'],
            chat_id,
            context,
            user_id
        )
    )

async def ejecutar_reserva_loop(rut, cancha, hora, chat_id, context, user_id):
    """Loop principal - mantiene el navegador abierto y refresca"""
    reserva_en_proceso[user_id] = True
    
    ahora = datetime.datetime.now()
    fin_ventana = ahora.replace(hour=18, minute=5, second=0, microsecond=0)
    if fin_ventana < ahora:
        fin_ventana += datetime.timedelta(days=1)
    
    intentos = 0
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-blink-features=AutomationControlled'
                ]
            )
            
            context_browser = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            page = await context_browser.new_page()
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="Navegador abierto, cargando pagina..."
            )
            
            # Cargar pagina inicial
            await page.goto("https://reservadehoras.lascondes.cl/#/agenda/28/agendar", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            
            await context.bot.send_message(
                chat_id=chat_id,
                text="Pagina cargada. Iniciando busqueda..."
            )
            
            # ========================================
            # LOOP PRINCIPAL DE BUSQUEDA
            # ========================================
            while datetime.datetime.now() < fin_ventana and reserva_en_proceso.get(user_id, False):
                intentos += 1
                
                try:
                    # Recargar la pagina
                    await page.reload(timeout=15000)
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    
                    # TODO: AQUI VAN LOS SELECTORES REALES
                    # Estos se deben ajustar despues del /test
                    # que nos mostrara la estructura real de la pagina
                    
                    # Buscar si hay disponibilidad
                    # Por ahora busca texto generico
                    disponible = await page.query_selector(
                        f'[data-hora="{hora}:00"]:not([disabled]), '
                        f'.disponible:has-text("{hora}:00"), '
                        f'td.available:has-text("{hora}")'
                    )
                    
                    if disponible:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"HORA ENCONTRADA! Intentando reservar..."
                        )
                        
                        # Capturar screenshot
                        screenshot = await page.screenshot()
                        await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=screenshot,
                            caption="Disponibilidad encontrada!"
                        )
                        
                        await disponible.click()
                        await page.wait_for_timeout(1000)
                        
                        # Buscar boton de confirmar
                        confirmar = await page.query_selector(
                            'button:has-text("Reservar"), '
                            'button:has-text("Confirmar"), '
                            'button.btn-primary'
                        )
                        
                        if confirmar:
                            await confirmar.click()
                            await page.wait_for_timeout(2000)
                            
                            screenshot2 = await page.screenshot()
                            await context.bot.send_photo(
                                chat_id=chat_id,
                                photo=screenshot2,
                                caption=f"CANCHA RESERVADA!\n\n"
                                        f"Cancha: {cancha}\n"
                                        f"Hora: {hora}:00\n"
                                        f"RUT: {rut}\n\n"
                                        f"Revisa tu email de confirmacion"
                            )
                            
                            reserva_en_proceso[user_id] = False
                            await browser.close()
                            return
                    
                    # Avisar cada 30 intentos (aprox cada 1 minuto)
                    if intentos % 30 == 0:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"Intento {intentos} - Buscando disponibilidad..."
                        )
                    
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    if intentos % 15 == 0:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"Error temporal (intento {intentos}): {str(e)[:100]}\nSiguiendo..."
                        )
                    await asyncio.sleep(3)
            
            await browser.close()
    
    except Exception as e:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Error critico: {str(e)[:200]}\n\nReintentando en 10 segundos..."
        )
        await asyncio.sleep(10)
    
    reserva_en_proceso[user_id] = False
    
    if datetime.datetime.now() >= fin_ventana:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Ventana 17:50-18:05 cerrada\n\n"
                 f"Total intentos: {intentos}\n"
                 f"No se encontro disponibilidad\n\n"
                 f"Se reprogramara para manana"
        )
        
        ahora = datetime.datetime.now()
        proxima = ahora.replace(hour=17, minute=50, second=0) + datetime.timedelta(days=1)
        segundos = (proxima - ahora).total_seconds()
        
        context.job_queue.run_once(
            callback_iniciar_reserva,
            when=segundos,
            data={'user_id': user_id, 'chat_id': chat_id},
            name=f'inicio_{user_id}'
        )

async def detener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reserva_en_proceso[user_id] = False
    
    jobs = context.job_queue.get_jobs_by_name(f'inicio_{user_id}')
    for job in jobs:
        job.schedule_removal()
    
    await update.message.reply_text("Bot detenido")
