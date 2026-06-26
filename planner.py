from log_config import logger

# Iniciación llm
import os
from langchain_openai import AzureChatOpenAI #pip install langchain-openai
from dotenv import load_dotenv
from typing import Any, Dict
from langgraph.checkpoint.memory import MemorySaver

# Generales
from pydantic import BaseModel, Field, config
from typing import TypedDict, List, Dict, Literal
from langgraph.graph import StateGraph, END, START

# Generales sobre prompts
from langchain_core.messages import SystemMessage, HumanMessage
from prompts import SYSTEM_ARCHITECT_CORE, SYSTEM_PLANNER_CORE

# Nodo 1
from prompts import PROMPT_ACLARADOR

# Nodo 2: Confirmación usuario casos de uso
from prompts import PROMPT_CONFIRMACION_CASOS

# Nodo 3: Consulta de catálogo (Temáticas)
import json
import httpx
#from ANTIGUO_mcpServerPadre import MCP_REGISTRY

# Nodo 6: Confirmar el plan con el usuario
from prompts import PROMPT_CONFIRMACION_PLAN

# Para la ejecución del workflow
import asyncio
import uuid


### 2. LLM Initialization
# Llm Openai
load_dotenv()
api_key = os.getenv("AZURE_OPENAI_API_KEY")

llm = AzureChatOpenAI(
    azure_deployment="gpt-4o",
    api_version="2025-01-01-preview",
    temperature=0,
    max_tokens=10000,
    timeout=100,
    max_retries=0,
    azure_endpoint="https://eduar-m698sapo-eastus2.openai.azure.com/",
    tags=["agente_planner"]
)



### 3. Estado del Grafo y Nodos
class State_N(TypedDict): #Esto es un TypedDict. Y cada elemento es una clave/llave/valor.
    prompt_usuario: str
    
    # --- ETAPA 1: CASOS DE USO ---
    casos_uso: List[Dict]
    casos_uso_confirmacion: Literal["Si","No"]
    feedback_casos: str  # <--- NUEVO: Para guardar "Solo quiero un caso..."

# --- ETAPA 2: REGISTRO Y SELECCIÓN (CONSOLIDADO) ---
    mcp_registry: Dict[str, Any] # <--- Nueva clave: Catálogo completo
    seleccion_tools_tematicas: List[Dict] # Resultado de la selección combinad
    herramientas_faltantes: List[Dict]

    # --- ETAPA 2.bis: CONTROL DE NDLoop ---
    # CORRECCIÓN 1: Flag para detectar si NDLoop ya fue invocado y, por tanto,
    # evitar reentrar en pausa_ndloop si las tools no fueron generadas (bucle infinito).
    ndloop_intentado: bool
    ndloop_fallo: bool

    # --- ETAPA 3: CHECKEO Y PRESENTACIÓN RESULTADOS ---
    autocheck_ok: bool  #Nodo 5: Auto-check de completitud
    autocheck_errores: List[str]
    autocheck_lectura: str
    
    # Nodo 6: Solicitud información faltante del usuario
    urls_usuario: List[str]

    lista_final: List[Dict] #Nodo 7: Generación del Plan Consolidado. Esto es el "resultado final sin repeticiones":
    lista_final_lectura: str
    
    feedback_final: str  #Nodo 8: Confirmar el plan con el usuario
    confirmacion_plan: Literal["Si", "No"]

    # --- Guía para el mcpClient ---
    guia_ejecucion: str
    
    # --- CONTROL ---
    input_externo: str | None # Input que viene del orquestador

    # --- HISTORIAL DE CONVERSACIÓN CON EL USUARIO ---
    historial_conversacion: List[Dict]



# Nodo 1: Deducción Casos de Uso
class CasoDeUso(BaseModel):
    title: str = Field(description="Título corto del caso de uso")
    description: str = Field(description="Descripción de qué resuelve")

class DeteccionCasosDeUso(BaseModel):
    casos: List[CasoDeUso] = Field(description="Casos de uso deducidos automáticamente")
 
def detectar_casos_de_uso(state: State_N):
#    feedback_anterior = state.get("feedback_casos", "")
    full_prompt = state["prompt_usuario"]
    historial_feedback = state.get("feedback_casos", "")

    # Creación del contexto
    if historial_feedback:
        contexto_input = f"--- PETICIÓN ORIGINAL:\n{full_prompt}\n\n--- HISTORIAL DE ITERACIONES CON EL USUARIO ---{historial_feedback}"
    else:
        contexto_input = f"--- PETICIÓN ORIGINAL:\n{full_prompt}"

    # Refinamiento del contexto si hay feedback
