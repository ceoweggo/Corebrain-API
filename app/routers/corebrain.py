from fastapi import APIRouter, Depends, HTTPException, status, Body, Request
from typing import Dict, Any
import json
import traceback

from app.models.database_query import DatabaseQuery, AIQueryResponse
from app.models.api_key import ApiKeyInDB
from app.services import db_service
from app.middleware.authentication import get_api_key
from app.core.permissions import verify_permissions, PermissionError
from app.core.logging import LogEntry, logger
from app.core.querys import AIQuery
from app.models.database_query import QueryResult, MongoDBQuery

router = APIRouter()

@router.post("/query", response_model=AIQueryResponse)
async def natural_language_query(
    query_data: DatabaseQuery = Body(...),
    api_key: ApiKeyInDB = Depends(get_api_key)
):
    """
    Ejecuta una consulta en lenguaje natural sobre la base de datos
    
    # * Sólo puede ejecutar actualmente consultas a 1 collection.
    # * No permite multiples consultas en diferentes collections.
    """
    try:
        # Verificar permisos básicos
        verify_permissions(api_key.level, "read")
        
        # Procesar consulta
        response = await db_service.process_natural_language_query(
            query=query_data.query,
            user_id=None,  # No hay usuario en API key
            api_key_id=api_key.id,
            api_key_level=api_key.level,
            collection_name=query_data.collection_name
        )
        
        return response
        
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
        
    except Exception as e:
        LogEntry("database_query_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("query", query_data.query) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al procesar consulta"
        )


