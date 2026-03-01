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

ESPERANDO_USUARIO, ESPERANDO_HORA = range(2)

user_data = {}
reserva_en_proceso = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def calcular_dv(rut_sin_dv):
    """Calcula digito verificador de un RUT chileno"""
    try:
        rut_num = int(rut_sin_dv)
    except ValueError:
        return None
    suma = 0
    multiplicador = 2
    for digito in reversed(str(rut_num)):
        suma += int(digito) * multiplicador
        multiplicador += 1
        if multiplicador > 7:
            multiplicador = 2
    resto = suma % 11
    dv = 11 - resto
    if dv == 11:
        return '0'
    elif dv == 10:
        return 'K'
    else:
        return str(dv)

def formatear_rut(rut_limpio):
    """Formatea RUT: 12165850K -> 12.165.850-K"""
    if len(rut_limpio) < 2:
        return rut_limpio
    dv = rut_limpio[-1].upper()
    cuerpo = rut_limpio[:-1]
    resultado = ""
    for i, digito in enumerate(reversed(cuerpo)):
        if i > 0 and i % 3 == 0:
            resultado = "." + resultado
        resultado = digito + resultado
    return f"{resultado}-{dv}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot Reserva Automatica Canchas Las Condes\n\n"
        "Comandos:\n"
        "/config - Configurar RUT y hora de juego\n"
        "/test - Probar conexion y validacion\n"
        "/auto - Activar reserva automatica\n"
        "/detener - Detener bot\n"
        "/status - Ver configuracion\n\n"
        "El bot trabaja de Martes a Sabado\n"
        "Se activa a las 17:55 y busca cada 2 seg\n"
        "hasta las 18:10 para tomar hora del dia siguiente"
    )

async def config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Configuracion\n\n"
        "Enviame tu RUT completo CON digito verificador\n\n"
        "Ejemplo: 12165850K"
    )
    return ESPERANDO_USUARIO

async def guardar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        user_data[user_id] = {}
    
    rut_raw = update.message.text.strip().replace(".", "").replace("-", "").replace(" ", "").upper()
    
    if len(rut_raw) < 8 or len(rut_raw) > 9:
        await update.message.reply_text(
            "RUT invalido. Debe tener 8-9 caracteres.\n"
            "Ejemplo: 12165850K\n\nIntenta de nuevo:"
        )
        return ESPERANDO_USUARIO
    
    dv_ingresado = rut_raw[-1]
    cuerpo = rut_raw[:-1]
    dv_correcto = calcular_dv(cuerpo)
    
    if dv_correcto and dv_ingresado != dv_correcto:
        await update.message.reply_text(
            f"ATENCION: Digito verificador incorrecto\n\n"
            f"Ingresaste: {formatear_rut(rut_raw)}\n"
            f"DV correcto: {dv_correcto}\n"
            f"RUT correcto: {formatear_rut(cuerpo + dv_correcto)}\n\n"
            f"Verifica tu carnet y envia de nuevo:"
        )
        return ESPERANDO_USUARIO
    
    rut_formateado = formatear_rut(rut_raw)
    user_data[user_id]['rut_raw'] = rut_raw
    user_data[user_id]['rut'] = rut_formateado
    
    await update.message.reply_text(
        f"RUT validado: {rut_formateado}\n\n"
        "A que hora quieres JUGAR manana?\n\n"
        "Escribe la hora (ejemplo: 7, 8, 9, 10... hasta 21)\n"
        "El bot buscara esa hora especifica:"
    )
    return ESPERANDO_HORA

