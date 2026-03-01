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

def formatear_rut(rut_limpio):
    """Formatea RUT: 111111111 -> 11.111.111-1"""
    rut_limpio = rut_limpio.replace(".", "").replace("-", "").replace(" ", "").upper()
    if len(rut_limpio) < 2:
        return rut_limpio
    dv = rut_limpio[-1]
    cuerpo = rut_limpio[:-1]
    resultado = ""
    for i, digito in enumerate(reversed(cuerpo)):
        if i > 0 and i % 3 == 0:
            resultado = "." + resultado
        resultado = digito + resultado
    return f"{resultado}-{dv}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot Reserva Automatica Las Condes\n\n"
        "Comandos:\n"
        "/config - Configurar datos\n"
        "/test - Probar conexion (MUY IMPORTANTE hacer primero)\n"
        "/auto - Activar reserva automatica\n"
        "/detener - Detener bot\n"
        "/status - Ver configuracion\n\n"
        "IMPORTANTE: Haz /test despues de /config\n"
        "para verificar que todo funcione"
    )

async def config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Configuracion\n\n"
        "Enviame tu RUT completo CON digito verificador\n\n"
        "Puedes enviarlo en cualquier formato:\n"
        "- 111111111\n"
        "- 11111111-1\n"
        "- 11.111.111-1\n\n"
        "El bot lo formateara automaticamente"
    )
    return ESPERANDO_USUARIO