@router.post("/sdk/query")
async def process_sdk_query(
    request: Request,
    api_key: ApiKeyInDB = Depends(get_api_key)
):
    """
    Procesa una consulta en lenguaje natural enviada desde el SDK de CoreBrain.
    Genera la consulta para la base de datos pero NO la ejecuta en el servidor.
    El SDK es responsable de ejecutar la consulta en la base de datos del cliente.
    """
    try:
        # Verificar permisos básicos
        verify_permissions(api_key.level, "read")
        
        # Leer el cuerpo de la solicitud como JSON
        body_bytes = await request.body()
        try:
            # Intentar parsear el JSON
            query_data = json.loads(body_bytes.decode('utf-8'))
        except json.JSONDecodeError:
            raise ValueError("El cuerpo de la solicitud no es un JSON válido")
        
        # Extraer datos de la consulta desde el formato del SDK
        question = query_data.get("question")
        db_schema = query_data.get("db_schema")
        config_id = query_data.get("config_id")
        
        if not question:
            raise ValueError("La consulta (question) no puede estar vacía")
        
        if not db_schema:
            raise ValueError("El esquema de la base de datos (db_schema) no puede estar vacío")
        
        # Registrar consulta recibida
        LogEntry("sdk_query_received", "info") \
            .set_api_key_id(api_key.id) \
            .add_data("question", question) \
            .add_data("config_id", config_id) \
            .log()
        
        # Determinar el tipo de base de datos
        db_type = db_schema.get("type", "").lower()
        
        # Generar la consulta basada en el tipo de base de datos
        if db_type == "sql":
            # Para bases de datos SQL
            engine = db_schema.get("engine", "").lower()
            
            if not engine:
                LogEntry("sql_engine_missing", "warning") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .log()
                engine = "generic"  # Valor por defecto
                
            # Generar consulta SQL usando la clase AIQuery
            try:
                sql_query = await AIQuery.generate_sql_query(question, db_schema, engine)
                
                # Registrar consulta generada
                LogEntry("sql_query_generated", "info") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("sql", sql_query) \
                    .log()
                
                # Devolver la consulta generada SIN ejecutarla
                response = {
                    "query": {
                        "sql": sql_query,
                        "engine": engine
                    },
                    "explanation": f"Se ha generado una consulta SQL para el motor {engine}. Ejecútala en tu SDK.",
                    "config_id": config_id
                }
                print("Response: ", response)
                return response
            except Exception as e:
                LogEntry("sql_query_generation_error", "error") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("error", str(e)) \
                    .log()
                raise ValueError(f"Error al generar consulta SQL: {str(e)}")
                
        elif db_type in ["nosql", "mongodb"]:
            # Para bases de datos MongoDB
            collection_name = query_data.get("collection_name")
            
            # Intentar determinar una colección por defecto si no se especificó
            if not collection_name and db_schema and "tables" in db_schema:
                tables = db_schema.get("tables", {})
                if isinstance(tables, dict) and tables:
                    collection_name = next(iter(tables.keys()))
                elif isinstance(tables, list) and tables:
                    collection_name = tables[0].get("name")
            
            # Generar consulta MongoDB
            try:
                mongo_query = await AIQuery.generate_mongodb_query(question, db_schema, collection_name)
                
                # Registrar consulta generada
                LogEntry("mongo_query_generated", "info") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("collection", mongo_query.collection) \
                    .log()
                
                # Devolver la consulta generada SIN ejecutarla
                response = {
                    "query": mongo_query.model_dump(),
                    "explanation": f"Se ha generado una consulta MongoDB para la colección {mongo_query.collection}. Ejecútala en tu SDK.",
                    "config_id": config_id
                }
                print("Response: ", response)
                return response
            except Exception as e:
                LogEntry("mongo_query_generation_error", "error") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("error", str(e)) \
                    .log()
                raise ValueError(f"Error al generar consulta MongoDB: {str(e)}")
        
        else:
            # Tipo de base de datos no reconocido
            LogEntry("unsupported_db_type", "error") \
                .set_api_key_id(api_key.id) \
                .add_data("question", question) \
                .add_data("db_type", db_type) \
                .log()
                
            return {
                "query": None,
                "explanation": f"Tipo de base de datos no soportado: {db_type}. Verifica la configuración de tu SDK.",
                "error": True,
                "config_id": config_id
            }
        
    except PermissionError as e:
        LogEntry("permission_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    
    except ValueError as e:
        LogEntry("value_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    except Exception as e:
        # Capturar los detalles del error para un mejor diagnóstico
        import traceback
        error_details = traceback.format_exc()
        
        LogEntry("sdk_query_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .add_data("traceback", error_details) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al procesar consulta: {str(e)}"
        )


@router.post("/sdk/query/process-results")
async def process_query_results(
    request: Request,
    api_key: ApiKeyInDB = Depends(get_api_key)
):
    """
    Procesa los resultados de una consulta ejecutada por el SDK.
    Genera una explicación en lenguaje natural de los resultados.
    """
    try:
        # Verificar permisos básicos
        verify_permissions(api_key.level, "read")
        
        # Leer el cuerpo de la solicitud como JSON
        body_bytes = await request.body()
        try:
            # Intentar parsear el JSON
            data = json.loads(body_bytes.decode('utf-8'))
        except json.JSONDecodeError:
            raise ValueError("El cuerpo de la solicitud no es un JSON válido")
        
        # Extraer datos
        question = data.get("question")
        query = data.get("query")
        results = data.get("results")
        config_id = data.get("config_id")
        
        if not question:
            raise ValueError("La consulta (question) no puede estar vacía")
        
        if not query:
            raise ValueError("La consulta generada (query) no puede estar vacía")
        
        if not results:
            raise ValueError("Los resultados (results) no pueden estar vacíos")
        
        # Registrar procesamiento de resultados
        LogEntry("sdk_results_processing", "info") \
            .set_api_key_id(api_key.id) \
            .add_data("question", question) \
            .add_data("config_id", config_id) \
            .log()
        
        # Determinar el tipo de consulta
        query_type = query.get("type", "").lower()
        
        # Procesar resultados según el tipo de consulta
        if query_type == "sql":
            # Para consultas SQL
            try:
                # Crear objeto QueryResult
                result = QueryResult(
                    data=results.get("data", []),
                    count=results.get("count", 0),
                    query_time_ms=results.get("query_time_ms", 0),
                    metadata={
                        "engine": query.get("engine", ""),
                        "database": results.get("database", ""),
                        "config_id": config_id
                    }
                )
                
                # Generar explicación
                explanation = await AIQuery.generate_sql_result_explanation(
                    question, 
                    query.get("sql", ""), 
                    result
                )
                
                # Devolver respuesta
                return {
                    "explanation": explanation,
                    "config_id": config_id
                }
                
            except Exception as e:
                LogEntry("sql_results_processing_error", "error") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("error", str(e)) \
                    .log()
                raise ValueError(f"Error al procesar resultados SQL: {str(e)}")
                
        elif query_type == "mongodb":
            # Para consultas MongoDB
            try:
                # Crear objeto MongoDBQuery
                mongo_query = MongoDBQuery(
                    collection=query.get("collection", ""),
                    operation=query.get("operation", "find"),
                    query=query.get("query", {}),
                    pipeline=query.get("pipeline", []),
                    projection=query.get("projection", {}),
                    sort=query.get("sort", {}),
                    limit=query.get("limit", 10),
                    skip=query.get("skip", 0)
                )
                
                # Crear objeto QueryResult
                result = QueryResult(
                    data=results.get("data", []),
                    count=results.get("count", 0),
                    query_time_ms=results.get("query_time_ms", 0),
                    metadata={
                        "config_id": config_id
                    }
                )
                
                # Generar explicación
                explanation = await AIQuery.generate_result_explanation(
                    question, 
                    mongo_query, 
                    result
                )
                
                # Devolver respuesta
                return {
                    "explanation": explanation,
                    "config_id": config_id
                }
                
            except Exception as e:
                LogEntry("mongo_results_processing_error", "error") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("error", str(e)) \
                    .log()
                raise ValueError(f"Error al procesar resultados MongoDB: {str(e)}")
        
        else:
            # Tipo de consulta no reconocido
            LogEntry("unsupported_query_type", "error") \
                .set_api_key_id(api_key.id) \
                .add_data("question", question) \
                .add_data("query_type", query_type) \
                .log()
                
            return {
                "explanation": f"Tipo de consulta no soportado: {query_type}. Verifica la configuración de tu SDK.",
                "error": True,
                "config_id": config_id
            }
        
    except PermissionError as e:
        LogEntry("permission_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    
    except ValueError as e:
        LogEntry("value_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    except Exception as e:
        # Capturar los detalles del error para un mejor diagnóstico
        import traceback
        error_details = traceback.format_exc()
        
        LogEntry("sdk_results_processing_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .add_data("traceback", error_details) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al procesar resultados: {str(e)}"
        )


@router.post("/sdk/query/identify-collections")
async def identify_query_collections(
    request: Request,
    api_key: ApiKeyInDB = Depends(get_api_key)
):
    """
    Identifica las colecciones relevantes para una consulta en lenguaje natural.
    Útil para determinar qué colecciones consultar antes de generar la consulta.
    """
    try:
        # Verificar permisos básicos
        verify_permissions(api_key.level, "read")
        
        # Leer el cuerpo de la solicitud como JSON
        body_bytes = await request.body()
        try:
            # Intentar parsear el JSON
            data = json.loads(body_bytes.decode('utf-8'))
        except json.JSONDecodeError:
            raise ValueError("El cuerpo de la solicitud no es un JSON válido")
        
        # Extraer datos
        question = data.get("question")
        db_schema = data.get("db_schema")
        config_id = data.get("config_id")
        
        if not question:
            raise ValueError("La consulta (question) no puede estar vacía")
        
        if not db_schema:
            raise ValueError("El esquema de la base de datos (db_schema) no puede estar vacío")
        
        # Registrar solicitud
        LogEntry("collection_identification_request", "info") \
            .set_api_key_id(api_key.id) \
            .add_data("question", question) \
            .add_data("config_id", config_id) \
            .log()
        
        # Determinar el tipo de base de datos
        db_type = db_schema.get("type", "").lower()
        
        # Identificar colecciones según el tipo de base de datos
        if db_type == "sql":
            # Para bases de datos SQL, identificar tablas
            try:
                # Extraer tablas del esquema
                tables = db_schema.get("tables", {})
                
                # Crear prompt para identificar tablas relevantes
                system_prompt = f"""
                Eres un asistente especializado en identificar tablas relevantes para consultas SQL.
                
                ESTRUCTURA DE LA BASE DE DATOS:
                {json.dumps(tables, indent=2)}
                
                Tu tarea es:
                1. Analizar la consulta del usuario
                2. Identificar qué tablas son relevantes para responder la consulta
                3. Devolver una lista de nombres de tablas en formato JSON
                
                Responde ÚNICAMENTE con un array JSON de nombres de tablas, sin ningún otro texto.
                """
                
                # Inicializar cliente OpenAI
                client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)
                
                # Enviar solicitud a OpenAI
                response = await client.chat.completions.create(
                    model=settings.OPENAI.OPENAI_MODEL,
                    max_tokens=settings.OPENAI.MAX_TOKENS,
                    temperature=0.2,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": question}
                    ]
                )
                
                # Extraer respuesta
                tables_json = response.choices[0].message.content.strip()
                
                # Limpiar respuesta (eliminar backticks, etc.)
                if tables_json.startswith('```') and tables_json.endswith('```'):
                    tables_json = tables_json[3:-3].strip()
                elif '```' in tables_json:
                    # Extraer contenido entre las primeras comillas de código triple
                    match = re.search(r'```(?:json)?(.*?)```', tables_json, re.DOTALL)
                    if match:
                        tables_json = match.group(1).strip()
                
                # Parsear JSON
                relevant_tables = json.loads(tables_json)
                
                # Registrar tablas identificadas
                LogEntry("tables_identified", "info") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("tables", relevant_tables) \
                    .log()
                
                # Devolver respuesta
                return {
                    "tables": relevant_tables,
                    "config_id": config_id
                }
                
            except Exception as e:
                LogEntry("table_identification_error", "error") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("error", str(e)) \
                    .log()
                raise ValueError(f"Error al identificar tablas: {str(e)}")
                
        elif db_type in ["nosql", "mongodb"]:
            # Para bases de datos MongoDB, identificar colecciones
            try:
                # Extraer colecciones del esquema
                collections = db_schema.get("collections", {})
                if not collections and "tables" in db_schema:
                    # Si no hay colecciones pero hay tablas, usar tablas como colecciones
                    collections = db_schema.get("tables", {})
                
                # Crear prompt para identificar colecciones relevantes
                system_prompt = f"""
                Eres un asistente especializado en identificar colecciones relevantes para consultas MongoDB.
                
                ESTRUCTURA DE LA BASE DE DATOS:
                {json.dumps(collections, indent=2)}
                
                Tu tarea es:
                1. Analizar la consulta del usuario
                2. Identificar qué colecciones son relevantes para responder la consulta
                3. Devolver una lista de nombres de colecciones en formato JSON
                
                Responde ÚNICAMENTE con un array JSON de nombres de colecciones, sin ningún otro texto.
                """
                
                # Inicializar cliente OpenAI
                client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)
                
                # Enviar solicitud a OpenAI
                response = await client.chat.completions.create(
                    model=settings.OPENAI.OPENAI_MODEL,
                    max_tokens=settings.OPENAI.MAX_TOKENS,
                    temperature=0.2,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": question}
                    ]
                )
                
                # Extraer respuesta
                collections_json = response.choices[0].message.content.strip()
                
                # Limpiar respuesta (eliminar backticks, etc.)
                if collections_json.startswith('```') and collections_json.endswith('```'):
                    collections_json = collections_json[3:-3].strip()
                elif '```' in collections_json:
                    # Extraer contenido entre las primeras comillas de código triple
                    match = re.search(r'```(?:json)?(.*?)```', collections_json, re.DOTALL)
                    if match:
                        collections_json = match.group(1).strip()
                
                # Parsear JSON
                relevant_collections = json.loads(collections_json)
                
                # Registrar colecciones identificadas
                LogEntry("collections_identified", "info") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("collections", relevant_collections) \
                    .log()
                
                # Devolver respuesta
                return {
                    "collections": relevant_collections,
                    "config_id": config_id
                }
                
            except Exception as e:
                LogEntry("collection_identification_error", "error") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("error", str(e)) \
                    .log()
                raise ValueError(f"Error al identificar colecciones: {str(e)}")
        
        else:
            # Tipo de base de datos no reconocido
            LogEntry("unsupported_db_type", "error") \
                .set_api_key_id(api_key.id) \
                .add_data("question", question) \
                .add_data("db_type", db_type) \
                .log()
                
            return {
                "error": True,
                "message": f"Tipo de base de datos no soportado: {db_type}. Verifica la configuración de tu SDK.",
                "config_id": config_id
            }
        
    except PermissionError as e:
        LogEntry("permission_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    
    except ValueError as e:
        LogEntry("value_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    except Exception as e:
        # Capturar los detalles del error para un mejor diagnóstico
        import traceback
        error_details = traceback.format_exc()
        
        LogEntry("collection_identification_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .add_data("traceback", error_details) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al identificar colecciones: {str(e)}"
        ) 