#    if feedback_anterior:
#        # Enmarcamos el feedback como una instrucción de refinamiento prioritaria
#        contexto_input = f"PETICIÓN ORIGINAL: {full_prompt}\n\nNUEVAS INSTRUCCIONES/FEEDBACK: {feedback_anterior}"
#        contexto_input = f"PETICIÓN ORIGINAL:\n{full_prompt}\n\n--- HISTORIAL DE ITERACIONES ---{historial_feedback}"
#    else:
#        contexto_input = full_prompt

    # Preparamos el contenido del mensaje Humano
    human_content = PROMPT_ACLARADOR.substitute(prompt_usuario=contexto_input)

    # Construcción de la jerarquía de mensajes
    messages = [
        SystemMessage(content=SYSTEM_PLANNER_CORE),
        HumanMessage(content=human_content)
    ]

    logger.info(f"[Planner] Detectando casos de uso para: {full_prompt[:50]}...")
    res: DeteccionCasosDeUso = llm.with_structured_output(DeteccionCasosDeUso).invoke(
        messages, 
        config={"tags": ["Planner_01_DetectarCasos"]} 
    )
    logger.info(f"[Planner] {len(res.casos)} casos detectados.")

    return {
        "casos_uso": [c.model_dump() for c in res.casos], 
    }



# Nodo 2: Confirmación usuario casos de uso
class ConfirmacionCasos(BaseModel):
    casos: List[CasoDeUso] = Field(
        description="Lista de casos de uso actualizada según lo que diga el usuario."
    )
    confirmacion_casos: Literal["Si","No"] = Field(
        description="Si el usuario está conforme con los casos de uso: 'Si'. Si añade, modifica o cuestiona algo: 'No'."
    )

#from prompts import PROMPT_CONFIRMACION_CASOS
def confirmar_casos_de_uso(state: State_N):
    respuesta_usuario = state.get("input_externo", "Si") 
    
    mensaje_al_usuario = (
        f"""\n He detectado estos casos de uso:\n\n{state["casos_uso"]}\n\n
        ¿Son correctos o desea añadir alguno?\n"""
    )

    # Preparamos el contenido para el modelo
    human_content = PROMPT_CONFIRMACION_CASOS.substitute(
        prompt_usuario=state["prompt_usuario"],
        casos_uso=json.dumps(state["casos_uso"], indent=2),
        pregunta_usuario=mensaje_al_usuario,
        respuesta_usuario=respuesta_usuario
    )

    messages = [
        SystemMessage(content=SYSTEM_PLANNER_CORE),
        HumanMessage(content=human_content)
    ]

    res: ConfirmacionCasos = llm.with_structured_output(ConfirmacionCasos).invoke(
        messages, 
        config={"tags": ["Planner_02_ConfirmarCasos"]}
    )
    logger.info(f"[Planner] Usuario confirma casos: {res.confirmacion_casos}")
    
#    nuevo_feedback = ""
    decision = res.confirmacion_casos
    historial_actual = state.get("feedback_casos", "")

# Guardamos el feedback acumulado para que el Nodo 1 tenga el contexto de todas las iteraciones anteriores
    if decision == "No":
        casos_str = json.dumps(state["casos_uso"], indent=2)
        nueva_iteracion = f"\n[PROPUESTA ANTERIOR]\n{casos_str}\n[FEEDBACK DEL USUARIO]\n{respuesta_usuario}\n"
        nuevo_feedback = historial_actual + nueva_iteracion
    else:
        nuevo_feedback = historial_actual

    # Registrar en historial
    historial = state.get("historial_conversacion", [])
    casos_str = json.dumps(state["casos_uso"], indent=2)
    historial.append({"rol": "planner", "fase": "confirmar_casos_de_uso", "contenido": f"Casos propuestos:\n{casos_str}"})
    historial.append({"rol": "usuario", "fase": "confirmar_casos_de_uso", "contenido": respuesta_usuario})

    return {
        "casos_uso": [c.model_dump() for c in res.casos],
        "casos_uso_confirmacion": decision,
        "feedback_casos": nuevo_feedback,
        "input_externo": None,
        "historial_conversacion": historial
    }


# Nodo 3: Consulta de temáticas y tools (todo junto)
IP_MCP_SERVER_PADRE = "http://127.0.0.1:8000"

async def consulta_mcp_registry(state: State_N):
    """
    Consulta el registro dinámico al MCPServerPadre vía HTTP.
    """
    url = f"{IP_MCP_SERVER_PADRE}/registry"
    
    try:
        logger.info(f"[Planner] Conectando con MCPServerPadre en {url}...")
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10) # Esta es solictud al MCPServerPadre
        response.raise_for_status() # Lanza excepción si hay error HTTP
        
        response_json = response.json()
        logger.info(f"[Planner] MCPRegistry obtenido: {len(response_json)} servidores.")
        logger.debug(f"MCPRegistry keys: {list(response_json.keys())}")
        
        #Estas tres líneas son para Mock en Local: Si inyectamos un servidor falso en el frontend, lo conservamos aquí
        # para que la llamada HTTP no lo borre.
        registro_actual = state.get("mcp_registry", {})
        if "mcp_creado_dinamico" in registro_actual:
            response_json["mcp_creado_dinamico"] = registro_actual["mcp_creado_dinamico"]
        # ------------------------------------

        return {
            "mcp_registry": response_json
        }
    except Exception as e:
        logger.error(f"❌ Error al consultar el MCP_REGISTRY: {e}")
        return {"mcp_registry": {}}


