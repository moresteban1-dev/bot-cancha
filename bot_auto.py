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

(ESPERANDO_RUT1, ESPERANDO_HORA1, ESPERANDO_HORA1_ALT, ESPERANDO_TEL1,
 ESPERANDO_RUT2, ESPERANDO_HORA2, ESPERANDO_HORA2_ALT, ESPERANDO_TEL2) = range(8)

user_data = {}
reserva_en_proceso = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def calcular_dv(rut_sin_dv):
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

def validar_rut_input(rut_text):
    rut_raw = rut_text.strip().replace(".", "").replace("-", "").replace(" ", "").upper()
    if len(rut_raw) < 8 or len(rut_raw) > 9:
        return None, "RUT invalido. 8-9 caracteres con DV."
    dv_ingresado = rut_raw[-1]
    cuerpo = rut_raw[:-1]
    dv_correcto = calcular_dv(cuerpo)
    if dv_correcto and dv_ingresado != dv_correcto:
        return None, (f"DV incorrecto.\nIngresaste: {formatear_rut(rut_raw)}\n"
                      f"Correcto: {formatear_rut(cuerpo + dv_correcto)}")
    return rut_raw, formatear_rut(rut_raw)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot Reserva Canchas Las Condes\n\n"
        "Reserva 2 horas con 2 RUTs\n\n"
        "/config - Configurar\n"
        "/test - Probar (captura formulario telefono)\n"
        "/auto - Activar reserva automatica\n"
        "/detener - Detener\n"
        "/status - Ver configuracion"
    )

async def config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "=== RESERVA 1 ===\n\n"
        "Enviame el RUT (con DV)\n"
        "Ejemplo: 12165850K"
    )
    return ESPERANDO_RUT1

async def guardar_rut1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        user_data[user_id] = {'reserva1': {}, 'reserva2': {}}
    rut_raw, resultado = validar_rut_input(update.message.text)
    if rut_raw is None:
        await update.message.reply_text(f"{resultado}\nIntenta de nuevo:")
        return ESPERANDO_RUT1
    user_data[user_id]['reserva1']['rut_raw'] = rut_raw
    user_data[user_id]['reserva1']['rut'] = resultado
    await update.message.reply_text(f"RUT 1: {resultado}\n\nHora PREFERIDA? (7-21):")
    return ESPERANDO_HORA1

async def guardar_hora1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    hora = update.message.text.strip()
    if not hora.isdigit() or int(hora) < 7 or int(hora) > 21:
        await update.message.reply_text("Hora invalida (7-21):")
        return ESPERANDO_HORA1
    user_data[user_id]['reserva1']['hora'] = int(hora)
    await update.message.reply_text(f"Hora preferida: {hora}:00\n\nHora ALTERNATIVA? (7-21):")
    return ESPERANDO_HORA1_ALT

async def guardar_hora1_alt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    hora = update.message.text.strip()
    if not hora.isdigit() or int(hora) < 7 or int(hora) > 21:
        await update.message.reply_text("Hora invalida (7-21):")
        return ESPERANDO_HORA1_ALT
    user_data[user_id]['reserva1']['hora_alt'] = int(hora)
    await update.message.reply_text("Telefono reserva 1 (9 digitos):")
    return ESPERANDO_TEL1

async def guardar_tel1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    tel = update.message.text.strip().replace("+56", "").replace(" ", "").replace("-", "")
    if len(tel) != 9 or not tel.isdigit():
        await update.message.reply_text("9 digitos. Intenta de nuevo:")
        return ESPERANDO_TEL1
    user_data[user_id]['reserva1']['telefono'] = tel
    r1 = user_data[user_id]['reserva1']
    await update.message.reply_text(
        f"RESERVA 1 OK:\n"
        f"  RUT: {r1['rut']}\n"
        f"  Hora: {r1['hora']}:00 (alt: {r1['hora_alt']}:00)\n"
        f"  Tel: {r1['telefono']}\n\n"
        "=== RESERVA 2 ===\n\nRUT:"
    )
    return ESPERANDO_RUT2

async def guardar_rut2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    rut_raw, resultado = validar_rut_input(update.message.text)
    if rut_raw is None:
        await update.message.reply_text(f"{resultado}\nIntenta de nuevo:")
        return ESPERANDO_RUT2
    if 'reserva2' not in user_data[user_id]:
        user_data[user_id]['reserva2'] = {}
    user_data[user_id]['reserva2']['rut_raw'] = rut_raw
    user_data[user_id]['reserva2']['rut'] = resultado
    await update.message.reply_text(f"RUT 2: {resultado}\n\nHora PREFERIDA? (7-21):")
    return ESPERANDO_HORA2