async def guardar_hora(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    hora = update.message.text.strip()
    
    if not hora.isdigit() or int(hora) < 7 or int(hora) > 21:
        await update.message.reply_text(
            "Hora invalida. Entre 7 y 21.\n"
            "Ejemplo: 9 para las 9:00\n"
            "Intenta de nuevo:"
        )
        return ESPERANDO_HORA
    
    user_data[user_id]['hora'] = int(hora)
    cfg = user_data[user_id]
    
    await update.message.reply_text(
        f"Configuracion completada!\n\n"
        f"RUT: {cfg['rut']}\n"
        f"Hora de juego: {cfg['hora']}:00\n"
        f"Cancha: asignacion automatica\n\n"
        f"El bot buscara {cfg['hora']}:00 para manana\n"
        f"entre las 17:55 y 18:10 de hoy\n\n"
        f"SIGUIENTE: /test para probar\n"
        f"Luego: /auto para activar"
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
    dia_semana = ahora.weekday()
    
    # Verificar si hoy es dia de reserva (Lun-Vie para Mar-Sab)
    dias_reserva = {0: "Martes", 1: "Miercoles", 2: "Jueves", 3: "Viernes", 4: "Sabado"}
    
    if ahora.hour >= 18 and ahora.minute > 10:
        proxima = ahora.replace(hour=17, minute=55, second=0) + datetime.timedelta(days=1)
    else:
        proxima = ahora.replace(hour=17, minute=55, second=0)
        if ahora > proxima:
            proxima += datetime.timedelta(days=1)
    
    dia_juego = proxima + datetime.timedelta(days=1)
    
    estado = "ACTIVO" if reserva_en_proceso.get(user_id, False) else "Inactivo"
    
    await update.message.reply_text(
        f"Configuracion actual\n\n"
        f"RUT: {cfg.get('rut')}\n"
        f"Hora de juego: {cfg.get('hora')}:00\n\n"
        f"Proxima ventana: {proxima.strftime('%d/%m %H:%M')}\n"
        f"Para jugar: {dia_juego.strftime('%A %d/%m')}\n\n"
        f"Estado: {estado}"
    )

async def test_reserva(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """TEST: valida RUT, muestra horas disponibles, NO reserva"""
    user_id = update.effective_user.id
    
    if user_id not in user_data or not user_data[user_id].get('rut'):
        await update.message.reply_text("Usa /config primero")
        return
    
    cfg = user_data[user_id]
    hora_deseada = f"{cfg['hora']}:00"
    
    await update.message.reply_text(
        f"TEST iniciado...\n\n"
        f"RUT: {cfg['rut']}\n"
        f"Hora buscada: {hora_deseada}\n\n"
        f"Abriendo navegador..."
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
            
            # PASO 2: Escribir RUT
            rut_input = await page.wait_for_selector('input[name="rut"]', timeout=10000)
            await rut_input.click()
            await rut_input.type(cfg['rut_raw'], delay=100)
            await page.wait_for_timeout(500)
            
            # PASO 3: Validar
            boton_validar = await page.wait_for_selector('button:has-text("Validar")', timeout=5000)
            await boton_validar.click()
            await page.wait_for_timeout(3000)
            await page.wait_for_load_state("networkidle", timeout=10000)
            await page.wait_for_timeout(2000)
            
            # Verificar error
            body_text = await page.inner_text('body')
            if "rut válido" in body_text.lower():
                await update.message.reply_text("ERROR: RUT no validado")
                await browser.close()
                return
            
            ss1 = await page.screenshot()
            await update.message.reply_photo(photo=ss1, caption="RUT validado - Horas visibles")
            
            # PASO 4: Analizar horas disponibles
            botones = await page.query_selector_all('button')
            
            horas_encontradas = []
            info = "HORAS EN PAGINA:\n\n"
            
            for btn in botones:
                texto = (await btn.inner_text()).strip()
                clase = await btn.get_attribute('class') or ''
                esta_disabled = await btn.is_disabled()
                
                # Detectar botones de hora (formato X:00 o XX:00)
                if ':00' in texto and len(texto) <= 5:
                    estado_hora = "DISPONIBLE" if not esta_disabled else "NO DISPONIBLE"
                    marcador = ">>>" if texto == hora_deseada else "   "
                    info += f"{marcador} {texto} - {estado_hora} (class: {clase[:30]})\n"
                    horas_encontradas.append({
                        'texto': texto,
                        'disponible': not esta_disabled,
                        'clase': clase
                    })
            
            if not horas_encontradas:
                info += "No se encontraron botones de hora\n"
                info += "\nTodos los botones:\n"
                for btn in botones:
                    texto = (await btn.inner_text()).strip()
                    info += f"  - '{texto[:40]}'\n"
            
            await update.message.reply_text(info)
            
            # Buscar hora deseada
            boton_hora = await page.query_selector(f'button:has-text("{hora_deseada}")')
            
            if boton_hora:
                esta_disabled = await boton_hora.is_disabled()
                if not esta_disabled:
                    await update.message.reply_text(
                        f"Hora {hora_deseada} DISPONIBLE!\n\n"
                        f"En modo TEST no se reserva.\n"
                        f"Usa /auto para reservar automaticamente."
                    )
                else:
                    await update.message.reply_text(
                        f"Hora {hora_deseada} existe pero NO disponible.\n"
                        f"Normal si no es horario de reserva (17:55-18:10).\n\n"
                        f"Usa /auto para que el bot espere y reserve."
                    )
            else:
                await update.message.reply_text(
                    f"Hora {hora_deseada} no aparece en pantalla.\n"
                    f"Puede que aparezca cuando se abran las reservas.\n\n"
                    f"Usa /auto para que el bot busque automaticamente."
                )
            
            # Buscar si hay navegacion de fecha (boton "siguiente")
            btn_siguiente = await page.query_selector('button:has-text("siguiente"), a:has-text("siguiente"), *:has-text("siguiente")')
            if btn_siguiente:
                await update.message.reply_text("Boton 'siguiente' encontrado (para cambiar fecha)")
            
            # Screenshot final
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(500)
            ss2 = await page.screenshot(full_page=True)
            await update.message.reply_photo(photo=ss2, caption="Pagina completa")
            
            await browser.close()
            
            await update.message.reply_text(
                "TEST COMPLETADO!\n\n"
                "Si el RUT fue validado correctamente,\n"
                "usa /auto para activar la reserva.\n\n"
                "El bot se activara a las 17:55\n"
                "y buscara cada 2 segundos hasta las 18:10"
            )
            
    except Exception as e:
        await update.message.reply_text(f"Error en test:\n{str(e)}")

async def reservar_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_data or not user_data[user_id].get('rut'):
        await update.message.reply_text("Usa /config primero")
        return
    
    ahora = datetime.datetime.now()
    dia_semana = ahora.weekday()  # 0=Lunes, 6=Domingo
    
    # Calcular inicio: 17:55
    if ahora.hour >= 18 and ahora.minute > 10:
        # Ya paso la ventana de hoy, programar para manana
        inicio = ahora.replace(hour=17, minute=55, second=0, microsecond=0) + datetime.timedelta(days=1)
    elif (ahora.hour == 17 and ahora.minute >= 55) or (ahora.hour == 18 and ahora.minute <= 10):
        # Estamos en la ventana, iniciar YA
        await iniciar_reserva_inmediata(update, context)
        return
    else:
        inicio = ahora.replace(hour=17, minute=55, second=0, microsecond=0)
    
    # Saltar domingos y lunes (no hay reserva para lunes/martes... ajustar segun reglas)
    dia_inicio = inicio.weekday()
    if dia_inicio == 6:  # Domingo -> saltar a Lunes
        inicio += datetime.timedelta(days=1)
    
    segundos_hasta = (inicio - ahora).total_seconds()
    horas = int(segundos_hasta // 3600)
    minutos = int((segundos_hasta % 3600) // 60)
    
    dia_juego = inicio + datetime.timedelta(days=1)
    
    reserva_en_proceso[user_id] = True
    
    await update.message.reply_text(
        f"RESERVA AUTOMATICA ACTIVADA\n\n"
        f"RUT: {user_data[user_id]['rut']}\n"
        f"Hora de juego: {user_data[user_id]['hora']}:00\n\n"
        f"Se activara: {inicio.strftime('%A %d/%m a las %H:%M')}\n"
        f"Para jugar: {dia_juego.strftime('%A %d/%m')}\n"
        f"Faltan: {horas}h {minutos}m\n\n"
        f"Ventana: 17:55 a 18:10\n"
        f"Velocidad: cada 2 seg (30 intentos/min)\n\n"
        f"Te avisare cuando reserve!"
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
    
    await update.message.reply_text("INICIANDO RESERVA AHORA!")
    
    cfg = user_data[user_id]
    
    asyncio.create_task(
        ejecutar_reserva_loop(
            cfg['rut_raw'],
            cfg['rut'],
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
    
    await context.bot.send_message(chat_id=chat_id, text="RESERVA AUTOMATICA INICIADA!\nAbriendo navegador...")
    
    cfg = user_data[user_id]
    
    asyncio.create_task(
        ejecutar_reserva_loop(
            cfg['rut_raw'],
            cfg['rut'],
            cfg['hora'],
            chat_id,
            context,
            user_id
        )
    )

async def ejecutar_reserva_loop(rut_raw, rut_formateado, hora, chat_id, context, user_id):
    """Loop principal: valida RUT y busca hora cada 2 segundos"""
    reserva_en_proceso[user_id] = True
    
    hora_deseada = f"{hora}:00"
    
    ahora = datetime.datetime.now()
    fin_ventana = ahora.replace(hour=18, minute=10, second=0, microsecond=0)
    if fin_ventana < ahora:
        fin_ventana += datetime.timedelta(days=1)
    
    intentos = 0
    reserva_exitosa = False
    
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
            
            # FASE 1: Cargar y validar RUT
            await context.bot.send_message(chat_id=chat_id, text="Fase 1: Cargando pagina...")
            
            await page.goto("https://reservadehoras.lascondes.cl/#/agenda/28/agendar", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            
            rut_input = await page.wait_for_selector('input[name="rut"]', timeout=10000)
            await rut_input.click()
            await rut_input.type(rut_raw, delay=100)
            await page.wait_for_timeout(500)
            
            boton_validar = await page.wait_for_selector('button:has-text("Validar")', timeout=5000)
            await boton_validar.click()
            await page.wait_for_timeout(3000)
            await page.wait_for_load_state("networkidle", timeout=10000)
            
            body_text = await page.inner_text('body')
            if "rut válido" in body_text.lower():
                await context.bot.send_message(chat_id=chat_id, text="ERROR: RUT no validado. Abortando.")
                await browser.close()
                reserva_en_proceso[user_id] = False
                return
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"RUT validado OK!\n"
                     f"Buscando hora {hora_deseada}...\n"
                     f"30 intentos por minuto hasta las 18:10"
            )
            
            # FASE 2: LOOP cada 2 segundos
            while datetime.datetime.now() < fin_ventana and reserva_en_proceso.get(user_id, False):
                intentos += 1
                
                try:
                    # Buscar boton de hora deseada
                    boton_hora = await page.query_selector(f'button:has-text("{hora_deseada}")')
                    
                    if boton_hora:
                        esta_disabled = await boton_hora.is_disabled()
                        
                        if not esta_disabled:
                            # HORA DISPONIBLE! RESERVAR!
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=f"HORA {hora_deseada} ENCONTRADA! (intento {intentos})\nReservando..."
                            )
                            
                            # Click en la hora
                            await boton_hora.click()
                            await page.wait_for_timeout(1000)
                            
                            # Click en "Ok, programar"
                            boton_programar = await page.query_selector('button:has-text("Ok, programar")')
                            
                            if boton_programar:
                                prog_disabled = await boton_programar.is_disabled()
                                
                                if not prog_disabled:
                                    await boton_programar.click()
                                    await page.wait_for_timeout(3000)
                                    
                                    # Capturar resultado
                                    ss_final = await page.screenshot()
                                    body_final = await page.inner_text('body')
                                    
                                    await context.bot.send_photo(
                                        chat_id=chat_id,
                                        photo=ss_final,
                                        caption=f"RESERVA REALIZADA!\n\n"
                                                f"Hora: {hora_deseada}\n"
                                                f"RUT: {rut_formateado}\n"
                                                f"Intento: {intentos}\n\n"
                                                f"Revisa tu email"
                                    )
                                    
                                    await context.bot.send_message(
                                        chat_id=chat_id,
                                        text=f"Pagina dice:\n{body_final[:1000]}"
                                    )
                                    
                                    reserva_exitosa = True
                                    reserva_en_proceso[user_id] = False
                                    await browser.close()
                                    return
                                else:
                                    # Boton programar disabled, deseleccionar y reintentar
                                    await page.reload(timeout=15000)
                                    await page.wait_for_load_state("networkidle", timeout=10000)
                                    await page.wait_for_timeout(500)
                                    
                                    # Re-validar RUT si es necesario
                                    rut_field = await page.query_selector('input[name="rut"]')
                                    if rut_field:
                                        await rut_field.click()
                                        await rut_field.fill("")
                                        await rut_field.type(rut_raw, delay=50)
                                        await page.wait_for_timeout(300)
                                        validar_btn = await page.query_selector('button:has-text("Validar")')
                                        if validar_btn:
                                            await validar_btn.click()
                                            await page.wait_for_timeout(2000)
                            else:
                                # No encontro "Ok, programar", recargar
                                await page.reload(timeout=15000)
                                await page.wait_for_load_state("networkidle", timeout=10000)
                                await page.wait_for_timeout(500)
                                
                                rut_field = await page.query_selector('input[name="rut"]')
                                if rut_field:
                                    await rut_field.click()
                                    await rut_field.fill("")
                                    await rut_field.type(rut_raw, delay=50)
                                    await page.wait_for_timeout(300)
                                    validar_btn = await page.query_selector('button:has-text("Validar")')
                                    if validar_btn:
                                        await validar_btn.click()
                                        await page.wait_for_timeout(2000)
                    else:
                        # No encontro la hora, recargar pagina
                        await page.reload(timeout=15000)
                        await page.wait_for_load_state("networkidle", timeout=10000)
                        await page.wait_for_timeout(500)
                        
                        # Re-validar RUT si aparece el campo
                        rut_field = await page.query_selector('input[name="rut"]')
                        if rut_field:
                            await rut_field.click()
                            await rut_field.fill("")
                            await rut_field.type(rut_raw, delay=50)
                            await page.wait_for_timeout(300)
                            validar_btn = await page.query_selector('button:has-text("Validar")')
                            if validar_btn:
                                await validar_btn.click()
                                await page.wait_for_timeout(2000)
                    
                    # Notificar progreso cada 30 intentos (cada minuto)
                    if intentos % 30 == 0:
                        now = datetime.datetime.now()
                        ss_prog = await page.screenshot()
                        await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=ss_prog,
                            caption=f"Intento {intentos} | {now.strftime('%H:%M:%S')} | Buscando {hora_deseada}..."
                        )
                    
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    if intentos % 15 == 0:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"Error temporal ({intentos}): {str(e)[:100]}\nRecargando..."
                        )
                    
                    # Intentar recargar en caso de error
                    try:
                        await page.goto("https://reservadehoras.lascondes.cl/#/agenda/28/agendar", timeout=30000)
                        await page.wait_for_load_state("networkidle", timeout=15000)
                        
                        rut_input = await page.query_selector('input[name="rut"]')
                        if rut_input:
                            await rut_input.click()
                            await rut_input.type(rut_raw, delay=50)
                            await page.wait_for_timeout(300)
                            validar_btn = await page.query_selector('button:has-text("Validar")')
                            if validar_btn:
                                await validar_btn.click()
                                await page.wait_for_timeout(2000)
                    except:
                        pass
                    
                    await asyncio.sleep(3)
            
            await browser.close()
    
    except Exception as e:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Error critico: {str(e)[:200]}"
        )
    
    reserva_en_proceso[user_id] = False
    
    if not reserva_exitosa and datetime.datetime.now() >= fin_ventana:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Ventana 17:55-18:10 cerrada\n"
                 f"Intentos realizados: {intentos}\n"
                 f"Hora {hora_deseada} no encontrada disponible\n\n"
                 f"Reprogramando para manana..."
        )
        
        # Reprogramar para manana
        ahora = datetime.datetime.now()
        proxima = ahora.replace(hour=17, minute=55, second=0, microsecond=0) + datetime.timedelta(days=1)
        
        # Saltar domingo
        if proxima.weekday() == 6:
            proxima += datetime.timedelta(days=1)
        
        segundos = (proxima - ahora).total_seconds()
        
        context.job_queue.run_once(
            callback_iniciar_reserva,
            when=segundos,
            data={'user_id': user_id, 'chat_id': chat_id},
            name=f'inicio_{user_id}'
        )
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Reprogramado: {proxima.strftime('%A %d/%m a las %H:%M')}"
        )

async def detener(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    reserva_en_proceso[user_id] = False
    
    jobs = context.job_queue.get_jobs_by_name(f'inicio_{user_id}')
    for job in jobs:
        job.schedule_removal()
    
    await update.message.reply_text("Bot detenido. Usa /auto para reactivar.")

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
            ESPERANDO_HORA: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_hora)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)]
    )
    app.add_handler(conv_handler)
    
    print("Bot automatico iniciado")
    print("Ventana: 17:55-18:10 | Cada 2 seg")
    print("Ctrl+C para detener")
    app.run_polling()

if __name__ == "__main__":
    main()