# Nodo 4: Selección tools y temáticas (todo junto)
class HerramientaFaltante(BaseModel):
    nombre_sugerido: str = Field(
    description=(
        "Nombre técnico propuesto para la tool. "
        "Debe estar adaptada para la necesidad del usuario pero suficientemente genérica para poder usarse en casos similares (ej: 'nlp_analysis', 'web_scraping')."
    ))
    descripcion_funcionalidad: str = Field(description="Explicación detallada de qué debe hacer exactamente esta tool, qué recibe y qué devuelve.")
    categoria_sugerida: str = Field(default="", description="Topic/módulo existente donde debería ir esta tool (ej: 'nlp_analysis', 'web_scraping'). Dejar vacío si no encaja en ninguna categoría existente.")

class SeleccionRecurso(BaseModel):
    server_id: str = Field(description="ID del servidor MCP")
    tools: List[str] = Field(description="Lista de nombres de herramientas seleccionadas de este servidor")

class CasoConRecursos(BaseModel):
    case_title: str
    recursos: List[SeleccionRecurso]

class PlanTecnicoCompleto(BaseModel):
    casos: List[CasoConRecursos]
    herramientas_faltantes: List[HerramientaFaltante] = Field(
        default_factory=list, 
        description="Si el catálogo actual NO tiene herramientas para resolver completamente un caso de uso, define aquí qué herramientas habría que crear."
    )

def seleccion_mcp_registry(state: State_N):
    from prompts import PROMPT_SELECCION_RECURSOS
    
    # Añadido por limitación del llm (Error code: 429). Reducción tamaño del prompt
    registro_original = state.get("mcp_registry", {})
    registro_reducido = {}

    for server_id, server_info in registro_original.items():
        # Conservamos la estructura pero filtramos las herramientas
        registro_reducido[server_id] = {
            "tools": [
                {
                    "name": t["name"],
                    "description": t["description"]
                    # NO incluimos t["source_code"]
                } for t in server_info.get("tools", [])
            ]
        }
    # --------------------------------------- ❗❗--------------------------------------- 

    # Preparación de datos
    casos_uso_lista = [
        c.model_dump() if hasattr(c, "model_dump") else c 
        for c in state["casos_uso"]
    ]

    prompt_usuario_final = PROMPT_SELECCION_RECURSOS.substitute(
        casos_uso=json.dumps(casos_uso_lista, indent=2),
        mcp_registry=json.dumps(registro_reducido, indent=2),
        feedback_final=state.get("feedback_final", "Ninguno")
    )

    # Inyección de SystemMessage y HumanMessage
    messages = [
        SystemMessage(content=SYSTEM_ARCHITECT_CORE),
        HumanMessage(content=prompt_usuario_final)
    ]

    logger.info("[Planner] Seleccionando herramientas del catalogo...")
    res = llm.with_structured_output(PlanTecnicoCompleto).invoke(
        messages, 
        config={"tags": ["Planner_04_SeleccionRecursos"]}
    )
    logger.info(f"[Planner] Seleccion completada para {len(res.casos)} casos.")

    nuevas_faltantes = [h.model_dump() for h in res.herramientas_faltantes]

    salida = {
        "seleccion_tools_tematicas": res.casos,
        "herramientas_faltantes": nuevas_faltantes
    }

    if not nuevas_faltantes and state.get("ndloop_intentado"):
        logger.info("[Planner] ✅ NDLoop resolvió las herramientas faltantes. Reseteando flag.")
        salida["ndloop_intentado"] = False

    return salida



# Nodo 5: Auto-check de completitud (sin hardcodear nada)
def auto_check_completitud(state: State_N):
    # Ahora leemos de la nueva clave consolidada
    plan = state.get("seleccion_tools_tematicas", [])
    registro = state.get("mcp_registry", {})
    
    errores = []
    completitud_ok = True
    necesita_urls = False

    for caso in plan:
        for recurso in caso.recursos:
            # 1. Verificar si alguna de las herramientas seleccionadas es 'scrape_url' para marcar que necesitamos URLs del usuario
            if "scrape_url" in recurso.tools:
                necesita_urls = True

            # 2. Validar que el servidor existe en nuestro registro importado
            if recurso.server_id not in registro:
                errores.append(f"El servidor '{recurso.server_id}' no existe en el catálogo.")
                completitud_ok = False
                continue
            
            # 3. Validar que las herramientas elegidas existan en ese servidor
            tools_en_registro = {t["name"] for t in registro[recurso.server_id]["tools"]}
            for tool_name in recurso.tools:
                if tool_name not in tools_en_registro:
                    errores.append(f"La tool '{tool_name}' no existe en el servidor '{recurso.server_id}'.")
                    completitud_ok = False

    lectura = "✅ Auto-check: OK" if completitud_ok else "❌ Errores: " + ", ".join(errores)
    
    salida = {
        "autocheck_ok": completitud_ok,
        "autocheck_errores": errores,
        "autocheck_lectura": lectura,
    }

    # Limpieza: si el plan cambió y ya no requiere URLs, las borramos del estado
    if not necesita_urls and state.get("urls_usuario"):
        salida["urls_usuario"] = []

    return salida