async def guardar_hora2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    hora = update.message.text.strip()
    if not hora.isdigit() or int(hora) < 7 or int(hora) > 21:
        await update.message.reply_text("Hora invalida (7-21):")
        return ESPERANDO_HORA2
    user_data[user_id]['reserva2']['hora'] = int(hora)
    await update.message.reply_text(f"Hora preferida: {hora}:00\n\nHora ALTERNATIVA? (7-21):")
    return ESPERANDO_HORA2_ALT

async def guardar_hora2_alt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    hora = update.message.text.strip()
    if not hora.isdigit() or int(hora) < 7 or int(hora) > 21:
        await update.message.reply_text("Hora invalida (7-21):")
        return ESPERANDO_HORA2_ALT
    user_data[user_id]['reserva2']['hora_alt'] = int(hora)
    await update.message.reply_text("Telefono reserva 2 (9 digitos):")
    return ESPERANDO_TEL2

async def guardar_tel2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    tel = update.message.text.strip().replace("+56", "").replace(" ", "").replace("-", "")
    if len(tel) != 9 or not tel.isdigit():
        await update.message.reply_text("9 digitos. Intenta de nuevo:")
        return ESPERANDO_TEL2
    user_data[user_id]['reserva2']['telefono'] = tel
    r1 = user_data[user_id]['reserva1']
    r2 = user_data[user_id]['reserva2']
    await update.message.reply_text(
        f"CONFIGURACION COMPLETA!\n\n"
        f"RESERVA 1:\n"
        f"  RUT: {r1['rut']}\n"
        f"  Hora: {r1['hora']}:00 (alt: {r1['hora_alt']}:00)\n"
        f"  Tel: {r1['telefono']}\n\n"
        f"RESERVA 2:\n"
        f"  RUT: {r2['rut']}\n"
        f"  Hora: {r2['hora']}:00 (alt: {r2['hora_alt']}:00)\n"
        f"  Tel: {r2['telefono']}\n\n"
        f"/test para probar\n/auto para activar"
    )
    return ConversationHandler.END

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelado")
    return ConversationHandler.END

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data or 'reserva1' not in user_data[user_id]:
        await update.message.reply_text("Sin config. Usa /config")
        return
    r1 = user_data[user_id].get('reserva1', {})
    r2 = user_data[user_id].get('reserva2', {})
    estado = "ACTIVO" if reserva_en_proceso.get(user_id, False) else "Inactivo"
    await update.message.reply_text(
        f"RESERVA 1: {r1.get('rut')} | {r1.get('hora')}:00 (alt {r1.get('hora_alt')}:00) | {r1.get('telefono')}\n"
        f"RESERVA 2: {r2.get('rut')} | {r2.get('hora')}:00 (alt {r2.get('hora_alt')}:00) | {r2.get('telefono')}\n\n"
        f"Estado: {estado}"
    )

