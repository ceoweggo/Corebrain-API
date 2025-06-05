
import anthropic
import uuid
import json
import time
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from app.core.config import settings
from app.core.cache import Cache
from app.core.logging import LogEntry
from app.core.security import sanitize_mongo_query
from app.core.permissions import verify_permissions
from app.models.message import MessageInDB, AIResponse, MessageWithAIResponse
from app.models.conversation import ConversationInDB, ConversationUpdate
from app.database.repositories.message_repository import MessageRepository
from app.database.repositories.conversation_repository import ConversationRepository
from motor.motor_asyncio import AsyncIOMotorClient

db_client = AsyncIOMotorClient(settings.MONGODB.MONGODB_URL)
db = db_client[settings.MONGODB.MONGODB_DB_NAME]

# Repositorios
message_repo = MessageRepository(db)
conversation_repo = ConversationRepository(db)


async def create_conversation(user_id: Optional[str], api_key_id: str, title: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> ConversationInDB:
    """
    Crea una nueva conversación
    """
    conversation_id = str(uuid.uuid4())
    
    conversation = ConversationInDB(
        id=conversation_id,
        user_id=user_id,
        api_key_id=api_key_id,
        title=title or "Nueva conversación",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        metadata=metadata or {}
    )
    
    await conversation_repo.create(conversation)
    
    # Registrar creación
    LogEntry("conversation_created") \
        .set_user_id(user_id) \
        .set_api_key_id(api_key_id) \
        .add_data("conversation_id", conversation_id) \
        .log()
    
    return conversation

async def get_conversation_history(conversation_id: str, limit: int = 10) -> List[MessageInDB]:
    """
    Obtiene el historial de mensajes de una conversación
    """
    messages = await message_repo.find_by_conversation_id(conversation_id, limit)
    return messages

async def process_message(
    content: str, 
    conversation_id: str, 
    user_id: Optional[str], 
    api_key_id: str,
    api_key_level: str,
    metadata: Optional[Dict[str, Any]] = None
) -> MessageWithAIResponse:
    """
    Procesa un mensaje con Anthropic y devuelve una respuesta
    """
    start_time = time.time()
    
    # Añadir log de inicio para depuración
    LogEntry("process_message_started", "debug") \
        .set_api_key_id(api_key_id) \
        .add_data("conversation_id", conversation_id) \
        .add_data("content", content[:100] + '...' if len(content) > 100 else content) \
        .log()
    
    # Verificar que la conversación existe o crear una nueva
    conversation = await conversation_repo.find_by_id(conversation_id)
    
    if not conversation:
        # Crear nueva conversación
        LogEntry("creating_new_conversation", "debug") \
            .set_api_key_id(api_key_id) \
            .log()
        conversation = await create_conversation(user_id, api_key_id)
    
    # Variables para seguimiento de costos
    token_usage = {
        "input": 0,
        "output": 0,
        "total": 0
    }
    api_calls = 0
    
    # Verificar permisos
    try:
        verify_permissions(api_key_level, "write")
    except PermissionError as e:
        LogEntry("permission_error", "error") \
            .set_api_key_id(api_key_id) \
            .add_data("error", str(e)) \
            .log()
        raise
    
    # Guardar mensaje del usuario
    user_message_id = str(uuid.uuid4())
    user_message = MessageInDB(
        id=user_message_id,
        content=content,
        conversation_id=conversation_id,
        user_id=user_id,
        api_key_id=api_key_id,
        is_user=True,
        created_at=datetime.now(),
        metadata=metadata or {}
    )
    
    await message_repo.create(user_message)
    
    # Obtener historial de mensajes para contexto
    message_history = await get_conversation_history(conversation_id)
    LogEntry("message_history_retrieved", "debug") \
        .set_api_key_id(api_key_id) \
        .add_data("conversation_id", conversation_id) \
        .add_data("history_length", len(message_history)) \
        .log()
    
    # Analizar la consulta de usuario para determinar si requiere consulta a la base de datos
    requires_db_query = any(keyword in content.lower() for keyword in [
        "transacciones", "transactions", "cuantas", "cuántas", "media", "promedio",
        "total", "registros", "documentos", "consulta", "query", "base de datos",
        "database", "colección", "collection", "precio", "price", "amount"
    ])
    
    if not requires_db_query:
        LogEntry("skipping_db_query", "debug") \
            .set_api_key_id(api_key_id) \
            .add_data("reason", "query does not seem to require db access") \
            .log()
    
    # Intentar obtener respuesta de caché
    cache_key = Cache.generate_key(
        "ai_response", 
        content, 
        conversation_id, 
        [msg.id for msg in message_history[-5:] if msg.id != user_message_id]
    )
    
    cached_response = Cache.get(cache_key)
    ai_response = None
    
    if cached_response:
        LogEntry("cache_hit", "debug") \
            .set_api_key_id(api_key_id) \
            .add_data("conversation_id", conversation_id) \
            .log()
        ai_response = AIResponse(**cached_response)
        
        # Recuperar uso de tokens del caché si está disponible
        if "tokens" in ai_response.metadata:
            token_usage = ai_response.metadata["tokens"]
    else:
        LogEntry("cache_miss", "debug") \
            .set_api_key_id(api_key_id) \
            .add_data("conversation_id", conversation_id) \
            .log()
        
        # Formatear historial para Anthropic
        formatted_history = []
        for msg in message_history:
            role = "user" if msg.is_user else "assistant"
            formatted_history.append({
                "role": role,
                "content": msg.content
            })
        
        # Inicializar cliente Anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC.ANTHROPIC_API_KEY)
        
        try:
            # Solo obtenemos información de la BD si la consulta parece requerirla
            db_info = None
            if requires_db_query:
                try:
                    # Obtener información de la base de datos para el contexto
                    from app.services import db_service
                    db_info = await db_service.get_database_info()
                    LogEntry("db_info_retrieved", "debug") \
                        .set_api_key_id(api_key_id) \
                        .add_data("collections", list(db_info.get("collections", {}).keys())) \
                        .log()
                except Exception as e:
                    LogEntry("db_info_retrieval_error", "error") \
                        .set_api_key_id(api_key_id) \
                        .add_data("error", str(e)) \
                        .log()
                    db_info = None
            
            # Crear un sistema prompt basado en si tenemos info de BD
            if db_info:
                db_context = json.dumps(db_info, indent=2)
                system_prompt = f"""
                Eres CoreBrain, un asistente IA experto conectado a una base de datos MongoDB.
                
                Información sobre la base de datos:
                {db_context}
                
                INSTRUCCIONES IMPORTANTES:
                1. Si detectas que es una consulta sobre la base de datos, proporciona UNA SOLA consulta MongoDB específica y ejecutable que responda la pregunta.
                2. No añadas consultas si no estás seguro de la estructura exacta o si no es necesario.
                3. Sigue EXACTAMENTE este formato:
                   ```mongodb
                   <UNA SOLA consulta MongoDB ejecutable>
                   ```
                4. Si no puedes formular una consulta precisa, responde con "ERROR: " seguido de una explicación clara del problema.
                5. No uses variables, solo consultas MongoDB literales que puedan ejecutarse directamente.
                
                El sistema ejecutará automáticamente la consulta que proporciones.
                """
            else:
                system_prompt = "Eres CoreBrain, un asistente IA experto y útil. Responde de manera concisa y precisa."
            
            # Primera llamada a Claude solo si es necesario
            LogEntry("calling_anthropic_api", "debug") \
                .set_api_key_id(api_key_id) \
                .add_data("with_db_info", db_info is not None) \
                .log()
                
            # Incrementar contador de llamadas a API
            api_calls += 1
                
            # Enviar consulta a Anthropic
            response = client.messages.create(
                model=settings.ANTHROPIC.ANTHROPIC_MODEL,
                max_tokens=settings.ANTHROPIC.MAX_TOKENS,
                temperature=settings.ANTHROPIC.TEMPERATURE,
                messages=formatted_history + [{"role": "user", "content": content}],
                system=system_prompt
            )
            
            # Actualizar uso de tokens
            token_usage["input"] += response.usage.input_tokens
            token_usage["output"] += response.usage.output_tokens
            token_usage["total"] += response.usage.input_tokens + response.usage.output_tokens
            
            # Extraer respuesta inicial con la consulta sugerida
            initial_response = response.content[0].text
            
            # Si la respuesta empieza con ERROR, no procesamos más
            if initial_response.strip().startswith("ERROR:"):
                LogEntry("anthropic_reported_error", "warning") \
                    .set_api_key_id(api_key_id) \
                    .add_data("error", initial_response) \
                    .log()
                    
                ai_content = initial_response
            # Si tenemos información de BD, intentamos ejecutar consultas
            elif db_info:
                # Extraer consultas MongoDB de la respuesta
                mongo_queries = extract_mongodb_queries(initial_response)
                
                # Imprimir la consulta para diagnóstico
                print("====== CONSULTA MONGODB A EJECUTAR ======")
                if mongo_queries:
                    for i, query in enumerate(mongo_queries):
                        print(f"Consulta {i+1}:\n{query}\n")
                else:
                    print("No se encontraron consultas MongoDB en la respuesta.")
                print("=======================================")
                
                LogEntry("extracted_queries", "debug") \
                    .set_api_key_id(api_key_id) \
                    .add_data("query_count", len(mongo_queries)) \
                    .add_data("queries", mongo_queries) \
                    .log()
                
                if mongo_queries:
                    # Ejecutar cada consulta encontrada (limitamos a la primera para optimizar)
                    query_results = []
                    query = mongo_queries[0]  # Solo tomamos la primera consulta
                    
                    try:
                        # Ejecutar la consulta extraída
                        LogEntry("executing_mongodb_query", "debug") \
                            .set_api_key_id(api_key_id) \
                            .add_data("query", query) \
                            .log()
                            
                        result = await execute_mongodb_query(query, api_key_level)
                        
                        # Formatear el resultado para incluirlo
                        formatted_result = json.dumps(result, default=str, indent=2)
                        query_results.append({
                            "query": query,
                            "result": formatted_result,
                            "success": True
                        })
                        
                        LogEntry("query_execution_success", "debug") \
                            .set_api_key_id(api_key_id) \
                            .add_data("result_length", len(result) if isinstance(result, list) else "single_value") \
                            .log()
                    except Exception as e:
                        LogEntry("query_execution_error", "error") \
                            .set_api_key_id(api_key_id) \
                            .add_data("query", query) \
                            .add_data("error", str(e)) \
                            .log()
                            
                        query_results.append({
                            "query": query,
                            "error": str(e),
                            "success": False
                        })
                    
                    # Si tenemos resultados exitosos, hacer una segunda llamada
                    if query_results and query_results[0]["success"]:
                        # Preparar mensaje con los resultados de las consultas
                        query_results_text = f"Resultados de la consulta:\n```json\n{query_results[0]['result']}\n```"
                        
                        # Segunda llamada: Interpretar resultados
                        LogEntry("calling_anthropic_for_interpretation", "debug") \
                            .set_api_key_id(api_key_id) \
                            .log()
                        
                        # Incrementar contador de llamadas a API
                        api_calls += 1
                            
                        final_response = client.messages.create(
                            model=settings.ANTHROPIC.ANTHROPIC_MODEL,
                            max_tokens=settings.ANTHROPIC.MAX_TOKENS,
                            temperature=settings.ANTHROPIC.TEMPERATURE,
                            messages=[
                                {"role": "user", "content": content},
                                {"role": "assistant", "content": "Voy a ejecutar una consulta para responder a tu pregunta."},
                                {"role": "user", "content": f"Ejecuté la consulta que generaste. Aquí están los resultados:\n\n{query_results_text}\n\nPor favor, interpreta estos resultados y responde a mi pregunta original de forma concisa."}
                            ],
                            system="Eres CoreBrain, un asistente IA experto en análisis de datos de MongoDB. Proporciona interpretaciones claras y concisas de los resultados de las consultas."
                        )
                        
                        # Actualizar uso de tokens
                        token_usage["input"] += final_response.usage.input_tokens
                        token_usage["output"] += final_response.usage.output_tokens
                        token_usage["total"] += final_response.usage.input_tokens + final_response.usage.output_tokens
                        
                        ai_content = final_response.content[0].text
                    elif query_results and not query_results[0]["success"]:
                        # Si la consulta falló, proporcionar una respuesta útil sin hacer otra llamada a Claude
                        ai_content = f"Lo siento, hubo un error al ejecutar la consulta en la base de datos: {query_results[0]['error']}. Por favor, reformula tu pregunta o contacta con soporte técnico si el problema persiste."
                    else:
                        # Si no hay resultados, usar la respuesta inicial
                        ai_content = initial_response
                else:
                    # Si no se encontraron consultas, usar la respuesta inicial
                    ai_content = initial_response
            else:
                # Si no tenemos info de BD, simplemente usamos la respuesta directa
                ai_content = initial_response
            
            # Calcular costo monetario (según precios de Anthropic para Claude-3 Opus)
            # Precios aproximados: $15 por millón de tokens de entrada, $75 por millón de tokens de salida
            cost_per_1m_input = 15.0  # $15 por millón de tokens de entrada
            cost_per_1m_output = 75.0  # $75 por millón de tokens de salida
            
            cost_input = (token_usage["input"] / 1000000) * cost_per_1m_input
            cost_output = (token_usage["output"] / 1000000) * cost_per_1m_output
            total_cost = cost_input + cost_output
            
            # Crear respuesta de IA
            ai_response_id = str(uuid.uuid4())
            ai_response = AIResponse(
                id=ai_response_id,
                content=ai_content,
                model=settings.ANTHROPIC.ANTHROPIC_MODEL,
                created_at=datetime.now(),
                tokens=token_usage,
                processing_time=time.time() - start_time,
                metadata={
                    "anthropic_version": anthropic.__version__,
                    "model": settings.ANTHROPIC.ANTHROPIC_MODEL,
                    "db_info_provided": db_info is not None,
                    "queries_executed": len(query_results) if 'query_results' in locals() else 0,
                    "api_calls": api_calls,
                    "cost": {
                        "input_usd": cost_input,
                        "output_usd": cost_output,
                        "total_usd": total_cost
                    }
                }
            )
            
            # Guardar en caché
            Cache.set(cache_key, ai_response.model_dump(), ttl=3600)  # 1 hora
            
        except Exception as e:
            # Registrar error
            LogEntry("anthropic_api_error", "error") \
                .set_api_key_id(api_key_id) \
                .add_data("conversation_id", conversation_id) \
                .add_data("error", str(e)) \
                .log()
            
            # Crear respuesta de error
            ai_response_id = str(uuid.uuid4())
            ai_response = AIResponse(
                id=ai_response_id,
                content="Lo siento, he tenido un problema al procesar tu mensaje. Por favor, intenta de nuevo.",
                model=settings.ANTHROPIC.ANTHROPIC_MODEL,
                created_at=datetime.now(),
                processing_time=time.time() - start_time,
                metadata={"error": str(e)}
            )
    
    # Guardar respuesta de IA como mensaje
    ai_message = MessageInDB(
        id=ai_response.id,
        content=ai_response.content,
        conversation_id=conversation_id,
        is_user=False,
        created_at=ai_response.created_at,
        metadata={
            "model": ai_response.model,
            "tokens": ai_response.tokens,
            "processing_time": ai_response.processing_time,
            "cost": ai_response.metadata.get("cost", {"total_usd": 0})
        }
    )
    
    await message_repo.create(ai_message)
    
    # Actualizar metadatos de la conversación
    current_costs = conversation.metadata.get("costs", {
        "tokens": {"input": 0, "output": 0, "total": 0},
        "usd": {"input": 0, "output": 0, "total": 0},
        "api_calls": 0
    })
    
    # Actualizar costos acumulados
    current_costs["tokens"]["input"] += token_usage["input"]
    current_costs["tokens"]["output"] += token_usage["output"]
    current_costs["tokens"]["total"] += token_usage["total"]
    current_costs["api_calls"] += api_calls
    
    if "cost" in ai_response.metadata:
        current_costs["usd"]["input"] += ai_response.metadata["cost"]["input_usd"]
        current_costs["usd"]["output"] += ai_response.metadata["cost"]["output_usd"]
        current_costs["usd"]["total"] += ai_response.metadata["cost"]["total_usd"]
    
    # Actualizar metadatos de la conversación incluyendo los costos
    update_data = ConversationUpdate(
        last_message_at=datetime.now(),
        message_count=conversation.message_count + 2,  # +2 por mensaje usuario + IA
        metadata={
            **conversation.metadata,
            "costs": current_costs
        }
    )
    
    await conversation_repo.update(conversation_id, update_data)
    
    # Registrar completado con información de costos
    LogEntry("message_processed", "info") \
        .set_user_id(user_id) \
        .set_api_key_id(api_key_id) \
        .add_data("conversation_id", conversation_id) \
        .add_data("processing_time", time.time() - start_time) \
        .add_data("tokens", token_usage) \
        .add_data("cost_usd", ai_response.metadata.get("cost", {}).get("total_usd", 0)) \
        .log()
    
    # Crear respuesta combinada
    response = MessageWithAIResponse(
        user_message=user_message,
        ai_response=ai_response
    )
    
    return response

def extract_mongodb_queries(text: str) -> List[str]:
    """
    Extrae consultas MongoDB del formato markdown code blocks
    """
    import re
    pattern = r"```(?:mongodb|js|javascript)?\s*([\s\S]*?)```"
    matches = re.findall(pattern, text)
    return [query.strip() for query in matches if query.strip()]

async def execute_mongodb_query(query_str: str, api_key_level: str) -> Any:
    """
    Ejecuta una consulta MongoDB de forma segura
    """
    from app.database.session import get_database
    from app.core.security import sanitize_mongo_query
    import re
    
    # Obtener la instancia de la base de datos
    db = get_database()
    
    # Extraer la colección y la operación
    collection_match = re.search(r"db\.(\w+)\.(find|aggregate|count|distinct)", query_str)
    if not collection_match:
        raise ValueError("No se pudo identificar la colección o la operación")
    
    collection_name = collection_match.group(1)
    operation = collection_match.group(2)
    
    # Verificar permisos para acceder a esta colección
    from app.core.permissions import check_collection_access
    if not check_collection_access(api_key_level, collection_name):
        raise PermissionError(f"Sin acceso a la colección: {collection_name}")
    
    # Analizar y sanitizar la consulta
    if operation == "find":
        # Extraer los parámetros de find
        find_params = re.search(r"find\((.*)\)", query_str)
        if not find_params:
            raise ValueError("Parámetros de find no válidos")
        
        # Separar query y projection
        params_str = find_params.group(1).strip()
        if params_str:
            # Evaluar la consulta de forma segura
            import ast
            # Reemplazar notación de MongoDB por Python
            params_str = params_str.replace("$", "_$_")
            params_str = params_str.replace("_$_", "$")
            
            # Intentar parsear los parámetros
            try:
                if "," in params_str:
                    query_part, projection_part = params_str.split(",", 1)
                    query = json.loads(query_part)
                    projection = json.loads(projection_part)
                else:
                    query = json.loads(params_str)
                    projection = None
            except json.JSONDecodeError:
                raise ValueError("No se pudo parsear la consulta JSON")
        else:
            query = {}
            projection = None
        
        # Sanitizar la consulta
        query = sanitize_mongo_query(query)
        
        # Ejecutar la consulta
        collection = db[collection_name]
        cursor = collection.find(query, projection)
        
        # Limitar resultados para seguridad
        limit = 100
        return await cursor.to_list(length=limit)
    
    elif operation == "aggregate":
        # Extraer el pipeline de agregación
        agg_match = re.search(r"aggregate\(\[(.*)\]\)", query_str, re.DOTALL)
        if not agg_match:
            raise ValueError("Pipeline de agregación no válido")
        
        # Parsear el pipeline
        pipeline_str = agg_match.group(1).strip()
        
        try:
            # Convertir el string del pipeline a una lista de etapas
            pipeline_str = pipeline_str.replace("$", "_$_")
            pipeline_str = f"[{pipeline_str}]"
            pipeline_str = pipeline_str.replace("_$_", "$")
            
            pipeline = json.loads(pipeline_str)
        except json.JSONDecodeError:
            raise ValueError("No se pudo parsear el pipeline JSON")
        
        # Sanitizar cada etapa del pipeline
        pipeline = [sanitize_mongo_query(stage) for stage in pipeline]
        
        # Ejecutar la agregación
        collection = db[collection_name]
        cursor = collection.aggregate(pipeline)
        
        # Limitar resultados para seguridad
        limit = 100
        return await cursor.to_list(length=limit)
    
    elif operation == "count":
        # Extraer parámetros de count
        count_match = re.search(r"count\((.*)\)", query_str)
        if count_match:
            params_str = count_match.group(1).strip()
            if params_str:
                try:
                    query = json.loads(params_str)
                except json.JSONDecodeError:
                    raise ValueError("No se pudo parsear la consulta JSON")
            else:
                query = {}
        else:
            query = {}
        
        # Sanitizar la consulta
        query = sanitize_mongo_query(query)
        
        # Ejecutar count
        collection = db[collection_name]
        return await collection.count_documents(query)
    
    elif operation == "distinct":
        # Extraer parámetros
        distinct_match = re.search(r"distinct\((.*)\)", query_str)
        if not distinct_match:
            raise ValueError("Parámetros de distinct no válidos")
        
        params_str = distinct_match.group(1).strip()
        
        try:
            params = json.loads(f"[{params_str}]")
            field = params[0]
            query = params[1] if len(params) > 1 else {}
        except json.JSONDecodeError:
            raise ValueError("No se pudo parsear los parámetros")
        
        # Sanitizar
        query = sanitize_mongo_query(query)
        
        # Ejecutar
        collection = db[collection_name]
        return await collection.distinct(field, query)
    
    else:
        raise ValueError(f"Operación no soportada: {operation}")