# Nodo 6: Solicitud de información adicional al usuario 
def solicitar_informacion_herramientas(state: State_N):
    """
    Verifica si se requiere información adicional para las tools seleccionadas. A día 23/2/2026 solo 'scrape_url' requiere información adicional (URLs). 
    """
    plan = state.get("seleccion_tools_tematicas", [])

    # Comprobar si 'scrape_url' está en alguna de las selecciones
    necesita_urls = any("scrape_url" in recurso.tools for caso in plan for recurso in caso.recursos)
    
    if not necesita_urls:
        return {"urls_usuario": []}

    respuesta_externa = state.get("input_externo")

    # Registrar en historial
    historial = state.get("historial_conversacion", [])
    historial.append({"rol": "planner", "fase": "solicitar_urls", "contenido": "Se solicitaron URLs al usuario."})
    historial.append({"rol": "usuario", "fase": "solicitar_urls", "contenido": respuesta_externa or "(vacío → URLs por defecto)"})



    if respuesta_externa == "":
        return {"urls_usuario": [
        "https://newsroom.accenture.es/es/news/2025/el-63-de-las-empresas-espanolas-planea-aumentar-las-inversiones-en-tecnologia-de-ia-soberana-en-los-proximos-anos, "
        "https://newsroom.accenture.es/es/news/2025/accenture-y-anthropic-se-unen-para-impulsar-la-innovacion-y-el-valor-empresarial-en-diferentes-industrias1, "
        "https://newsroom.accenture.es/es/news/2026/scottishpower-renewables-selecciona-a-boslan-compania-de-accenture-para-apoyar-la-fabricacion-del-parque-eolico-east-anglia-two, "
        "https://newsroom.accenture.es/es/news/2025/accenture-snowflake-impulsan-la-reinvencion-empresarial-mediante-ia-y-datos2"
        ], 
        "historial_conversacion": historial
        }

    if respuesta_externa and respuesta_externa.lower() != "":
        urls = [url.strip() for url in respuesta_externa.split(",") if url.strip()]
        return {
            "urls_usuario": urls,
            "input_externo": None,
            "historial_conversacion": historial
        }
    return {"historial_conversacion": historial}

# Esta función se usa para no tener que estar pidiendo URLs si el usuario ya las ha dado o si el plan no las requiere
def enrutar_peticion_urls(state: State_N) -> str:
    """Evalúa si es necesario interrumpir el flujo para pedir URLs."""
    plan = state.get("seleccion_tools_tematicas", [])
    
    necesita_urls = any("scrape_url" in r.tools for c in plan for r in c.recursos)
    tiene_urls = bool(state.get("urls_usuario"))

    # Si la herramienta lo exige y aún no tenemos datos, vamos al nodo de solicitud
    if necesita_urls and not tiene_urls:
        return "solicitar_informacion_herramientas"
    
    # En cualquier otro caso, saltamos directamente a la consolidación
    return "generar_plan_consolidado"


# Nodo 7: Generación del Plan Consolidado y Guía de Ejecución
class MCPPlanItem(BaseModel):
    topic: str
    server_id: str
    tools: List[str]

class PlanConsolidadoEstrategico(BaseModel):
    plan: List[MCPPlanItem] = Field(description="Lista de servidores y sus tools sin duplicados.")
    guia_pasos: str = Field(description="Explicación secuencial de cómo usar estas herramientas (menciona específicamente qué herramienta ejecutar en cada momento) para cumplir el objetivo.")

def generar_plan_consolidado(state: State_N):
    from prompts import PROMPT_CONSOLIDACION_ESTRATEGICA
    