async def test_reserva(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """TEST: valida RUT, click hora disponible, captura formulario telefono, NO confirma"""
    user_id = update.effective_user.id
    if user_id not in user_data or 'reserva1' not in user_data[user_id]:
        await update.message.reply_text("Usa /config primero")
        return
    
    r1 = user_data[user_id]['reserva1']
    
    await update.message.reply_text(
        f"TEST con RUT: {r1['rut']}\n"
        f"Buscara CUALQUIER hora disponible\n"
        f"para llegar al formulario de telefono\n"
        f"NO VA A CONFIRMAR (no gasta reserva)\n\n"
        f"Abriendo navegador..."
    )
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
            )
            ctx_browser = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = await ctx_browser.new_page()
            
            # Paso 1: Cargar
            await page.goto("https://reservadehoras.lascondes.cl/#/agenda/28/agendar", timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=15000)
            
            # Paso 2: RUT
            rut_input = await page.wait_for_selector('input[name="rut"]', timeout=10000)
            await rut_input.click()
            await rut_input.type(r1['rut_raw'], delay=100)
            await page.wait_for_timeout(500)
            
            # Paso 3: Validar
            boton_validar = await page.wait_for_selector('button:has-text("Validar")', timeout=5000)
            await boton_validar.click()
            await page.wait_for_timeout(3000)
            await page.wait_for_load_state("networkidle", timeout=10000)
            await page.wait_for_timeout(2000)
            
            body_text = await page.inner_text('body')
            if "rut válido" in body_text.lower():
                await update.message.reply_text("ERROR: RUT no validado")
                await browser.close()
                return
            
            ss1 = await page.screenshot()
            await update.message.reply_photo(photo=ss1, caption="Paso 1: RUT validado")
            
            # Paso 4: Buscar CUALQUIER hora disponible
            botones = await page.query_selector_all('button')
            hora_disponible = None
            horas_info = "Horas en pagina:\n"
            
            for btn in botones:
                texto = (await btn.inner_text()).strip()
                if ':00' in texto and len(texto) <= 5:
                    disabled = await btn.is_disabled()
                    estado = "DISPONIBLE" if not disabled else "ocupada"
                    horas_info += f"  {texto} - {estado}\n"
                    if not disabled and hora_disponible is None:
                        hora_disponible = btn
                        hora_texto = texto
            
            await update.message.reply_text(horas_info)
            
            if not hora_disponible:
                await update.message.reply_text(
                    "No hay horas disponibles ahora.\n"
                    "No puedo llegar al formulario de telefono.\n\n"
                    "Intenta /test entre 17:55-18:10\n"
                    "cuando se abran las reservas."
                )
                await browser.close()
                return
            
            # Paso 5: Click en hora disponible
            await update.message.reply_text(f"Clickeando hora {hora_texto}...")
            await hora_disponible.click()
            await page.wait_for_timeout(1000)
            
            ss2 = await page.screenshot()
            await update.message.reply_photo(photo=ss2, caption=f"Paso 2: Hora {hora_texto} seleccionada")
            
            # Paso 6: Click "Ok, programar"
            boton_programar = await page.query_selector('button:has-text("Ok, programar")')
            if boton_programar:
                prog_disabled = await boton_programar.is_disabled()
                if not prog_disabled:
                    await update.message.reply_text("Clickeando 'Ok, programar'...")
                    await boton_programar.click()
                    await page.wait_for_timeout(3000)
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    await page.wait_for_timeout(2000)
                    
                    # Paso 7: CAPTURAR FORMULARIO DE TELEFONO
                    ss3 = await page.screenshot()
                    await update.message.reply_photo(photo=ss3, caption="Paso 3: FORMULARIO DE TELEFONO")
                    
                    # Analizar TODOS los inputs
                    inputs = await page.query_selector_all('input')
                    analisis = f"=== FORMULARIO DETECTADO ===\n\n"
                    analisis += f"Inputs: {len(inputs)}\n\n"
                    
                    for idx, inp in enumerate(inputs):
                        name = await inp.get_attribute('name') or ''
                        id_a = await inp.get_attribute('id') or ''
                        ph = await inp.get_attribute('placeholder') or ''
                        tipo = await inp.get_attribute('type') or ''
                        clase = await inp.get_attribute('class') or ''
                        valor = await inp.input_value() or ''
                        required = await inp.get_attribute('required')
                        maxlen = await inp.get_attribute('maxlength') or ''
                        
                        analisis += (
                            f"INPUT {idx}:\n"
                            f"  name='{name}'\n"
                            f"  id='{id_a}'\n"
                            f"  placeholder='{ph}'\n"
                            f"  type='{tipo}'\n"
                            f"  class='{clase[:50]}'\n"
                            f"  value='{valor}'\n"
                            f"  required={required}\n"
                            f"  maxlength='{maxlen}'\n\n"
                        )
                    
                    # Analizar botones
                    botones2 = await page.query_selector_all('button')
                    analisis += f"Botones: {len(botones2)}\n\n"
                    
                    for idx, btn in enumerate(botones2):
                        texto = (await btn.inner_text()).strip()
                        clase = await btn.get_attribute('class') or ''
                        disabled = await btn.is_disabled()
                        tipo = await btn.get_attribute('type') or ''
                        
                        analisis += (
                            f"BOTON {idx}:\n"
                            f"  texto='{texto[:40]}'\n"
                            f"  class='{clase[:50]}'\n"
                            f"  disabled={disabled}\n"
                            f"  type='{tipo}'\n\n"
                        )
                    
                    # Enviar analisis
                    if len(analisis) > 4000:
                        await update.message.reply_text(analisis[:4000])
                        await update.message.reply_text(analisis[4000:])
                    else:
                        await update.message.reply_text(analisis)
                    
                    # Texto visible
                    body2 = await page.inner_text('body')
                    await update.message.reply_text(f"TEXTO VISIBLE:\n\n{body2[:2000]}")
                    
                    # Screenshot completo
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(500)
                    ss4 = await page.screenshot(full_page=True)
                    await update.message.reply_photo(photo=ss4, caption="Paso 4: Pagina completa (scroll)")
                    
                    # Obtener HTML del formulario
                    html = await page.content()
                    # Buscar seccion relevante
                    if 'telefono' in html.lower() or 'celular' in html.lower() or 'fono' in html.lower():
                        await update.message.reply_text("ENCONTRE referencia a telefono en el HTML!")
                    
                    await update.message.reply_text(
                        "TEST COMPLETO!\n\n"
                        "IMPORTANTE: NO se confirmo la reserva.\n"
                        "Si aparecio el formulario, enviame\n"
                        "los screenshots y la info de arriba\n"
                        "para ajustar los selectores.\n\n"
                        "Si se reservo por accidente,\n"
                        "puedes cancelar en la pagina web."
                    )
                else:
                    await update.message.reply_text("'Ok, programar' esta deshabilitado")
            else:
                await update.message.reply_text("No encontre 'Ok, programar'")
                btns = await page.query_selector_all('button')
                info = "Botones:\n"
                for b in btns:
                    t = (await b.inner_text()).strip()
                    info += f"  '{t[:30]}'\n"
                await update.message.reply_text(info)
            
            await browser.close()
            
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)[:300]}")