async def guardar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        user_data[user_id] = {}
    
    rut_raw = update.message.text.strip().replace(".", "").replace("-", "").replace(" ", "").upper()
    
    if len(rut_raw) < 8 or len(rut_raw) > 9:
        await update.message.reply_text(
            "RUT invalido. Debe tener 8-9 caracteres con digito verificador.\n"
            "Ejemplo: 111111111 o 11111111K\n\n"
            "Intenta de nuevo:"
        )
        return ESPERANDO_USUARIO
    
    rut_formateado = formatear_rut(rut_raw)
    user_data[user_id]['rut_raw'] = rut_raw
    user_data[user_id]['rut'] = rut_formateado
    
    await update.message.reply_text(
        f"RUT guardado: {rut_formateado}\n\n"
        "Que cancha quieres? (1-12):"
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
        "A que hora quieres jugar? (6-23)\n"
        "Ejemplo: 19 para las 19:00"
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
        f"Configuracion completada!\n\n"
        f"RUT: {cfg['rut']}\n"
        f"Cancha: {cfg['cancha']}\n"
        f"Hora: {cfg['hora']}:00\n\n"
        f"SIGUIENTE PASO: Envia /test\n"
        f"para verificar que el login funcione"
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
    """TEST COMPLETO: entra a la pagina, valida RUT y captura lo que aparece despues"""
    user_id = update.effective_user.id
    
    if user_id not in user_data or not user_data[user_id].get('rut'):
        await update.message.reply_text("Usa /config primero")
        return
    
    cfg = user_data[user_id]
    
    await update.message.reply_text(
        "TEST iniciado...\n\n"
        "Paso 1: Abriendo navegador\n"
        "Esto tarda 15-30 segundos, espera..."
    )
    
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
            
            ctx_browser = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            page = await ctx_browser.new_page()
            
            # PASO 1: Cargar pagina
            await page.goto("https://reservadehoras.lascondes.cl/#/agenda/28/agendar", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            
            # Screenshot 1: Pagina inicial
            ss1 = await page.screenshot()
            await update.message.reply_photo(photo=ss1, caption="SCREENSHOT 1: Pagina inicial cargada")
            
            # PASO 2: Buscar campo RUT
            rut_input = await page.wait_for_selector('input[name="rut"]', timeout=10000)
            
            if rut_input:
                await update.message.reply_text(
                    f"Paso 2: Campo RUT encontrado!\n"
                    f"Escribiendo: {cfg['rut']}"
                )
                
                # Limpiar y escribir RUT formateado
                await rut_input.click()
                await rut_input.fill("")
                await page.wait_for_timeout(300)
                
                # Escribir con type() para simular teclado (mejor para React)
                await rut_input.type(cfg['rut'], delay=50)
                await page.wait_for_timeout(500)
                
                # Screenshot 2: RUT escrito
                ss2 = await page.screenshot()
                await update.message.reply_photo(photo=ss2, caption=f"SCREENSHOT 2: RUT ingresado: {cfg['rut']}")
            else:
                await update.message.reply_text("ERROR: No se encontro campo RUT")
                await browser.close()
                return
            
            # PASO 3: Click en Validar
            await update.message.reply_text("Paso 3: Buscando boton Validar...")
            
            # Buscar boton Validar con multiples selectores
            boton_validar = None
            selectores_validar = [
                'button:has-text("Validar")',
                'button.sc-fjdhpX',
                'button h1:has-text("Validar")',
            ]
            
            for selector in selectores_validar:
                try:
                    boton_validar = await page.wait_for_selector(selector, timeout=3000)
                    if boton_validar:
                        await update.message.reply_text(f"Boton encontrado con: {selector}")
                        break
                except:
                    continue
            
            if not boton_validar:
                # Intentar buscar cualquier boton
                botones = await page.query_selector_all('button')
                info = f"No encontre boton Validar especifico.\nBotones en pagina: {len(botones)}\n\n"
                for i, btn in enumerate(botones):
                    texto = await btn.inner_text()
                    clase = await btn.get_attribute('class') or ''
                    info += f"Boton {i}: texto='{texto[:30]}' clase='{clase[:40]}'\n"
                await update.message.reply_text(info)
                await browser.close()
                return
            
            # Click en Validar
            await boton_validar.click()
            await update.message.reply_text("Paso 3: Click en Validar! Esperando respuesta...")
            
            # Esperar que la pagina cambie
            await page.wait_for_timeout(3000)
            await page.wait_for_load_state("networkidle", timeout=10000)
            await page.wait_for_timeout(2000)
            
            # Screenshot 3: Despues de validar
            ss3 = await page.screenshot()
            await update.message.reply_photo(photo=ss3, caption="SCREENSHOT 3: Despues de validar RUT")
            
            # PASO 4: Analizar que aparecio despues
            await update.message.reply_text("Paso 4: Analizando pagina post-validacion...")
            
            # Obtener todo el texto visible
            body_text = await page.inner_text('body')
            texto_resumen = body_text[:1500] if len(body_text) > 1500 else body_text
            
            # Buscar todos los elementos interactivos
            inputs_nuevos = await page.query_selector_all('input')
            botones_nuevos = await page.query_selector_all('button')
            selects = await page.query_selector_all('select')
            links = await page.query_selector_all('a')
            
            analisis = f"ANALISIS POST-VALIDACION:\n\n"
            analisis += f"Inputs: {len(inputs_nuevos)}\n"
            for inp in inputs_nuevos:
                name = await inp.get_attribute('name') or ''
                id_a = await inp.get_attribute('id') or ''
                ph = await inp.get_attribute('placeholder') or ''
                tipo = await inp.get_attribute('type') or ''
                clase = await inp.get_attribute('class') or ''
                analisis += f"  name='{name}' id='{id_a}' placeholder='{ph}' type='{tipo}' class='{clase[:30]}'\n"
            
            analisis += f"\nBotones: {len(botones_nuevos)}\n"
            for btn in botones_nuevos[:15]:
                texto = await btn.inner_text()
                clase = await btn.get_attribute('class') or ''
                analisis += f"  texto='{texto[:40]}' class='{clase[:30]}'\n"
            
            analisis += f"\nSelects: {len(selects)}\n"
            for sel in selects:
                name = await sel.get_attribute('name') or ''
                id_a = await sel.get_attribute('id') or ''
                analisis += f"  name='{name}' id='{id_a}'\n"
            
            # Enviar analisis (dividir si es muy largo)
            if len(analisis) > 4000:
                await update.message.reply_text(analisis[:4000])
                await update.message.reply_text(analisis[4000:])
            else:
                await update.message.reply_text(analisis)
            
            # Enviar texto visible
            await update.message.reply_text(f"TEXTO VISIBLE EN PAGINA:\n\n{texto_resumen[:3000]}")
            
            # Screenshot final completo (scroll down)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1000)
            ss4 = await page.screenshot(full_page=True)
            await update.message.reply_photo(photo=ss4, caption="SCREENSHOT 4: Pagina completa (scroll)")
            
            await browser.close()
            
            await update.message.reply_text(
                "TEST COMPLETADO!\n\n"
                "Revisa los screenshots y la info.\n"
                "Enviame los screenshots para que pueda\n"
                "ajustar los selectores del paso siguiente\n"
                "(seleccion de cancha, fecha y hora)"
            )
            
    except Exception as e:
        await update.message.reply_text(
            f"Error en test:\n{str(e)}\n\n"
            f"Posibles causas:\n"
            f"- RUT invalido\n"
            f"- Pagina no disponible\n"
            f"- Timeout de conexion"
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
    elif (ahora.hour == 17 and ahora.minute >= 50) or (ahora.hour == 18 and ahora.minute <= 5):
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
        f"2. Ingresar RUT y validar\n"
        f"3. Buscar cancha disponible\n"
        f"4. Refrescar cada 2 segundos\n"
        f"5. Reservar apenas este disponible\n\n"
        f"Te avisare cuando reserve\n\n"
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
             "Abriendo navegador..."
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
    """Loop principal - mantiene navegador abierto y refresca"""
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
            
            ctx_browser = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            page = await ctx_browser.new_page()
            
            await context.bot.send_message(chat_id=chat_id, text="Navegador abierto, cargando pagina...")
            
            # Cargar pagina
            await page.goto("https://reservadehoras.lascondes.cl/#/agenda/28/agendar", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            
            # Ingresar RUT
            rut_input = await page.wait_for_selector('input[name="rut"]', timeout=10000)
            await rut_input.click()
            await rut_input.type(rut, delay=50)
            await page.wait_for_timeout(500)
            
            # Click Validar
            boton_validar = await page.wait_for_selector('button:has-text("Validar")', timeout=5000)
            await boton_validar.click()
            await page.wait_for_timeout(3000)
            await page.wait_for_load_state("networkidle", timeout=10000)
            
            await context.bot.send_message(chat_id=chat_id, text="RUT validado. Iniciando busqueda de canchas...")
            
            # LOOP PRINCIPAL
            while datetime.datetime.now() < fin_ventana and reserva_en_proceso.get(user_id, False):
                intentos += 1
                
                try:
                    # Recargar pagina
                    await page.reload(timeout=15000)
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    
                    # Re-validar RUT si es necesario
                    rut_field = await page.query_selector('input[name="rut"]')
                    if rut_field:
                        await rut_field.click()
                        await rut_field.fill("")
                        await rut_field.type(rut, delay=30)
                        await page.wait_for_timeout(300)
                        validar_btn = await page.query_selector('button:has-text("Validar")')
                        if validar_btn:
                            await validar_btn.click()
                            await page.wait_for_timeout(2000)
                    
                    # TODO: Buscar disponibilidad
                    # Los selectores exactos se ajustaran despues del /test
                    # que mostrara la estructura de la pagina post-validacion
                    
                    # Buscar elementos que indiquen hora disponible
                    disponible = await page.query_selector(
                        f'[data-hora="{hora}:00"]:not([disabled]), '
                        f'.disponible, '
                        f'td.available, '
                        f'button.hora-libre, '
                        f'.slot-disponible'
                    )
                    
                    if disponible:
                        # Capturar screenshot
                        ss = await page.screenshot()
                        await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=ss,
                            caption=f"HORA ENCONTRADA en intento {intentos}! Reservando..."
                        )
                        
                        await disponible.click()
                        await page.wait_for_timeout(1000)
                        
                        # Buscar boton confirmar
                        confirmar = await page.query_selector(
                            'button:has-text("Reservar"), '
                            'button:has-text("Confirmar"), '
                            'button:has-text("Agendar")'
                        )
                        
                        if confirmar:
                            await confirmar.click()
                            await page.wait_for_timeout(2000)
                        
                        ss2 = await page.screenshot()
                        await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=ss2,
                            caption=f"CANCHA RESERVADA!\n"
                                    f"Cancha: {cancha}\n"
                                    f"Hora: {hora}:00\n"
                                    f"RUT: {rut}\n\n"
                                    f"Revisa tu email de confirmacion"
                        )
                        
                        reserva_en_proceso[user_id] = False
                        await browser.close()
                        return
                    
                    if intentos % 30 == 0:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"Intento {intentos} - Sin disponibilidad aun..."
                        )
                    
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    if intentos % 15 == 0:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"Error temporal (intento {intentos}): {str(e)[:100]}\nContinuando..."
                        )
                    await asyncio.sleep(3)
            
            await browser.close()
    
    except Exception as e:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Error critico: {str(e)[:200]}"
        )
    
    reserva_en_proceso[user_id] = False
    
    if datetime.datetime.now() >= fin_ventana:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Ventana 17:50-18:05 cerrada\n"
                 f"Total intentos: {intentos}\n"
                 f"No se encontro disponibilidad\n\n"
                 f"Reprogramado para manana"
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
    
    print("Bot automatico iniciado")
    print("Selectores ajustados para reservadehoras.lascondes.cl")
    print("Ctrl+C para detener")
    app.run_polling()

if __name__ == "__main__":
    main()