# --- REDUCCIÓN DE TAMAÑO POR EL LLM (Igual que en el Nodo 4) ---
    registro_original = state.get("mcp_registry", {})
    registro_reducido = {
        server_id: {
            "tools": [{"name": t["name"], "description": t["description"]} for t in info.get("tools", [])]
        } for server_id, info in registro_original.items()
    }
    # --------------------------------------------------

    # Aseguramos que casos_uso sean diccionarios antes del dumps
    casos_limpios = [c.model_dump() if hasattr(c, "model_dump") else c for c in state["casos_uso"]]
    plan_datos = [c.model_dump() if hasattr(c, "model_dump") else c for c in state["seleccion_tools_tematicas"]]

    human_content = PROMPT_CONSOLIDACION_ESTRATEGICA.substitute(
        casos_uso=json.dumps(casos_limpios, indent=2),
        seleccion_tools_tematicas=json.dumps(plan_datos, indent=2), 
        mcp_registry=json.dumps(registro_reducido, indent=2),
        feedback_final=state.get("feedback_final", ""),
        urls_usuario=json.dumps(state.get("urls_usuario", []), indent=2)
    )

    messages = [
        SystemMessage(content=SYSTEM_ARCHITECT_CORE),
        HumanMessage(content=human_content)
    ]

    logger.info("[Planner] Consolidando plan estrategico...")
    res: PlanConsolidadoEstrategico = llm.with_structured_output(PlanConsolidadoEstrategico).invoke(
        messages, 
        config={"tags": ["Planner_07_GenerarPlan"]}
    )
    logger.info("[Planner] Plan consolidado generado.")

    # Generación de la versión "lectura" para el log y el usuario
    lineas = ["Plan de Despliegue Consolidado:"]
    for item in res.plan:
        lineas.append(f"- {item.server_id}: {', '.join(item.tools)}")
    
    return {
        "lista_final": [item.model_dump() for item in res.plan],
        "lista_final_lectura": "\n".join(lineas),
        "guia_ejecucion": res.guia_pasos
    }


# Nodo 8: Confirmar el plan con el usuario
class ConfirmacionPlan(BaseModel):
    confirmacion_plan: Literal["Si","No"] = Field(
        description="Si el usuario confirma el despliegue del plan consolidado: 'Si'. Si no lo confirma o expresa dudas: 'No'."
    )

class ConfirmacionPlan(BaseModel):
    confirmacion_plan: Literal["Si","No"] = Field(
        description="Si el usuario confirma el plan: 'Si'. Si pide cambios: 'No'."
    )
    urls_actualizadas: List[str] = Field(
        default_factory=list,
        description="""Lista DEFINITIVA de URLs a usar en el plan. 
        Debe estar actualizada según las indicaciones del usuario. Si el usuario no menciona cambios en las URLs, se asume que la lista anterior es correcta y se devuelve sin cambios."""
    )


#from prompts import PROMPT_CONFIRMACION_PLAN
def confirmar_plan_consolidado(state: State_N):
    respuesta_usuario = state.get("input_externo", "Si")
    urls_actuales = state.get("urls_usuario", [])


    # Registrar interacción en historial
    historial = state.get("historial_conversacion", [])
    plan_mostrado = (
        f"Plan: {state['lista_final_lectura']}\n"
        f"Estrategia: {state['guia_ejecucion']}\n"
        f"URLs en el plan: {json.dumps(urls_actuales)}"
    )
    historial.append({"rol": "planner", "fase": "confirmar_plan", "contenido": plan_mostrado})
    historial.append({"rol": "usuario", "fase": "confirmar_plan", "contenido": respuesta_usuario})

    # Formatear historial para el prompt
    historial_texto = "\n".join(
        f"[{h['fase']}] {h['rol'].upper()}: {h['contenido']}" for h in historial
    )

    mensaje_al_usuario = (
        f"\n--- 📋 PLAN TÉCNICO ---\n{state['lista_final_lectura']}\n"
        f"\n--- 💡 ESTRATEGIA DE EJECUCIÓN ---\n{state['guia_ejecucion']}\n"
        f"\n¿Desea confirmar este plan, su estrategia y las URLs incluidas en él? \n"
    )

    prompt = PROMPT_CONFIRMACION_PLAN.substitute(
        lista_final=state["lista_final"],
        pregunta_usuario=mensaje_al_usuario,
        respuesta_usuario=respuesta_usuario,
        urls_actuales=json.dumps(urls_actuales, indent=2),
        historial_conversacion=historial_texto
    )

    res: ConfirmacionPlan = llm.with_structured_output(ConfirmacionPlan).invoke(
        prompt, 
        config={"tags": ["Planner_08_ConfirmarPlan"]}
    )

    decision = res.confirmacion_plan
    historial_actual = state.get("feedback_final", "")

    # LÓGICA DE ACUMULACIÓN DE HISTORIAL
    if decision == "No":
        # Formateamos el plan que ha sido rechazado o comentado
        plan_anterior = state.get("lista_final_lectura", "")
        nueva_iteracion = f"\n[PLAN ANTERIOR RECHAZADO]\n{plan_anterior}\n[FEEDBACK DEL USUARIO]\n{respuesta_usuario}\n"
        nuevo_feedback = historial_actual + nueva_iteracion
    else:
        nuevo_feedback = historial_actual



    result = {
    "confirmacion_plan": decision,
    "feedback_final": nuevo_feedback,
    "input_externo": None,
    "historial_conversacion": historial
    }

    if decision == "No":
        result["ndloop_intentado"] = False  # Permitir nuevo ciclo NDLoop


    # Actualizar urls_usuario solo si el LLM devuelve una lista distinta
    if res.urls_actualizadas and res.urls_actualizadas != urls_actuales:
        logger.info(f"[Planner] URLs actualizadas por feedback: {res.urls_actualizadas}")
        result["urls_usuario"] = res.urls_actualizadas

    return result