async def reservar_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data or 'reserva1' not in user_data[user_id]:
        await update.message.reply_text("Usa /config primero")
        return
    r1 = user_data[user_id]['reserva1']
    r2 = user_data[user_id]['reserva2']
    if not r1.get('telefono') or not r2.get('telefono'):
        await update.message.reply_text("Falta config. Usa /config")
        return
    
    ahora = datetime.datetime.now()
    if ahora.hour >= 18 and ahora.minute > 10:
        inicio = ahora.replace(hour=17, minute=55, second=0, microsecond=0) + datetime.timedelta(days=1)
    elif (ahora.hour == 17 and ahora.minute >= 55) or (ahora.hour == 18 and ahora.minute <= 10):
        await iniciar_reserva_inmediata(update, context)
        return
    else:
        inicio = ahora.replace(hour=17, minute=55, second=0, microsecond=0)
    
    if inicio.weekday() == 6:
        inicio += datetime.timedelta(days=1)
    
    segundos_hasta = (inicio - ahora).total_seconds()
    horas = int(segundos_hasta // 3600)
    minutos = int((segundos_hasta % 3600) // 60)
    
    reserva_en_proceso[user_id] = True
    
    await update.message.reply_text(
        f"RESERVA DOBLE ACTIVADA!\n\n"
        f"R1: {r1['rut']} -> {r1['hora']}:00 (alt {r1['hora_alt']}:00)\n"
        f"R2: {r2['rut']} -> {r2['hora']}:00 (alt {r2['hora_alt']}:00)\n\n"
        f"Inicia: {inicio.strftime('%A %d/%m %H:%M')}\n"
        f"Faltan: {horas}h {minutos}m"
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
    await update.message.reply_text("INICIANDO RESERVA DOBLE AHORA!")
    asyncio.create_task(ejecutar_reserva_doble(user_id, chat_id, context))

async def callback_iniciar_reserva(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    user_id = data['user_id']
    chat_id = data['chat_id']
    await context.bot.send_message(chat_id=chat_id, text="RESERVA DOBLE INICIADA!")
    asyncio.create_task(ejecutar_reserva_doble(user_id, chat_id, context))

async def ejecutar_reserva_doble(user_id, chat_id, context):
    reserva_en_proceso[user_id] = True
    r1 = user_data[user_id]['reserva1']
    r2 = user_data[user_id]['reserva2']
    
    await context.bot.send_message(chat_id=chat_id, text="Lanzando 2 reservas en paralelo...")
    
    resultado1, resultado2 = await asyncio.gather(
        ejecutar_una_reserva(r1, 1, chat_id, context, user_id),
        ejecutar_una_reserva(r2, 2, chat_id, context, user_id),
        return_exceptions=True
    )
    
    reserva_en_proceso[user_id] = False
    
    resumen = "=== RESUMEN ===\n\n"
    resumen += f"R1: {resultado1 if not isinstance(resultado1, Exception) else f'ERROR: {resultado1}'}\n"
    resumen += f"R2: {resultado2 if not isinstance(resultado2, Exception) else f'ERROR: {resultado2}'}\n"
    await context.bot.send_message(chat_id=chat_id, text=resumen)
    
    # Reprogramar
    ahora = datetime.datetime.now()
    proxima = ahora.replace(hour=17, minute=55, second=0, microsecond=0) + datetime.timedelta(days=1)
    if proxima.weekday() == 6:
        proxima += datetime.timedelta(days=1)
    segundos = (proxima - ahora).total_seconds()
    
    context.job_queue.run_once(
        callback_iniciar_reserva,
        when=segundos,
        data={'user_id': user_id, 'chat_id': chat_id},
        name=f'inicio_{user_id}'
    )
    await context.bot.send_message(chat_id=chat_id, text=f"Proximo: {proxima.strftime('%A %d/%m %H:%M')}")

async def validar_rut_en_pagina(page, rut_raw):
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
            return True
    return False

async def ejecutar_una_reserva(reserva, numero, chat_id, context, user_id):
    rut_raw = reserva['rut_raw']
    rut_fmt = reserva['rut']
    hora_pref = reserva['hora']
    hora_alt = reserva['hora_alt']
    telefono = reserva['telefono']
    hora_pref_str = f"{hora_pref}:00"
    hora_alt_str = f"{hora_alt}:00"
    
    ahora = datetime.datetime.now()
    fin_ventana = ahora.replace(hour=18, minute=10, second=0, microsecond=0)
    if fin_ventana < ahora:
        fin_ventana += datetime.timedelta(days=1)
    
    intentos = 0
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
            )
            ctx_browser = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = await ctx_browser.new_page()
            
            await context.bot.send_message(chat_id=chat_id, text=f"[R{numero}] Cargando para {rut_fmt}...")
            
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
            
            body = await page.inner_text('body')
            if "rut válido" in body.lower():
                await browser.close()
                return f"ERROR: RUT {rut_fmt} no validado"
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"[R{numero}] RUT OK. Buscando {hora_pref_str} (alt: {hora_alt_str})..."
            )
            
            while datetime.datetime.now() < fin_ventana and reserva_en_proceso.get(user_id, False):
                intentos += 1
                
                try:
                    # Buscar hora preferida
                    hora_encontrada = None
                    hora_encontrada_str = None
                    
                    btn_pref = await page.query_selector(f'button:has-text("{hora_pref_str}")')
                    if btn_pref and not await btn_pref.is_disabled():
                        hora_encontrada = btn_pref
                        hora_encontrada_str = hora_pref_str
                    
                    # Si no hay preferida, buscar alternativa
                    if not hora_encontrada:
                        btn_alt = await page.query_selector(f'button:has-text("{hora_alt_str}")')
                        if btn_alt and not await btn_alt.is_disabled():
                            hora_encontrada = btn_alt
                            hora_encontrada_str = hora_alt_str
                    
                    if hora_encontrada:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"[R{numero}] {hora_encontrada_str} ENCONTRADA! (intento {intentos})"
                        )
                        
                        # Click hora
                        await hora_encontrada.click()
                        await page.wait_for_timeout(1000)
                        
                        # Click "Ok, programar"
                        btn_prog = await page.query_selector('button:has-text("Ok, programar")')
                        if btn_prog and not await btn_prog.is_disabled():
                            await btn_prog.click()
                            await page.wait_for_timeout(3000)
                            await page.wait_for_load_state("networkidle", timeout=10000)
                            await page.wait_for_timeout(2000)
                            
                            ss = await page.screenshot()
                            await context.bot.send_photo(
                                chat_id=chat_id,
                                photo=ss,
                                caption=f"[R{numero}] Formulario telefono"
                            )
                            
                            # Buscar campo telefono
                            campo_tel = None
                            for sel in [
                                'input[name="telefono"]',
                                'input[name="phone"]',
                                'input[name="celular"]',
                                'input[name="fono"]',
                                'input[type="tel"]',
                                'input[placeholder*="elefono"]',
                                'input[placeholder*="celular"]',
                                'input[placeholder*="fono"]',
                            ]:
                                campo_tel = await page.query_selector(sel)
                                if campo_tel:
                                    break
                            
                            if not campo_tel:
                                inputs = await page.query_selector_all('input')
                                for inp in inputs:
                                    name = await inp.get_attribute('name') or ''
                                    if name != 'rut':
                                        campo_tel = inp
                                        break
                            
                            if campo_tel:
                                await campo_tel.click()
                                await campo_tel.fill("")
                                await campo_tel.type(telefono, delay=50)
                                await page.wait_for_timeout(500)
                                
                                # Buscar confirmar
                                btn_conf = None
                                for sel in [
                                    'button:has-text("Confirmar")',
                                    'button:has-text("Reservar")',
                                    'button:has-text("Agendar")',
                                    'button:has-text("Enviar")',
                                    'button:has-text("Aceptar")',
                                    'button[type="submit"]',
                                ]:
                                    btn_conf = await page.query_selector(sel)
                                    if btn_conf and not await btn_conf.is_disabled():
                                        break
                                    btn_conf = None
                                
                                if btn_conf:
                                    texto_btn = await btn_conf.inner_text()
                                    await context.bot.send_message(
                                        chat_id=chat_id,
                                        text=f"[R{numero}] Tel ingresado. Confirmando con '{texto_btn.strip()}'..."
                                    )
                                    
                                    await btn_conf.click()
                                    await page.wait_for_timeout(3000)
                                    await page.wait_for_load_state("networkidle", timeout=10000)
                                    await page.wait_for_timeout(2000)
                                    
                                    ss_final = await page.screenshot()
                                    body_final = await page.inner_text('body')
                                    
                                    await context.bot.send_photo(
                                        chat_id=chat_id,
                                        photo=ss_final,
                                        caption=f"[R{numero}] RESERVA COMPLETADA!\n\n"
                                                f"Hora: {hora_encontrada_str}\n"
                                                f"RUT: {rut_fmt}\n"
                                                f"Intento: {intentos}"
                                    )
                                    
                                    await browser.close()
                                    return f"EXITOSA - {hora_encontrada_str} ({rut_fmt}) intento {intentos}"
                                else:
                                    btns = await page.query_selector_all('button')
                                    info = f"[R{numero}] No encontre confirmar. Botones:\n"
                                    for b in btns:
                                        t = (await b.inner_text()).strip()
                                        info += f"  '{t[:30]}'\n"
                                    await context.bot.send_message(chat_id=chat_id, text=info)
                            else:
                                inps = await page.query_selector_all('input')
                                info = f"[R{numero}] No encontre campo tel. Inputs:\n"
                                for inp in inps:
                                    n = await inp.get_attribute('name') or ''
                                    ph = await inp.get_attribute('placeholder') or ''
                                    info += f"  name='{n}' ph='{ph}'\n"
                                await context.bot.send_message(chat_id=chat_id, text=info)
                    
                    # Recargar
                    await page.reload(timeout=15000)
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    await page.wait_for_timeout(500)
                    await validar_rut_en_pagina(page, rut_raw)
                    
                    if intentos % 30 == 0:
                        now = datetime.datetime.now()
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"[R{numero}] Intento {intentos} | {now.strftime('%H:%M:%S')}"
                        )
                    
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    if intentos % 15 == 0:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"[R{numero}] Error ({intentos}): {str(e)[:80]}"
                        )
                    try:
                        await page.goto("https://reservadehoras.lascondes.cl/#/agenda/28/agendar", timeout=30000)
                        await page.wait_for_load_state("networkidle", timeout=15000)
                        await validar_rut_en_pagina(page, rut_raw)
                    except:
                        pass
                    await asyncio.sleep(3)
            
            await browser.close()
    except Exception as e:
        return f"ERROR: {str(e)[:100]}"
    
    return f"SIN RESULTADO - {intentos} intentos"

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
            ESPERANDO_RUT1: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_rut1)],
            ESPERANDO_HORA1: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_hora1)],
            ESPERANDO_HORA1_ALT: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_hora1_alt)],
            ESPERANDO_TEL1: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_tel1)],
            ESPERANDO_RUT2: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_rut2)],
            ESPERANDO_HORA2: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_hora2)],
            ESPERANDO_HORA2_ALT: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_hora2_alt)],
            ESPERANDO_TEL2: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_tel2)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)]
    )
    app.add_handler(conv_handler)
    
    print("Bot doble reserva iniciado")
    print("Ventana: 17:55-18:10 | Cada 2 seg")
    app.run_polling()

if __name__ == "__main__":
    main()
