from typing import Dict, Any, Optional, List, Tuple
from langdetect import detect

import json
import re
import time
import openai

from app.core.config import settings
from app.core.logging import logger
from app.core.utils import Utils
from app.core.diagnostic import Diagnostic
from app.models.database_query import MongoDBQuery, QueryResult

class AIQuery:
    def __init__(self, query: str, collection_name: str = None, limit: int = 50, 
                 config_id: Optional[str] = None, db_schema: Optional[Dict[str, Any]] = None):
        """
        Inicializa un objeto AIQuery para procesar consultas en lenguaje natural.
        
        Args:
            query: Consulta en lenguaje natural
            collection_name: Nombre de la colección/tabla a consultar (opcional)
            limit: Límite de resultados (por defecto 50)
            config_id: ID de configuración (opcional)
            db_schema: Esquema de la base de datos (opcional)
        """
        self.query = query
        self.collection_name = collection_name
        self.limit = limit
        self.config_id = config_id
        self.db_schema = db_schema

    @staticmethod
    async def generate_sql_query(
        query: str, 
        db_info: Dict[str, Any],
        engine: str
    ) -> str:
        """
        Genera una consulta SQL a partir de una consulta en lenguaje natural y la información de la base de datos.
        
        Args:
            query: Consulta en lenguaje natural
            db_info: Información del esquema de la base de datos
            engine: Motor de base de datos (sqlite, mysql, postgresql)
            
        Returns:
            Consulta SQL generada
        """
        # Preparar el contexto con la información de la base de datos
        db_context = json.dumps(db_info, indent=2, default=str)  # Usar default=str para manejar tipos especiales
        
        # Limitar el contexto si es demasiado grande
        if len(db_context) > 10000:
            # Extraer solo las primeras tablas
            tables = list(db_info.get("tables", {}).keys())
            truncated_tables = tables[:5]
            
            truncated_db_info = {
                "engine": db_info.get("engine", engine),
                "tables": {
                    name: db_info["tables"][name]
                    for name in truncated_tables if name in db_info.get("tables", {})
                }
            }
            
            db_context = json.dumps(truncated_db_info, indent=2, default=str)
            db_context += f"\n\n... y {len(tables) - 5} tablas más."
        
        # Crear system prompt específico para SQL
        system_prompt = f"""
        You are an assistant specialized in translating natural language queries into SQL.

        DATABASE STRUCTURE:
        {db_context}

        DATABASE ENGINE: {engine}

        Your task is:
        1. Analyze the user's query
        2. Determine which tables should be queried
        3. Construct a valid SQL query for the {engine} engine
        4. Add the right capitalization to the names in functions
        5. Return ONLY the SQL query, without any other text or explanation

        RULES:
        - Use {engine}-specific syntax
        - For aggregation queries, use GROUP BY when necessary
        - Limit results to a maximum of 100 rows using LIMIT 100
        - Do not use advanced features specific to recent versions that may not be available
        - If the query is unclear, generate a simple query that retrieves relevant information
        - Add the right capitalization to the names of columns

        Respond ONLY with the SQL query, without any other text or explanation.
        """
        print("Prompt a pasar a la IA: ", system_prompt)
        
        try:
            # Inicializar cliente de OpenAI
            client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)
            
            # Enviar solicitud a OpenAI
            response = await client.chat.completions.create(
                model=settings.OPENAI.OPENAI_MODEL,
                max_tokens=settings.OPENAI.MAX_TOKENS,
                temperature=0.2,  # Temperatura baja para respuestas más deterministas
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ]
            )
            
            # Extraer la consulta SQL generada
            sql_query = response.choices[0].message.content.strip()
            
            # Limpiar la consulta (eliminar comentarios, etc.)
            sql_query = AIQuery.clean_sql_query(sql_query)
            
            return sql_query
            
        except Exception as e:
            logger.error(f"Error al generar consulta SQL: {str(e)}")
            # En caso de error, generar una consulta segura y simple
            safe_table = next(iter(db_info.get("tables", {}).keys()), "users")
            return f"SELECT * FROM {safe_table} LIMIT 10"

    
    @staticmethod
    def clean_sql_query(sql_query: str) -> str:
        """
        Limpia una consulta SQL eliminando comentarios, backticks y otros elementos innecesarios.
        
        Args:
            sql_query: Consulta SQL a limpiar
            
        Returns:
            Consulta SQL limpia
        """
        # Eliminar bloques de código markdown
        if sql_query.startswith('```') and sql_query.endswith('```'):
            sql_query = sql_query[3:-3].strip()
        elif '```' in sql_query:
            # Extraer contenido entre las primeras comillas de código triple
            match = re.search(r'```(?:sql)?(.*?)```', sql_query, re.DOTALL)
            if match:
                sql_query = match.group(1).strip()
        
        # Eliminar especificadores de lenguaje al inicio
        if sql_query.lower().startswith('sql'):
            sql_query = sql_query[3:].strip()
        
        # Eliminar comentarios de una línea
        sql_query = re.sub(r'--.*$', '', sql_query, flags=re.MULTILINE)
        
        # Eliminar comentarios de múltiples líneas
        sql_query = re.sub(r'/\*.*?\*/', '', sql_query, flags=re.DOTALL)
        
        # Eliminar líneas vacías y espacios extras
        sql_query = '\n'.join(line.strip() for line in sql_query.split('\n') if line.strip())
        
        return sql_query

    @staticmethod
    async def execute_sql_query(
        sql_query: str,
        db_config: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], float]:
        """
        Ejecuta una consulta SQL en la base de datos configurada.
        
        Args:
            sql_query: Consulta SQL a ejecutar
            db_config: Configuración de la base de datos
            
        Returns:
            Tupla con (resultado, tiempo_ejecución)
        """
        start_time = time.time()
        engine = db_config.get("engine", "").lower()
        result_data = []
        
        try:
            if engine == "sqlite":
                result_data = await AIQuery.execute_sqlite_query(sql_query, db_config)
            elif engine == "mysql":
                result_data = await AIQuery.execute_mysql_query(sql_query, db_config)
            elif engine == "postgresql":
                result_data = await AIQuery.execute_postgresql_query(sql_query, db_config)
            else:
                raise ValueError(f"Motor SQL no soportado: {engine}")
            
            # Calcular tiempo de ejecución
            execution_time = time.time() - start_time
            
            # Limitar resultados si son demasiados
            if len(result_data) > 100:
                result_data = result_data[:100]
            
            return result_data, execution_time
            
        except Exception as e:
            # Registrar error
            logger.error(f"Error al ejecutar consulta SQL ({engine}): {str(e)}")
            execution_time = time.time() - start_time
            raise

    @staticmethod
    async def execute_sqlite_query(sql_query: str, db_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Ejecuta una consulta en SQLite."""
        import sqlite3
        import aiosqlite
        
        database_path = db_config.get("database", "")
        if not database_path:
            raise ValueError("Ruta de base de datos SQLite no especificada")
        
        async with aiosqlite.connect(database_path) as db:
            # Configurar para obtener resultados como diccionarios
            db.row_factory = aiosqlite.Row
            
            # Ejecutar la consulta
            cursor = await db.execute(sql_query)
            rows = await cursor.fetchall()
            
            # Convertir a lista de diccionarios
            result = []
            for row in rows:
                # Convertir Row a dict
                row_dict = {key: row[key] for key in row.keys()}
                # Serializar valores especiales
                for key, value in row_dict.items():
                    if hasattr(value, 'isoformat') and callable(getattr(value, 'isoformat')):
                        row_dict[key] = value.isoformat()
                    elif isinstance(value, (bytes, bytearray)):
                        row_dict[key] = value.hex()
                
                result.append(row_dict)
            
            return result

    @staticmethod
    async def execute_mysql_query(sql_query: str, db_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Ejecuta una consulta en MySQL."""
        import aiomysql
        
        # Extraer parámetros de conexión
        host = db_config.get("host", "localhost")
        port = db_config.get("port", 3306)
        user = db_config.get("user", "")
        password = db_config.get("password", "")
        database = db_config.get("database", "")
        
        # Crear pool de conexiones
        pool = await aiomysql.create_pool(
            host=host,
            port=port,
            user=user,
            password=password,
            db=database,
            autocommit=True
        )
        
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                # Ejecutar la consulta
                await cursor.execute(sql_query)
                rows = await cursor.fetchall()
                
                # Convertir resultados
                result = []
                for row in rows:
                    # Convertir valores no serializables (como bytes, datetime, etc.)
                    row_dict = {}
                    for key, value in row.items():
                        if isinstance(value, (bytes, bytearray)):
                            row_dict[key] = value.decode('utf-8', errors='replace')
                        elif hasattr(value, 'isoformat'):  # datetime, date, time
                            row_dict[key] = value.isoformat()
                        else:
                            row_dict[key] = value
                    
                    result.append(row_dict)
                
                return result
        
        # Cerrar el pool
        pool.close()
        await pool.wait_closed()

    @staticmethod
    async def execute_postgresql_query(sql_query: str, db_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Ejecuta una consulta en PostgreSQL."""
        import asyncpg
        
        # Extraer parámetros de conexión
        host = db_config.get("host", "localhost")
        port = db_config.get("port", 5432)
        user = db_config.get("user", "")
        password = db_config.get("password", "")
        database = db_config.get("database", "")
        
        # Conectar a la base de datos
        conn = await asyncpg.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database
        )
        
        try:
            # Ejecutar la consulta
            rows = await conn.fetch(sql_query)
            
            # Convertir Record a dict
            result = []
            for row in rows:
                # Convertir asyncpg.Record a dict
                row_dict = dict(row)
                
                # Convertir valores no serializables
                for key, value in row_dict.items():
                    if hasattr(value, 'isoformat'):  # datetime, date, time
                        row_dict[key] = value.isoformat()
                    elif isinstance(value, asyncpg.BitString):
                        row_dict[key] = str(value)
                    elif isinstance(value, (bytes, bytearray)):
                        row_dict[key] = value.hex()
                
                result.append(row_dict)
            
            return result
            
        finally:
            # Cerrar la conexión
            await conn.close()

    @staticmethod
    async def execute_mongodb_query(mongo_query, db_config):
        """
        Ejecuta una consulta MongoDB y devuelve los resultados.
        
        Args:
            mongo_query (MongoDBQuery): La consulta MongoDB a ejecutar
            db_config (dict): Configuración de la base de datos
            
        Returns:
            tuple: (resultado, explicación)
        """
        import motor.motor_asyncio
        from bson.json_util import dumps, loads
        
        try:
            # Obtener parámetros de conexión
            host = db_config.get("host", "localhost")
            port = db_config.get("port", 27017)
            user = db_config.get("user", "")
            password = db_config.get("password", "")
            database = db_config.get("database", "")
            
            # Construir la URL de conexión
            if user and password:
                connection_string = f"mongodb://{user}:{password}@{host}:{port}/{database}"
            else:
                connection_string = f"mongodb://{host}:{port}/{database}"
            
            # Conectar a MongoDB
            client = motor.motor_asyncio.AsyncIOMotorClient(connection_string)
            db = client[database]
            collection = db[mongo_query.collection]
            
            # Determinar la operación a realizar
            if mongo_query.operation == "find":
                # Ejecutar consulta de búsqueda
                cursor = collection.find(
                    mongo_query.filter or {},
                    mongo_query.projection or None
                )
                
                # Aplicar opciones si existen
                if mongo_query.sort:
                    cursor = cursor.sort(mongo_query.sort)
                if mongo_query.limit:
                    cursor = cursor.limit(mongo_query.limit)
                if mongo_query.skip:
                    cursor = cursor.skip(mongo_query.skip)
                
                # Obtener resultados
                result_list = await cursor.to_list(length=100)  # Limitar a 100 documentos por defecto
                
                # Convertir ObjectId a string para serialización JSON
                result_serializable = loads(dumps(result_list))
                
                # Generar explicación
                num_docs = len(result_serializable)
                if num_docs == 0:
                    explanation = "La consulta no devolvió ningún documento."
                else:
                    explanation = f"La consulta devolvió {num_docs} {'documento' if num_docs == 1 else 'documentos'}."
                    
                    # Añadir información adicional
                    if mongo_query.projection:
                        fields = ", ".join(mongo_query.projection.keys())
                        explanation += f" Campos seleccionados: {fields}."
                    if mongo_query.sort:
                        explanation += " Los resultados están ordenados según los criterios especificados."
                    if mongo_query.limit:
                        explanation += f" Se limitó la búsqueda a {mongo_query.limit} documentos."
                
                return result_serializable, explanation
            elif mongo_query.operation == "aggregate":
                # Ejecutar agregación
                pipeline = mongo_query.pipeline or []
                
                # Aplicar opciones de proyección y ordenamiento si existen
                if mongo_query.projection:
                    pipeline.append({"$project": mongo_query.projection})
                if mongo_query.sort:
                    pipeline.append({"$sort": mongo_query.sort})
                if mongo_query.limit:
                    pipeline.append({"$limit": mongo_query.limit})
                if mongo_query.skip:
                    pipeline.append({"$skip": mongo_query.skip})
                
                cursor = collection.aggregate(pipeline)
                
                # Obtener resultados
                result_list = await cursor.to_list(length=100)
                result_serializable = loads(dumps(result_list))
                
                num_docs = len(result_serializable)
                if num_docs == 0:
                    explanation = "La agregación no devolvió ningún documento."
                else:
                    explanation = f"La agregación devolvió {num_docs} {'documento' if num_docs == 1 else 'documentos'}."
                    if mongo_query.projection:
                        fields = ", ".join(mongo_query.projection.keys())
                        explanation += f" Campos seleccionados: {fields}."
                    if mongo_query.sort:
                        explanation += " Los resultados están ordenados según los criterios especificados."
                    if mongo_query.limit:
                        explanation += f" Se limitó la búsqueda a {mongo_query.limit} documentos."
                
                return result_serializable, explanation
            elif mongo_query.operation == "findOne":
                # Ejecutar consulta de búsqueda de un documento
                document = await collection.find_one(
                    mongo_query.filter or {},
                    mongo_query.projection or None
                )
                
                # Convertir ObjectId a string para serialización JSON
                result_serializable = loads(dumps(document))
                
                # Generar explicación
                if result_serializable:
                    explanation = "Se encontró el documento solicitado."
                    if mongo_query.projection:
                        fields = ", ".join(mongo_query.projection.keys())
                        explanation += f" Campos seleccionados: {fields}."
                else:
                    explanation = "No se encontró ningún documento que coincida con los criterios de búsqueda."
                
                return result_serializable, explanation
                
            elif mongo_query.operation == "insertOne":
                # Ejecutar inserción de un documento
                result = await collection.insert_one(mongo_query.document)
                
                # Generar explicación
                explanation = f"Se ha insertado un nuevo documento con ID: {str(result.inserted_id)}."
                
                return {"insertedId": str(result.inserted_id)}, explanation
                
            elif mongo_query.operation == "updateOne":
                # Ejecutar actualización de un documento
                result = await collection.update_one(
                    mongo_query.filter or {},
                    mongo_query.update
                )
                
                # Generar explicación
                matched = result.matched_count
                modified = result.modified_count
                
                if matched == 0:
                    explanation = "No se encontró ningún documento que coincida con los criterios de búsqueda para actualizar."
                elif modified == 0:
                    explanation = "Se encontró un documento pero no se realizaron cambios (los valores son idénticos a los existentes)."
                else:
                    explanation = "Se actualizó correctamente el documento."
                
                return {
                    "matchedCount": matched,
                    "modifiedCount": modified
                }, explanation
                
            elif mongo_query.operation == "deleteOne":
                # Ejecutar eliminación de un documento
                result = await collection.delete_one(mongo_query.filter or {})
                
                # Generar explicación
                deleted = result.deleted_count
                
                if deleted == 0:
                    explanation = "No se encontró ningún documento que coincida con los criterios para eliminar."
                else:
                    explanation = "Se eliminó correctamente el documento."
                
                return {"deletedCount": deleted}, explanation
                
            else:
                # Operación no soportada
                raise ValueError(f"Operación MongoDB no soportada: {mongo_query.operation}")
        
        except Exception as e:
            # Proporcionar una explicación del error
            error_message = str(e)
            explanation = f"Error al ejecutar la consulta MongoDB: {error_message}"
            
            # Sugerir soluciones según el tipo de error
            if "Authentication failed" in error_message:
                explanation += " Las credenciales de acceso son incorrectas."
            elif "not authorized" in error_message:
                explanation += " El usuario no tiene permisos suficientes para esta operación."
            elif "No such collection" in error_message:
                explanation += " La colección especificada no existe en la base de datos."
            
            raise ValueError(explanation)

    @staticmethod
    async def generate_mongodb_query(
        query: str,
        db_info: Dict[str, Any],
        collection_name: Optional[str] = None,
        db_connection = None
    ) -> MongoDBQuery:
        """
        Genera una consulta MongoDB a partir de una consulta en lenguaje natural.
        Adaptado para funcionar con bases de datos de clientes sin conocimiento previo de colecciones.
        
        Args:
            query: Consulta en lenguaje natural
            db_info: Información del esquema de la base de datos
            collection_name: Nombre de la colección a consultar (opcional)
            db_connection: Conexión a la base de datos para exploración adicional (opcional)
            
        Returns:
            Objeto MongoDBQuery con la consulta generada
        """
        # Preparar el contexto con la información de la base de datos
        db_context = json.dumps(db_info, indent=2, default=str)
        
        # Obtener colecciones disponibles
        available_collections = []
        if "tables" in db_info:
            available_collections = list(db_info.get("tables", {}).keys())
        elif "collections" in db_info:
            available_collections = list(db_info.get("collections", {}).keys())
        
        # Si se proporcionó una colección específica, usarla
        if collection_name:
            selected_collection = collection_name
        else:
            # Intentar determinar la mejor colección basada en la consulta
            selected_collection = Utils.determine_best_collection(query, available_collections, db_connection)
            logger.info(f"Colección seleccionada automáticamente: {selected_collection}")
        
        # Crear system prompt específico para la colección seleccionada
        collection_info = ""
        if selected_collection and selected_collection in db_info.get("tables", {}):
            # Incluir información específica sobre la colección seleccionada
            try:
                collection_data = db_info["tables"][selected_collection]
                collection_info = f"\nINFORMACIÓN DE LA COLECCIÓN SELECCIONADA ({selected_collection}):\n"
                
                # Incluir campos/estructura
                if "columns" in collection_data or "fields" in collection_data:
                    fields = collection_data.get("columns", collection_data.get("fields", []))
                    collection_info += f"Campos: {json.dumps([f.get('name') for f in fields], default=str)}\n"
                
                # Incluir muestra de documentos si está disponible
                if "sample_data" in collection_data and collection_data["sample_data"]:
                    collection_info += f"Muestra de documentos: {json.dumps(collection_data['sample_data'][:2], default=str)}\n"
                    
            except Exception as e:
                logger.warning(f"Error al preparar información de colección: {str(e)}")
        
        # Crear system prompt para consulta MongoDB
        system_prompt = f"""
        Eres un asistente especializado en traducir consultas en lenguaje natural a operaciones MongoDB.
        
        ESTRUCTURA DE LA BASE DE DATOS:
        {db_context}
        
        {collection_info}
        
        Tu tarea es:
        1. Analizar cuidadosamente la consulta del usuario
        2. Utilizar la colección '{selected_collection}' para esta consulta
        3. Construir la consulta apropiada (find o aggregate)
        4. Devolver la consulta como un objeto JSON con el siguiente formato:
        
        Para búsquedas simples:
        {{
        "collection": "{selected_collection}",
        "operation": "find",
        "query": {{ /* filtros */ }},
        "projection": {{ /* campos a incluir/excluir */ }},
        "sort": {{ /* ordenamiento */ }},
        "limit": 10
        }}
        
        Para agregaciones:
        {{
        "collection": "{selected_collection}",
        "operation": "aggregate",
        "pipeline": [
            {{ /* etapa 1 */ }},
            {{ /* etapa 2 */ }}
        ]
        }}
        
        IMPORTANTE: Utiliza exactamente la colección "{selected_collection}" en tu respuesta.
        
        Responde ÚNICAMENTE con el objeto JSON, sin ningún otro texto.
        """
        
        try:
            # Inicializar cliente de OpenAI
            client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)
            
            # Enviar solicitud a OpenAI
            response = await client.chat.completions.create(
                model=settings.OPENAI.OPENAI_MODEL,
                max_tokens=settings.OPENAI.MAX_TOKENS,
                temperature=0.2,  # Temperatura baja para respuestas más deterministas
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ]
            )
            
            # Extraer y procesar respuesta JSON
            ai_response = response.choices[0].message.content
            json_text = AIQuery.clean_json_response(ai_response)
            
            # Parsear JSON
            query_data = json.loads(json_text)
            
            # Verificar que se está usando la colección seleccionada
            if query_data.get("collection") != selected_collection:
                logger.warning(f"La IA seleccionó una colección diferente: {query_data.get('collection')}. Forzando uso de {selected_collection}")
                query_data["collection"] = selected_collection
            
            # Crear objeto MongoDBQuery
            mongo_query = MongoDBQuery(
                collection=query_data["collection"],
                operation=query_data.get("operation", "find"),
                query=query_data.get("query", {}),
                pipeline=query_data.get("pipeline", []),
                projection=query_data.get("projection"),
                sort=query_data.get("sort"),
                limit=query_data.get("limit", 10),
                skip=query_data.get("skip", 0)
            )
            
            # Registrar la consulta generada
            logger.info(f"Consulta MongoDB generada para colección {mongo_query.collection}: {json.dumps(mongo_query.dict() if hasattr(mongo_query, 'dict') else vars(mongo_query), default=str)}")
            
            return mongo_query
            
        except Exception as e:
            logger.error(f"Error al generar consulta MongoDB: {str(e)}")
            
            # Realizar diagnóstico de la conexión si está disponible
            if db_connection:
                try:
                    debug_result = await Diagnostic.debug_mongodb_connection(db_connection, collection_name)
                    logger.info(f"Diagnóstico MongoDB: {json.dumps(debug_result, default=str)}")
                    
                    # Usar información del diagnóstico para una mejor selección de fallback
                    if debug_result.get("available_collections"):
                        available_collections = debug_result["available_collections"]
                except Exception as debug_error:
                    logger.error(f"Error al realizar diagnóstico: {str(debug_error)}")
            
            
            # En caso de error, generar una consulta segura y simple con la colección seleccionada
            return MongoDBQuery(
                collection=selected_collection or (available_collections[0] if available_collections else "users"),
                operation="find",
                query={},
                limit=10,
                skip=0
            )
            
    @staticmethod
    def clean_json_response(response_text: str) -> str:
        """Limpia y extrae el JSON de la respuesta del modelo."""
        json_text = response_text.strip()
        
        # Eliminar bloques de código markdown
        if json_text.startswith('```') and json_text.endswith('```'):
            json_text = json_text[3:-3].strip()
        elif '```' in json_text:
            # Extraer contenido entre las primeras comillas de código triple
            match = re.search(r'```(?:json)?(.*?)```', json_text, re.DOTALL)
            if match:
                json_text = match.group(1).strip()
                
        # Eliminar prefijo de lenguaje
        if json_text.startswith('json'):
            json_text = json_text[4:].strip()
        
        return json_text
    
    @staticmethod
    async def generate_result_explanation(
        query: str,
        mongo_query: Any,
        result: QueryResult
    ) -> str:
        """
        Genera una explicación en lenguaje natural de los resultados de una consulta MongoDB.
        
        Args:
            query: Consulta original en lenguaje natural
            mongo_query: Consulta MongoDB generada (objeto o diccionario)
            result: Resultado de la consulta
            
        Returns:
            Explicación en lenguaje natural
        """
        # Convertir mongo_query a diccionario si es un objeto
        if hasattr(mongo_query, "model_dump"):
            mongo_query_dict = mongo_query.model_dump()
        elif hasattr(mongo_query, "dict"):
            mongo_query_dict = mongo_query.dict()
        elif not isinstance(mongo_query, dict):
            # Intentar extraer atributos comunes
            mongo_query_dict = {
                "collection": getattr(mongo_query, "collection", ""),
                "operation": getattr(mongo_query, "operation", "find"),
                "filter": getattr(mongo_query, "filter", {}),
                "projection": getattr(mongo_query, "projection", {}),
                "sort": getattr(mongo_query, "sort", {}),
                "limit": getattr(mongo_query, "limit", 0),
                "skip": getattr(mongo_query, "skip", 0),
                "pipeline": getattr(mongo_query, "pipeline", [])
            }
        else:
            mongo_query_dict = mongo_query
        
        # Limitar resultado para el prompt
        result_sample = result.data[:5] if isinstance(result.data, list) else [result.data]
        
        # Extraer información relevante de la consulta
        collection = mongo_query_dict.get("collection", "")
        operation = mongo_query_dict.get("operation", "find")
        filter_criteria = mongo_query_dict.get("filter", {}) or mongo_query_dict.get("query", {})
        fields = list(mongo_query_dict.get("projection", {}).keys()) if mongo_query_dict.get("projection") else []
        pipeline = mongo_query_dict.get("pipeline", [])
        
        # Analizar la estructura de los resultados
        fields_in_results = []
        if result.count > 0 and isinstance(result_sample[0], dict):
            fields_in_results = list(result_sample[0].keys())
        
        # Determinar el tipo de consulta
        query_type = operation
        is_aggregation = operation == "aggregate" or (isinstance(pipeline, list) and len(pipeline) > 0)
        has_filter = bool(filter_criteria)
        has_projection = bool(fields)
        
        # Crear resumen para el contexto
        summary = {
            "query_type": query_type,
            "collection": collection,
            "total_results": result.count,
            "execution_time_ms": result.query_time_ms,
            "is_aggregation": is_aggregation,
            "has_filter": has_filter,
            "has_projection": has_projection,
            "fields_selected": fields,
            "fields_in_results": fields_in_results
        }
        
        # Añadir información específica según el tipo de operación
        if operation == "find" or operation == "findOne":
            summary["filter_criteria"] = filter_criteria
            summary["sort"] = mongo_query_dict.get("sort", {})
            summary["limit"] = mongo_query_dict.get("limit", 0)
            summary["skip"] = mongo_query_dict.get("skip", 0)
        elif operation == "aggregate":
            summary["pipeline_stages"] = [list(stage.keys())[0] if isinstance(stage, dict) else str(stage) for stage in pipeline]
        elif operation in ["insertOne", "updateOne", "deleteOne"]:
            summary["affected_documents"] = result.count
        
        # Preparar contexto para OpenAI
        context = {
            "original_query": query,
            "mongodb_query": mongo_query_dict,
            "result_count": result.count,
            "result_sample": result_sample,
            "query_time_ms": result.query_time_ms,
            "summary": summary
        }
        
        try:
            query_language = detect(query)
        except:
            query_language = "es"  # Valor predeterminado si no se puede detectar

        query_language = detect(query)
        context["detected_language"] = query_language
        
        context_json = json.dumps(context, indent=2, default=str)
                
        # Generar prompt para OpenAI
        system_prompt = f"""
        Eres un asistente especializado en explicar resultados de consultas MongoDB.
        Tu objetivo es proporcionar explicaciones claras y naturales de los resultados obtenidos,
        evitando tecnicismos innecesarios pero manteniendo la precisión de la información.
        
        IMPORTANTE: La consulta original está en {context["detected_language"]}. 
        TU RESPUESTA DEBE ESTAR EXCLUSIVAMENTE EN EL MISMO IDIOMA QUE LA CONSULTA ORIGINAL.
        
        Directrices específicas:
        1. Comienza con un resumen conciso de los resultados (cuántos documentos se encontraron)
        2. Describe el tipo de consulta realizada (búsqueda, filtrado, agregación, etc.) sin usar terminología técnica de MongoDB
        3. Comenta los hallazgos más importantes o patrones identificados en los datos
        4. Destaca información relevante sobre los documentos mostrados
        5. La explicación debe ser comprensible para personas sin conocimientos técnicos
        6. Usa un lenguaje accesible pero preciso
        7. NO menciones la sintaxis MongoDB ni términos técnicos como "$match", "$group", etc.
        8. Prioriza la relevancia para el usuario sobre los detalles técnicos
        
        Formato deseado:
        - Empezar con un resumen directo y claro
        - Continuar con 1-3 observaciones importantes sobre los datos
        - Si es relevante, terminar con una conclusión breve
        - Mantener la explicación en 100-150 palabras como máximo
        - Devuelve la explicación en el mismo lenguaje que la pregunta "{query}"
        """
        
        try:
            # Inicializar cliente OpenAI
            client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)
            
            # Enviar solicitud a OpenAI
            response = await client.chat.completions.create(
                model=settings.OPENAI.OPENAI_MODEL,
                max_tokens=settings.OPENAI.MAX_TOKENS,
                temperature=0.7,  # Un poco más de creatividad para la explicación
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Explica los resultados de esta consulta MOngoDB en {context['detected_language']}:\n{context_json}"}
                ]
            )
            
            # Extraer explicación
            explanation = response.choices[0].message.content
            
            return explanation
        
        except Exception as e:
            # En caso de error, generar explicación básica
            logger.error(f"Error al generar explicación MongoDB: {str(e)}")
            
            # Crear una explicación sencilla basada en los datos disponibles
            if result.count == 0:
                return f"No se encontraron documentos en la colección {collection} que coincidan con tu consulta."
            else:
                explanation = f"Se encontraron {result.count} documentos en la colección {collection}."
                
                # Añadir información sobre el tipo de operación
                if operation == "findOne":
                    explanation = f"Se encontró el documento solicitado en la colección {collection}."
                elif operation == "aggregate":
                    explanation = f"La agregación en la colección {collection} devolvió {result.count} resultados."
                elif operation == "insertOne":
                    explanation = f"Se ha insertado correctamente un nuevo documento en la colección {collection}."
                elif operation == "updateOne":
                    explanation = f"Se ha actualizado correctamente un documento en la colección {collection}."
                elif operation == "deleteOne":
                    explanation = f"Se ha eliminado correctamente un documento de la colección {collection}."
                
                # Añadir información sobre campos si están disponibles
                if fields_in_results:
                    sample_fields = fields_in_results[:5]  # Limitar a 5 campos para no saturar la explicación
                    explanation += f" Los campos presentes en los resultados incluyen: {', '.join(sample_fields)}"
                    if len(fields_in_results) > 5:
                        explanation += f" y {len(fields_in_results) - 5} más."
                    else:
                        explanation += "."
                
                # Añadir información sobre el tiempo de ejecución
                if result.query_time_ms > 0:
                    explanation += f" La consulta se ejecutó en {result.query_time_ms:.1f} ms."
                    
                return explanation
    
    @staticmethod
    async def generate_sql_result_explanation(
        query: str,
        sql_query: str,
        result: QueryResult
    ) -> str:
        """
        Genera una explicación en lenguaje natural de los resultados de una consulta SQL.
        
        Args:
            query: Consulta original en lenguaje natural
            sql_query: Consulta SQL ejecutada
            result: Resultado de la consulta
            
        Returns:
            Explicación en lenguaje natural
        """
        # Comprobar si hay resultados
        if result.count == 0:
            return "No se encontraron registros que coincidan con tu consulta."
        
        # Limitar resultado para el prompt
        result_sample = result.data[:5]
        
        # Analizar la estructura de los resultados para una mejor explicación
        column_names = []
        if result.count > 0 and isinstance(result.data[0], dict):
            column_names = list(result.data[0].keys())
        
        # Analizar la consulta SQL para extraer información relevante
        sql_lower = sql_query.lower()
        selected_tables = []
        join_tables = []
        
        # Detectar tablas en la consulta
        from_pattern = r'from\s+([a-zA-Z0-9_\.]+)'
        join_pattern = r'join\s+([a-zA-Z0-9_\.]+)'
        
        from_matches = re.findall(from_pattern, sql_lower)
        if from_matches:
            selected_tables.extend(from_matches)
        
        join_matches = re.findall(join_pattern, sql_lower)
        if join_matches:
            join_tables.extend(join_matches)
        
        # Detectar tipo de consulta SQL
        query_type = "consulta"
        if "count" in sql_lower and "group by" not in sql_lower:
            query_type = "conteo"
        elif "group by" in sql_lower:
            query_type = "agrupación"
        elif "join" in sql_lower:
            query_type = "relación"
        elif "order by" in sql_lower:
            query_type = "ordenamiento"
        elif "where" in sql_lower:
            query_type = "filtrado"
        
        # Crear resumen básico para incluir en el contexto
        summary = {
            "query_type": query_type,
            "total_results": result.count,
            "execution_time_ms": result.query_time_ms,
            "tables_involved": selected_tables + join_tables,
            "columns_returned": column_names,
            "has_aggregation": "group by" in sql_lower or "sum(" in sql_lower or "avg(" in sql_lower or "count(" in sql_lower,
            "has_conditions": "where" in sql_lower,
            "has_ordering": "order by" in sql_lower,
            "has_joins": len(join_tables) > 0
        }
        
        # Preparar contexto para OpenAI
        context = {
            "original_query": query,
            "sql_query": sql_query,
            "result_count": result.count,
            "result_sample": result_sample,
            "query_time_ms": result.query_time_ms,
            "engine": result.metadata.get("engine", ""),
            "summary": summary,
            "column_names": column_names
        }

        try:
            query_language = detect(query)
        except:
            query_language = "es"  # Valor predeterminado si no se puede detectar

        query_language = detect(query)
        print("Lenguaje identificado: ", query_language)
        context["detected_language"] = query_language
        
        context_json = json.dumps(context, indent=2, default=str)
        
        LANGUAGE_MAPPING = {
            "en": "English",
            "es": "Spanish",
            "fr": "French",
            "de": "German",
            "it": "Italian",
            "pt": "Portuguese"
        }

        language_name = LANGUAGE_MAPPING.get(query_language, "English")
        
        # Generar prompt para OpenAI
        system_prompt = f"""
            You are a specialized assistant for explaining SQL query results.
            YOUR ENTIRE RESPONSE MUST BE IN {language_name} ONLY.

            Current detected language: {query_language} ({language_name})

            CRITICAL: The user's query was detected to be in {language_name}. 
            YOU MUST RESPOND ONLY IN {language_name}.

            Guidelines:
            1. Begin with a concise summary of the results (how many records were found)
            2. Describe the type of query performed (search, filtering, relation, count, etc.) without using SQL terminology
            3. Comment on the most important findings or patterns identified
            4. Highlight relevant information about the displayed data
            5. When appropriate, mention specific values (maximums, minimums, averages, etc.)
            6. The explanation should be understandable for people without technical knowledge
            7. Use accessible but precise language
            8. DO NOT mention SQL syntax or technical terms like JOIN, GROUP BY, etc.
            9. Prioritize relevance to the user over technical details

            Desired format:
            - Start with a direct and clear summary
            - Continue with 1-3 important observations about the data
            - If relevant, end with a brief conclusion
            - Keep the explanation to 100-150 words maximum
            - YOUR RESPONSE MUST BE ENTIRELY IN {language_name}
        """
        
        try:
            # Inicializar cliente OpenAI
            client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)
            
            # Enviar solicitud a OpenAI
            response = await client.chat.completions.create(
                model=settings.OPENAI.OPENAI_MODEL,
                max_tokens=settings.OPENAI.MAX_TOKENS,
                temperature=0.7,  # Un poco más de creatividad para la explicación
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Explica los resultados de esta consulta SQL en {context['detected_language']}:\n{context_json}"}
                ]
            )
            
            # Extraer explicación
            explanation = response.choices[0].message.content
            
            return explanation
        
        except Exception as e:
            # En caso de error, generar explicación básica
            logger.error(f"Error al generar explicación SQL: {str(e)}")
            
            # Crear una explicación sencilla basada en los datos disponibles
            if result.count == 0:
                return "No se encontraron registros que coincidan con tu consulta."
            else:
                explanation = f"Se encontraron {result.count} registros en la base de datos."
                
                # Añadir información básica sobre las columnas si están disponibles
                if column_names:
                    explanation += f" Los campos presentes en los resultados son: {', '.join(column_names)}."
                
                # Añadir información sobre las tablas involucradas
                if selected_tables or join_tables:
                    tables = list(set(selected_tables + join_tables))
                    explanation += f" La consulta se realizó sobre {', '.join(tables)}."
                
                # Añadir información sobre el tiempo de ejecución
                if result.query_time_ms > 0:
                    explanation += f" La consulta se ejecutó en {result.query_time_ms:.1f} ms."
                    
                return explanation
         
    @staticmethod
    async def process_natural_language_query(
        query: str,
        db_schema: Dict[str, Any],
        collection_name: Optional[str] = None,
        config_id: Optional[str] = None,
        db_connection = None
    ) -> Dict[str, Any]:
        """
        Procesa una consulta en lenguaje natural y genera una explicación.
        
        Args:
            query: Consulta en lenguaje natural
            db_schema: Esquema de la base de datos
            collection_name: Nombre de la colección/tabla a consultar (opcional)
            config_id: ID de configuración (opcional)
            
        Returns:
            Diccionario con la consulta generada, resultados y explicación
        """
        try:
            # Determinar el tipo de base de datos
            db_type = db_schema.get("type", "").lower()
            
            if db_type == "sql":
                # Para bases de datos SQL
                engine = db_schema.get("engine", "sqlite").lower()
                
                # Generar consulta SQL
                sql_query = await AIQuery.generate_sql_query(query, db_schema, engine)
                
                # Ejecutar consulta SQL
                result_data, execution_time = await AIQuery.execute_sql_query(sql_query, db_schema)
                
                # Crear objeto QueryResult
                result = QueryResult(
                    data=result_data,
                    count=len(result_data),
                    query_time_ms=int(execution_time * 1000),
                    metadata={
                        "engine": engine,
                        "database": db_schema.get("database", ""),
                        "config_id": config_id
                    }
                )
                
                # Generar explicación
                explanation = await AIQuery.generate_sql_result_explanation(query, sql_query, result)
                
                # Devolver respuesta completa
                return {
                    "explanation": explanation,
                    "query": {
                        "sql": sql_query,
                        "engine": engine
                    },
                    "result": result
                }
                
            elif db_type in ["nosql", "mongodb"]:
                # Para MongoDB
                
                if db_connection:
                    debug_info = await Diagnostic.debug_mongodb_connection(db_connection)
                    logger.info(f"Diagnóstico MongoDB previo a consulta: {json.dumps(debug_info, default=str)}")
                    
                    if debug_info.get("connection_status") != "connected":
                        return {
                            "explanation": "No se pudo conectar a la base de datos MongoDB",
                            "query": None,
                            "result": None,
                            "error": True,
                            "debug_info": debug_info
                        }
                
                # Generar consulta MongoDB
                mongo_query = await AIQuery.generate_mongodb_query(
                    query, db_schema, collection_name, db_connection
                )
                
                # Ejecutar la consulta MongoDB
                # Implementación de ejecución de consulta MongoDB...
                # (Suponiendo que existe un método para ejecutar la consulta)
                
                # Placeholder para resultados
                result = QueryResult(
                    data=[],  # Aquí irían los datos reales
                    count=0,
                    query_time_ms=0,
                    metadata={
                        "config_id": config_id
                    }
                )
                
                # Preparar el objeto de consulta para devolverlo
                # Convertir MongoDBQuery a dict de forma segura
                if hasattr(mongo_query, "model_dump"):
                    query_dict = mongo_query.model_dump()
                elif hasattr(mongo_query, "dict"):
                    query_dict = mongo_query.dict()
                else:
                    # Fallback: crear diccionario manualmente
                    query_dict = {
                        "collection": getattr(mongo_query, "collection", ""),
                        "operation": getattr(mongo_query, "operation", "find"),
                        "query": getattr(mongo_query, "query", {}),
                        "pipeline": getattr(mongo_query, "pipeline", []),
                        "projection": getattr(mongo_query, "projection", {}),
                        "sort": getattr(mongo_query, "sort", {}),
                        "limit": getattr(mongo_query, "limit", 10),
                        "skip": getattr(mongo_query, "skip", 0)
                    }
                
                # Generar explicación
                explanation = await AIQuery.generate_result_explanation(query, mongo_query, result)
                
                # Devolver respuesta completa
                return {
                    "explanation": explanation,
                    "query": query_dict,
                    "result": result
                }
            
            else:
                # Tipo de base de datos no soportado
                return {
                    "explanation": f"Tipo de base de datos no soportado: {db_type}",
                    "query": None,
                    "result": None,
                    "error": True
                }
                
        except Exception as e:
            logger.error(f"Error al procesar consulta en lenguaje natural: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            
            return {
                "explanation": f"Error al procesar la consulta: {str(e)}",
                "query": None,
                "result": None,
                "error": True
            }
            
    @staticmethod
    async def process_collections_query(
        question: str,
        db_schema: Dict[str, Any]
    ) -> List[str]:
        """
        Procesa una consulta en lenguaje natural y genera una explicación.
        
        Args:
            question: Consulta en lenguaje natural
            db_schema: Esquema de la base de datos
            
        Returns:
            Diccionario con la consulta generada, resultados y explicación sobre los datos
            Determina qué colección se debe consultar	
        """

        # Crear system prompt para consulta MongoDB
        system_prompt = f"""
        Eres un asistente especializado en identificar las colecciones necesarias y relativas a la consulta del usuario.
        
        ESTRUCTURA DE LA BASE DE DATOS:
        {db_schema}
    
        Tu tarea es:
        1. Analizar cuidadosamente la consulta del usuario
        2. Entender el esquema de la base de datos
        3. Determinar cual o cuales son las colecciones que se deben consultar
        
        Responde ÚNICAMENTE con las colecciones que se deben consultar en formato de lista, separadas por comas.
        """
        
        try:
            # Inicializar cliente de OpenAI
            client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)

            # Enviar solicitud a OpenAI
            response = await client.chat.completions.create(
                model=settings.OPENAI.OPENAI_MODEL,
                max_tokens=settings.OPENAI.MAX_TOKENS,
                temperature=0.2,  # Temperatura baja para respuestas más deterministas
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question}
                ]
            )
            
            # Extraer y procesar respuesta JSON
            ai_response = response.choices[0].message.content
            json_text = AIQuery.clean_json_response(ai_response)
            
            # Parsear JSON
            query_data = json.loads(json_text)
            
            return query_data
            
        except Exception as e:
            logger.error(f"Error al generar consulta MongoDB: {str(e)}")
            
            
        