def pausa_ndloop(state: State_N):
    """
    Nodo puente. Su única función es servir como punto de interrupción para 
    devolver el control al orquestador. Al reanudarse, limpia la lista para no hacer bucles.
    """
    logger.info("[Planner] Reanudando tras NDLoop externo. Limpiando herramientas faltantes...")
    return {
        "herramientas_faltantes": [],
        "ndloop_intentado": True
    }


def ndloop_fallido(state: State_N):
    """
    Nodo terminal. Se invoca cuando, tras haber pasado por 'pausa_ndloop',
    el catálogo MCP sigue sin contener las herramientas necesarias.
    Informa al usuario y finaliza la ejecución sin un plan válido.
    """
    faltantes = state.get("herramientas_faltantes", [])
    nombres = [h.get("nombre_sugerido", "desconocida") for h in faltantes]

    mensaje_error = (
        "❌ El sistema NDLoop no ha generado las herramientas necesarias para completar el plan.\n"
        f"   Herramientas que siguen faltando ({len(faltantes)}): {', '.join(nombres) if nombres else '(sin detalle)'}\n"
        "   La ejecución del Planner finaliza SIN un plan válido. "
        "Por favor, revise la solicitud o el funcionamiento del módulo NDLoop."
    )
    logger.error(f"[Planner] {mensaje_error}")
    print(f"\n{mensaje_error}\n")

    # Registrar en historial para trazabilidad
    historial = state.get("historial_conversacion", [])
    historial.append({
        "rol": "planner",
        "fase": "ndloop_fallido",
        "contenido": mensaje_error
    })

    return {
        "ndloop_fallo": True,
        "lista_final_lectura": mensaje_error,
        "guia_ejecucion": "Ejecución abortada: NDLoop no generó las herramientas necesarias.",
        "historial_conversacion": historial
    }


def enrutar_tras_seleccion(state: State_N) -> str:
    """Enruta a la pausa si faltan herramientas, al autocheck si está completo,
    o al nodo de fallo si NDLoop ya fue invocado y las tools siguen faltando."""
    faltantes = state.get("herramientas_faltantes", [])
    if len(faltantes) > 0:
        if state.get("ndloop_intentado", False):
            logger.error(
                f"[Planner] ❌ NDLoop ya fue invocado pero siguen faltando "
                f"{len(faltantes)} herramientas. Abortando para evitar bucle."
            )
            return "ndloop_fallido"
        logger.warning(f"[Planner] ⚠️ Faltan {len(faltantes)} herramientas. Pausando para delegar al Orquestador.")
        return "pausa_ndloop"
    return "auto_check_completitud"




### 6. Construcción del workflow
workflow_N = StateGraph(State_N)

workflow_N.add_node("detectar_casos_de_uso",detectar_casos_de_uso)
workflow_N.add_node("confirmar_casos_de_uso", confirmar_casos_de_uso)
workflow_N.add_node("consulta_mcp_registry", consulta_mcp_registry)
workflow_N.add_node("seleccion_mcp_registry", seleccion_mcp_registry)
workflow_N.add_node("pausa_ndloop", pausa_ndloop)
workflow_N.add_node("ndloop_fallido", ndloop_fallido)  # CORRECCIÓN 1
workflow_N.add_node("auto_check_completitud", auto_check_completitud)
workflow_N.add_node("solicitar_informacion_herramientas", solicitar_informacion_herramientas)
workflow_N.add_node("generar_plan_consolidado", generar_plan_consolidado)
workflow_N.add_node("confirmar_plan_consolidado", confirmar_plan_consolidado)



#workflow_N.add_edge(START, "detectar_casos_de_uso")
workflow_N.set_entry_point("detectar_casos_de_uso")
workflow_N.add_edge("detectar_casos_de_uso", "confirmar_casos_de_uso")

# CAMBIO AQUÍ: Si es "No", volvemos a DETECTAR (Nodo 1), no a confirmar.
workflow_N.add_conditional_edges(
    "confirmar_casos_de_uso",
    lambda state: state["casos_uso_confirmacion"],
    {
        "Si": "consulta_mcp_registry",
        "No": "detectar_casos_de_uso" 
    }
)

workflow_N.add_edge("consulta_mcp_registry","seleccion_mcp_registry")
#workflow_N.add_edge("seleccion_mcp_registry", "auto_check_completitud")
workflow_N.add_conditional_edges(
    "seleccion_mcp_registry",
    enrutar_tras_seleccion,
    {
        "pausa_ndloop": "pausa_ndloop",
        "auto_check_completitud": "auto_check_completitud",
        "ndloop_fallido": "ndloop_fallido"  # CORRECCIÓN 1
    }
)
workflow_N.add_edge("pausa_ndloop", "consulta_mcp_registry")

workflow_N.add_edge("ndloop_fallido", END)

#workflow_N.add_edge("auto_check_completitud", "solicitar_informacion_herramientas")
#Esto es para no tener que pedir URLs si el plan no las requiere o si ya las tenemos
workflow_N.add_conditional_edges(
    "auto_check_completitud",
    enrutar_peticion_urls,
    {
        "solicitar_informacion_herramientas": "solicitar_informacion_herramientas",
        "generar_plan_consolidado": "generar_plan_consolidado"
    }
)
workflow_N.add_edge("solicitar_informacion_herramientas", "generar_plan_consolidado")


workflow_N.add_edge("generar_plan_consolidado", "confirmar_plan_consolidado")

workflow_N.add_conditional_edges(
    "confirmar_plan_consolidado",
    lambda state: state["confirmacion_plan"],
    {
        "Si": END,
        "No": "seleccion_mcp_registry"
    }
)

# 1. Instanciamos la memoria
checkpointer = MemorySaver()

# 2. Compilamos indicando dónde parar.
# Se detiene antes de ejecutar estos nodos, esperando intervención externa.
chain_N = workflow_N.compile(
    checkpointer=checkpointer,
    interrupt_before=[
        "confirmar_casos_de_uso", 
        "pausa_ndloop",        
        "solicitar_informacion_herramientas",
        "confirmar_plan_consolidado"
    ]
)

#Esto es para el orquestator
def to_serializable(state_values):
    """Convierte los modelos de Pydantic en diccionarios para el Orquestador."""
    if isinstance(state_values, dict):
        return {k: to_serializable(v) for k, v in state_values.items()}
    elif isinstance(state_values, list):
        return [to_serializable(i) for i in state_values]
    elif hasattr(state_values, "model_dump"):
        return state_values.model_dump()
    return state_values




if __name__ == "__main__":
    
    async def main():
        # Prompt "típico" del happy path
        #prompt_usuario = """Somos una pequeña empresa especializada en analizar noticias de actualidad, artículos web y tweets de opinión,
    #con el objetivo de generar un reporte estructurado de opinión pública para un cliente concreto, en este caso, Accenture."""
    
        # Este es el que estamos probando
        #prompt_usuario = """Quiero extraer información de noticias para generar un resumen y descargarlo en pdf"""
        #prompt_usuario = """ Quiero extraer noticias sobre Accenture, generar un resumen y enviármelo por email a mi correo."""
        prompt_usuario = """ Haz un reporte financiero de las acciones del IBEX 35 analizando y recomendando SELD, HOLD O BUY, de esta URL: https://www.google.com/finance/beta/quote/I:INDEXBME """

        #config = {"configurable": {"thread_id": "sesion_interactiva_mcp"}}
        thread_id = f"sesion_{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id}}
        logger.info(f"[Planner] Sesión iniciada: {thread_id}")

        #await chain_N.ainvoke({"prompt_usuario": prompt_usuario}, config=config)


        try:
            await chain_N.ainvoke({"prompt_usuario": prompt_usuario}, config=config)
        except Exception as e:
            logger.error(f"[Planner] Error en ejecución: {e}")
            thread_id = config["configurable"]["thread_id"]
            keys_to_delete = [k for k in checkpointer.storage if k[0] == thread_id]
            for k in keys_to_delete:
                del checkpointer.storage[k]
            raise

        while True:
            snapshot = chain_N.get_state(config)

            if not snapshot.next:
                # CORRECCIÓN 1: distinguimos entre éxito y fallo de NDLoop.
                final_state = chain_N.get_state(config).values
                if final_state.get("ndloop_fallo"):
                    print("❌ Planner: Proceso finalizado con ERROR (NDLoop no generó las herramientas).")
                else:
                    print("✅ Planner: Proceso finalizado con éxito.")

                # Borrado memoria
                keys_to_delete = [k for k in checkpointer.storage if k[0] == thread_id]
                for k in keys_to_delete:
                    del checkpointer.storage[k]
                logger.info(f"[Planner] Memoria del thread '{thread_id}' liberada.")
                break


            next_step = snapshot.next[0]
            valores_actuales = snapshot.values
            respuesta = "" # Variable para guardar la entrada del usuario

            #--- FASE FRONTEND (Simulada) ---
            if next_step == "confirmar_casos_de_uso":
                casos = valores_actuales.get("casos_uso", [])
                print(f"\n--- [INTERRUPCIÓN: {next_step}] ---")
                for i, c in enumerate(casos, 1):
                    # Ahora esto funcionará porque 'c' es un dict
                    print(f"  {i}. {c['title']}: {c['description']}") 

                respuesta = input("\n> Confirmación de casos (Si/Corrección): ")

            elif next_step == "solicitar_informacion_herramientas":
                print(f"\n--- [INTERRUPCIÓN: Requisito de Herramienta] ---")
                respuesta = input("""> Introduzca las URLs a analizar (separadas por coma). Si no escribes nada, se usarán las URLs hardcodeadas: " \
                "La adjunto para copiar y pegar:
                https://newsroom.accenture.es/es/news/2025/el-63-de-las-empresas-espanolas-planea-aumentar-las-inversiones-en-tecnologia-de-ia-soberana-en-los-proximos-anos, 
                https://newsroom.accenture.es/es/news/2025/accenture-y-anthropic-se-unen-para-impulsar-la-innovacion-y-el-valor-empresarial-en-diferentes-industrias1, 
                https://newsroom.accenture.es/es/news/2026/scottishpower-renewables-selecciona-a-boslan-compania-de-accenture-para-apoyar-la-fabricacion-del-parque-eolico-east-anglia-two, 
                https://newsroom.accenture.es/es/news/2025/accenture-snowflake-impulsan-la-reinvencion-empresarial-mediante-ia-y-datos2\n""")

            elif next_step == "confirmar_plan_consolidado":
                plan_lectura = valores_actuales.get("lista_final_lectura", "")
                print(f"\n--- [INTERRUPCIÓN: {next_step}] ---")
                print(plan_lectura)
                print("\n--- Estrategia de Ejecución ---\n" \
                f"{valores_actuales.get('guia_ejecucion', '')}\n")

                respuesta = input("\n> Confirmación de plan (Si/Corrección de plan o URLs): ")

            elif next_step == "pausa_ndloop":
                faltantes = valores_actuales.get("herramientas_faltantes", [])
                print(f"\n--- [INTERRUPCIÓN: {next_step}] ---")
                print(f"⚠️ El Planner necesita {len(faltantes)} herramientas nuevas.")
                
                nuevas_tools = []
                for h in faltantes:
                    print(f"  - {h.get('nombre_sugerido')}: {h.get('descripcion_funcionalidad')}")
                    nuevas_tools.append({
                        "name": h.get("nombre_sugerido"),
                        "description": h.get("descripcion_funcionalidad")
                    })
                    
                print(">>> (Simulando que el Orquestador toma el control y crea las herramientas...)")
                respuesta = input("> Presiona Enter para continuar la simulación: ")
                
                # --- INYECCIÓN EN EL ESTADO CORREGIDA ---
                registro_actual = valores_actuales.get("mcp_registry", {})
                
                # 1. Recuperamos las tools que ya habíamos simulado antes (si existen)
                tools_existentes = []
                if "mcp_creado_dinamico" in registro_actual:
                    tools_existentes = registro_actual["mcp_creado_dinamico"].get("tools", [])
                
                # 2. Las sumamos a las nuevas sin borrar nada
                registro_actual["mcp_creado_dinamico"] = {
                    "topic": "herramientas_nuevas",
                    "tools": tools_existentes + nuevas_tools
                }
                
                chain_N.update_state(config, {
                    "input_externo": respuesta, 
                    "mcp_registry": registro_actual # Le pasamos el catálogo acumulativo
                })

            # --- FASE PLANNER: REANUDACIÓN ---
            print(f"\n🔄 Reanudando grafo en nodo '{next_step}'...")
            # Solo actualizamos si NO es pausa_ndloop (ese caso ya actualizó el estado arriba)
            if next_step != "pausa_ndloop":
                chain_N.update_state(config, {"input_externo": respuesta})
            # Continuamos la ejecución
            await chain_N.ainvoke(None, config=config)

            
        # --- 3. IMPRESIÓN DE RESULTADOS ---
        print("\n" + "="*50)
        print("SALIDA FINAL DEL PLANNER")
        print("="*50)
        print(
        #f"\n---N3 MCP Registry completo: \n{final_state.get('mcp_registry')}\n"
        f"\n---N4 Selección tools y temáticas: \n{final_state.get('seleccion_tools_tematicas')}\n"    
        f"\n---N5 Detalles auto-check: \n{final_state.get('autocheck_lectura')}\n"
        f"\n---N7.0 Plan consolidado (bruto): \n{final_state.get('lista_final')}\n"
        f"\n---N7.1 Plan consolidado (lectura): \n{final_state.get('lista_final_lectura')}\n"
        f"\n---N7.2 Guía ejecución para el mcpClient: \n{final_state.get('guia_ejecucion')}\n"
        f"\n---URLS usadas: \n{final_state.get('urls_usuario')}\n"
        ) 

    asyncio.run(main())

