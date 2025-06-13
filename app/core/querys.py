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
        Initializes an AIQuery object to process natural language queries.

        Args:
            query: Natural language query
            collection_name: Name of the collection/table to query (optional)
            limit: Result limit (default 50)
            config_id: Configuration ID (optional)
            db_schema: Database schema (optional)
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
        Generates an SQL query from a natural language query and database information.

        Args:
            query: Natural language query
            db_info: Database schema information
            engine: Database engine (sqlite, mysql, postgresql)

        Returns:
            Generated SQL query
        """
        # Prepare the context with the information from the database
        db_context = json.dumps(db_info, indent=2, default=str)  # Use default=str to handle special types
        
        # Limit the context if it is too large
        if len(db_context) > 10000:
            # Extract only the first tables
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
        
        # Create specific system prompt for SQL
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
        print("Prompt sent to AI: ", system_prompt)
        
        try:
            # Initialize OpenAI client
            client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)
            
            # Submit application to OpenAI
            response = await client.chat.completions.create(
                model=settings.OPENAI.OPENAI_MODEL,
                max_tokens=settings.OPENAI.MAX_TOKENS,
                temperature=0.2,  # Low temperature for more deterministic responses
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ]
            )
            
            # Extract the generated SQL query
            sql_query = response.choices[0].message.content.strip()
            
            # Clean the query (delete comments, etc.)
            sql_query = AIQuery.clean_sql_query(sql_query)
            
            return sql_query
            
        except Exception as e:
            logger.error(f"Error al generar consulta SQL: {str(e)}")
            # In case of error, generate a safe and simple query
            safe_table = next(iter(db_info.get("tables", {}).keys()), "users")
            return f"SELECT * FROM {safe_table} LIMIT 10"

    
    @staticmethod
    def clean_sql_query(sql_query: str) -> str:
        """
        Cleans up an SQL query by removing comments, backticks, and other unnecessary elements.

        Args:
            sql_query: SQL query to clean

        Returns:
            Cleaned SQL query
        """
        # Remove markdown code blocks
        if sql_query.startswith('```') and sql_query.endswith('```'):
            sql_query = sql_query[3:-3].strip()
        elif '```' in sql_query:
            # Extract content between the first triple quotes of code
            match = re.search(r'```(?:sql)?(.*?)```', sql_query, re.DOTALL)
            if match:
                sql_query = match.group(1).strip()
        
        # Remove language specifiers at the beginning
        if sql_query.lower().startswith('sql'):
            sql_query = sql_query[3:].strip()
        
        # Delete comments from a line
        sql_query = re.sub(r'--.*$', '', sql_query, flags=re.MULTILINE)
        
        # Delete multi-line comments
        sql_query = re.sub(r'/\*.*?\*/', '', sql_query, flags=re.DOTALL)
        
        # Remove empty lines and extra spaces
        sql_query = '\n'.join(line.strip() for line in sql_query.split('\n') if line.strip())
        
        return sql_query

    @staticmethod
    async def execute_sql_query(
        sql_query: str,
        db_config: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], float]:
        """
        Executes an SQL query on the configured database.

        Args:
            sql_query: SQL query to execute
            db_config: Database configuration

        Returns:
            Tuple containing (result, execution_time)
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
                raise ValueError(f"SQL Engine Not Supported: {engine}")
            
            # Calculate execution time
            execution_time = time.time() - start_time
            
            # Limit results if there are too many
            if len(result_data) > 100:
                result_data = result_data[:100]
            
            return result_data, execution_time
            
        except Exception as e:
            # Log error
            logger.error(f"Error executing SQL query ({engine}): {str(e)}")
            execution_time = time.time() - start_time
            raise

    @staticmethod
    async def execute_sqlite_query(sql_query: str, db_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Executes a query in SQLite."""
        import sqlite3
        import aiosqlite
        
        database_path = db_config.get("database", "")
        if not database_path:
            raise ValueError("SQLite database path not specified")
        
        async with aiosqlite.connect(database_path) as db:
            # Configure to get results as dictionaries
            db.row_factory = aiosqlite.Row
            
            # Run the query
            cursor = await db.execute(sql_query)
            rows = await cursor.fetchall()
            
            # Convert to dictionary list
            result = []
            for row in rows:
                # Convert Row to dict
                row_dict = {key: row[key] for key in row.keys()}
                # Serialize special values
                for key, value in row_dict.items():
                    if hasattr(value, 'isoformat') and callable(getattr(value, 'isoformat')):
                        row_dict[key] = value.isoformat()
                    elif isinstance(value, (bytes, bytearray)):
                        row_dict[key] = value.hex()
                
                result.append(row_dict)
            
            return result

    @staticmethod
    async def execute_mysql_query(sql_query: str, db_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Execute a query in MySQL."""
        import aiomysql
        
        # Extract connection parameters
        host = db_config.get("host", "localhost")
        port = db_config.get("port", 3306)
        user = db_config.get("user", "")
        password = db_config.get("password", "")
        database = db_config.get("database", "")
        
        # Create connection pool
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
                # Run the query
                await cursor.execute(sql_query)
                rows = await cursor.fetchall()
                
                # Convert results
                result = []
                for row in rows:
                    # Convert non-serializable values ​​(such as bytes, datetime, etc.)
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
        
        # Close the pool
        pool.close()
        await pool.wait_closed()

    @staticmethod
    async def execute_postgresql_query(sql_query: str, db_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Execute a query in PostgreSQL."""
        import asyncpg
        
        # Extract connection parameters
        host = db_config.get("host", "localhost")
        port = db_config.get("port", 5432)
        user = db_config.get("user", "")
        password = db_config.get("password", "")
        database = db_config.get("database", "")
        
        # Connect to the database
        conn = await asyncpg.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database
        )
        
        try:
            # Run the query
            rows = await conn.fetch(sql_query)
            
            # Convert Record to Dict
            result = []
            for row in rows:
                # Convert asyncpg.Record to dict
                row_dict = dict(row)
                
                # Convert non-serializable values
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
            # Close the connection
            await conn.close()

    @staticmethod
    async def execute_mongodb_query(mongo_query, db_config):
        """
        Executes a MongoDB query and returns the results.

        Args:
            mongo_query (MongoDBQuery): The MongoDB query to execute
            db_config (dict): Database configuration

        Returns:
            tuple: (result, explanation)
        """
        import motor.motor_asyncio
        from bson.json_util import dumps, loads
        
        try:
            # Get connection parameters
            host = db_config.get("host", "localhost")
            port = db_config.get("port", 27017)
            user = db_config.get("user", "")
            password = db_config.get("password", "")
            database = db_config.get("database", "")
            
            # Build the connection URL
            if user and password:
                connection_string = f"mongodb://{user}:{password}@{host}:{port}/{database}"
            else:
                connection_string = f"mongodb://{host}:{port}/{database}"
            
            # Connect to MongoDB
            client = motor.motor_asyncio.AsyncIOMotorClient(connection_string)
            db = client[database]
            collection = db[mongo_query.collection]
            
            # Determine the operation to be performed
            if mongo_query.operation == "find":
                # Run search query
                cursor = collection.find(
                    mongo_query.filter or {},
                    mongo_query.projection or None
                )
                
                # Apply options if they exist
                if mongo_query.sort:
                    cursor = cursor.sort(mongo_query.sort)
                if mongo_query.limit:
                    cursor = cursor.limit(mongo_query.limit)
                if mongo_query.skip:
                    cursor = cursor.skip(mongo_query.skip)
                
                # Get results
                result_list = await cursor.to_list(length=100)  # Limit to 100 documents by default
                
                # Convert ObjectId to string for JSON serialization
                result_serializable = loads(dumps(result_list))
                
                # Generate explanation
                num_docs = len(result_serializable)
                if num_docs == 0:
                    explanation = "The query did not return any documents."
                else:
                    explanation = f"The query returned {num_docs} {'document' if num_docs == 1 else 'documents'}."
                    
                    # Add additional information
                    if mongo_query.projection:
                        fields = ", ".join(mongo_query.projection.keys())
                        explanation += f" Selected fields: {fields}."
                    if mongo_query.sort:
                        explanation += " The results are sorted according to the specified criteria."
                    if mongo_query.limit:
                        explanation += f" The search was limited to {mongo_query.limit} documents."
                
                return result_serializable, explanation
            elif mongo_query.operation == "aggregate":
                # Run aggregation
                pipeline = mongo_query.pipeline or []
                
                # Apply projection and sorting options if they exist
                if mongo_query.projection:
                    pipeline.append({"$project": mongo_query.projection})
                if mongo_query.sort:
                    pipeline.append({"$sort": mongo_query.sort})
                if mongo_query.limit:
                    pipeline.append({"$limit": mongo_query.limit})
                if mongo_query.skip:
                    pipeline.append({"$skip": mongo_query.skip})
                
                cursor = collection.aggregate(pipeline)
                
                # Get results
                result_list = await cursor.to_list(length=100)
                result_serializable = loads(dumps(result_list))
                
                num_docs = len(result_serializable)
                if num_docs == 0:
                    explanation = "The aggregation did not return any documents."
                else:
                    explanation = f"The aggregation returned {num_docs} {'document' if num_docs == 1 else 'documents'}."
                    if mongo_query.projection:
                        fields = ", ".join(mongo_query.projection.keys())
                        explanation += f" Selected fields: {fields}."
                    if mongo_query.sort:
                        explanation += " The results are sorted according to the specified criteria."
                    if mongo_query.limit:
                        explanation += f" The search was limited to {mongo_query.limit} documents."
                
                return result_serializable, explanation
            elif mongo_query.operation == "findOne":
                # Run a search query on a document
                document = await collection.find_one(
                    mongo_query.filter or {},
                    mongo_query.projection or None
                )
                
                # Convert ObjectId to string for JSON serialization
                result_serializable = loads(dumps(document))
                
                # Generate explanation
                if result_serializable:
                    explanation = "The requested document was found."
                    if mongo_query.projection:
                        fields = ", ".join(mongo_query.projection.keys())
                        explanation += f" Selected fields: {fields}."
                else:
                    explanation = "No documents were found matching the search criteria."
                
                return result_serializable, explanation
                
            elif mongo_query.operation == "insertOne":
                # Execute insertion of a document
                result = await collection.insert_one(mongo_query.document)
                
                # Generate explanation
                explanation = f"A new document has been inserted with ID: {str(result.inserted_id)}."
                
                return {"insertedId": str(result.inserted_id)}, explanation
                
            elif mongo_query.operation == "updateOne":
                # Run a document update
                result = await collection.update_one(
                    mongo_query.filter or {},
                    mongo_query.update
                )
                
                # Run a document update
                matched = result.matched_count
                modified = result.modified_count
                
                if matched == 0:
                    explanation = "No documents were found matching the search criteria to update."
                elif modified == 0:
                    explanation = "A document was found but no changes were made (the values are identical to the existing ones)."
                else:
                    explanation = "The document has been successfully updated."
                
                return {
                    "matchedCount": matched,
                    "modifiedCount": modified
                }, explanation
                
            elif mongo_query.operation == "deleteOne":
                # Execute deletion of a document
                result = await collection.delete_one(mongo_query.filter or {})
                
                # Generate explanation
                deleted = result.deleted_count
                
                if deleted == 0:
                    explanation = "No documents were found that matched the criteria for deletion."
                else:
                    explanation = "The document has been successfully deleted."
                
                return {"deletedCount": deleted}, explanation
                
            else:
                # Unsupported operation
                raise ValueError(f"MongoDB operation not supported: {mongo_query.operation}")
        
        except Exception as e:
            # Provide an explanation of the error
            error_message = str(e)
            explanation = f"Error executing MongoDB query: {error_message}"
            
            # Suggest solutions based on the type of error
            if "Authentication failed" in error_message:
                explanation += " The login credentials are incorrect."
            elif "not authorized" in error_message:
                explanation += " The user does not have sufficient permissions for this operation."
            elif "No such collection" in error_message:
                explanation += " The specified collection does not exist in the database."
            
            raise ValueError(explanation)

    @staticmethod
    async def generate_mongodb_query(
        query: str,
        db_info: Dict[str, Any],
        collection_name: Optional[str] = None,
        db_connection = None
    ) -> MongoDBQuery:
        """
       Generates a MongoDB query from a natural language query.Designed to work with client databases without prior knowledge of collections.

        Args:
            query: Natural language query
            db_info: Database schema information
            collection_name: Name of the collection to query (optional)
            db_connection: Database connection for further exploration (optional)

        Returns:
            MongoDBQuery object with the generated query
        """
        # Prepare the context with the information from the database
        db_context = json.dumps(db_info, indent=2, default=str)
        
        # Get available collections
        available_collections = []
        if "tables" in db_info:
            available_collections = list(db_info.get("tables", {}).keys())
        elif "collections" in db_info:
            available_collections = list(db_info.get("collections", {}).keys())
        
        # If a specific collection was provided, use it
        if collection_name:
            selected_collection = collection_name
        else:
            # Attempt to determine the best collection based on the query
            selected_collection = Utils.determine_best_collection(query, available_collections, db_connection)
            logger.info(f"Colección seleccionada automáticamente: {selected_collection}")
        
        # Create system prompt specific to the selected collectionCreate system prompt specific to the selected collection
        collection_info = ""
        if selected_collection and selected_collection in db_info.get("tables", {}):
            # Include specific information about the selected collection
            try:
                collection_data = db_info["tables"][selected_collection]
                collection_info = f"\nSELECTED COLLECTION INFORMATION ({selected_collection}):\n"
                
                # Include fields/structure
                if "columns" in collection_data or "fields" in collection_data:
                    fields = collection_data.get("columns", collection_data.get("fields", []))
                    collection_info += f"Fields: {json.dumps([f.get('name') for f in fields], default=str)}\n"
                
                # Include sample documents if available
                if "sample_data" in collection_data and collection_data["sample_data"]:
                    collection_info += f"Sample documents: {json.dumps(collection_data['sample_data'][:2], default=str)}\n"
                    
            except Exception as e:
                logger.warning(f"Error preparing collection information: {str(e)}")
        
        # Create system prompt for MongoDB query
        system_prompt = f"""
       You are an assistant specialized in translating natural language queries into MongoDB operations.

        DATABASE STRUCTURE:
        {db_context}

        {collection_info}

        IMPORTANT: If the user requests a field that is within an object (for example, 'customer.name'), you must use the dot notation in the filter, for example: {{"customer.name": "John"}} to search for documents where the 'customer' object has 'name' equal to 'John'.
        IMPORTANT: If the user requests a field that is within an array of objects (for example, 'items.name'), you must use the dot notation in the filter, for example: {{"items.name": "notepad"}} to search for documents where any object in the 'items' array has 'name' equal to 'notepad'.
        Your task is:  
        1. Carefully analyze the user's query
        2. Use the '{selected_collection}' collection for this query
        3. Construct the appropriate query (find or aggregate)
        4. Return the query as a JSON object with the following format:

        For simple searches:
        {{
        "collection": "{selected_collection}",
        "operation": "find",
        "query": {{ /* filters */ }},
        "projection": {{ /* fields to include/exclude */ }},
        "sort": {{ /* ordering */ }},
        "limit": 10
        }}

        For aggregations:
        {{
        "collection": "{selected_collection}",
        "operation": "aggregate",
        "pipeline": [
        {{ /* step 1 */ }},
        {{ /* step 2 */ }}
        ]   
        }}

        IMPORTANT: Use the exact "{selected_collection}" collection in your response.
        Respond ONLY with the JSON object, without any other text.
        """
        print(system_prompt)
        try:
            # Initialize OpenAI client
            client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)
            
            # Submit application to OpenAI
            response = await client.chat.completions.create(
                model=settings.OPENAI.OPENAI_MODEL,
                max_tokens=settings.OPENAI.MAX_TOKENS,
                temperature=0.2,  # Low temperature for more deterministic responses
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ]
            )
            
            # Extract and process JSON response
            ai_response = response.choices[0].message.content
            json_text = AIQuery.clean_json_response(ai_response)
            
            # Parse JSON
            query_data = json.loads(json_text)
            
            # Verify that the selected collection is being used
            if query_data.get("collection") != selected_collection:
                logger.warning(f"The AI selected a different collection: {query_data.get('collection')}. Forcing use of {selected_collection}")
                query_data["collection"] = selected_collection
            
            # Create MongoDBQuery object
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
            
            # Record the generated query
            logger.info(f"Generated MongoDB query for collection {mongo_query.collection}: {json.dumps(mongo_query.dict() if hasattr(mongo_query, 'dict') else vars(mongo_query), default=str)}")
            
            return mongo_query
            
        except Exception as e:
            logger.error(f"Error generating MongoDB query: {str(e)}")
            
            # Perform connection diagnostics if available
            if db_connection:
                try:
                    debug_result = await Diagnostic.debug_mongodb_connection(db_connection, collection_name)
                    logger.info(f"MongoDB Diagnostics: {json.dumps(debug_result, default=str)}")
                    
                    # Using diagnostic information for better fallback selection
                    if debug_result.get("available_collections"):
                        available_collections = debug_result["available_collections"]
                except Exception as debug_error:
                    logger.error(f"Error when performing diagnosis: {str(debug_error)}")
            
            
            # In case of error, generate a safe and simple query with the selected collection
            return MongoDBQuery(
                collection=selected_collection or (available_collections[0] if available_collections else "users"),
                operation="find",
                query={},
                limit=10,
                skip=0
            )
            
    @staticmethod
    def clean_json_response(response_text: str) -> str:
        """Cleans and extracts the JSON from the model response."""
        json_text = response_text.strip()
        
        # Remove markdown code blocks
        if json_text.startswith('```') and json_text.endswith('```'):
            json_text = json_text[3:-3].strip()
        elif '```' in json_text:
            # Extract content between the first triple quotes of code
            match = re.search(r'```(?:json)?(.*?)```', json_text, re.DOTALL)
            if match:
                json_text = match.group(1).strip()
                
        # Remove language prefix
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
       Generates a natural language explanation of the results of a MongoDB query.

        Args:
            query: Original natural language query
            mongo_query: Generated MongoDB query (object or dictionary)
            result: Query result

        Returns:
            Natural language explanation
        """
        # Convert mongo_query to dictionary if it is an object
        if hasattr(mongo_query, "model_dump"):
            mongo_query_dict = mongo_query.model_dump()
        elif hasattr(mongo_query, "dict"):
            mongo_query_dict = mongo_query.dict()
        elif not isinstance(mongo_query, dict):
            # Trying to extract common attributes
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
        
        # Limit output for the prompt
        result_sample = result.data[:5] if isinstance(result.data, list) else [result.data]
        
        # Extract relevant information from the query
        collection = mongo_query_dict.get("collection", "")
        operation = mongo_query_dict.get("operation", "find")
        filter_criteria = mongo_query_dict.get("filter", {}) or mongo_query_dict.get("query", {})
        fields = list(mongo_query_dict.get("projection", {}).keys()) if mongo_query_dict.get("projection") else []
        pipeline = mongo_query_dict.get("pipeline", [])
        
        # Analyze the structure of the results
        fields_in_results = []
        if result.count > 0 and isinstance(result_sample[0], dict):
            fields_in_results = list(result_sample[0].keys())
        
        # Determine the type of query
        query_type = operation
        is_aggregation = operation == "aggregate" or (isinstance(pipeline, list) and len(pipeline) > 0)
        has_filter = bool(filter_criteria)
        has_projection = bool(fields)
        
        # Create summary for context
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
        
        # Add specific information according to the type of operation
        if operation == "find" or operation == "findOne":
            summary["filter_criteria"] = filter_criteria
            summary["sort"] = mongo_query_dict.get("sort", {})
            summary["limit"] = mongo_query_dict.get("limit", 0)
            summary["skip"] = mongo_query_dict.get("skip", 0)
        elif operation == "aggregate":
            summary["pipeline_stages"] = [list(stage.keys())[0] if isinstance(stage, dict) else str(stage) for stage in pipeline]
        elif operation in ["insertOne", "updateOne", "deleteOne"]:
            summary["affected_documents"] = result.count
        
        # Preparing context for OpenAI
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
            query_language = "es"  # Default value if cannot be detected

        query_language = detect(query)
        context["detected_language"] = query_language
        
        context_json = json.dumps(context, indent=2, default=str)
                
        # Generar prompt para OpenAI
        system_prompt = f"""
        You are an assistant specialized in explaining MongoDB query results.
        Your goal is to provide clear and natural explanations of the results obtained,
    	avoiding unnecessary technical jargon while maintaining the accuracy of the information.

        IMPORTANT: The original query is in {context["detected_language"]}.
        YOUR ANSWER MUST BE IN THE SAME LANGUAGE AS THE ORIGINAL QUERY.

        Specific Guidelines:
        1. Start with a concise summary of the results (how many documents were found)
        2. Describe the type of query performed (search, filtering, aggregation, etc.) without using technical MongoDB terminology
        3. Discuss the most important findings or patterns identified in the data
        4. Highlight relevant information about the documents displayed
        5. The explanation should be understandable to non-technical users
        6. Use accessible but precise language
        7. DO NOT mention MongoDB syntax or technical terms such as "$match", "$group", etc.
        8. Prioritize relevance to the user over technical details

        Desired Format:
        - Start with a direct and clear summary
        - Continue with 1-3 important observations about the data
        - If relevant, end with a brief conclusion
        - Keep the explanation to 100-150 words maximum
        - Return the explanation in the same language as the "{query}" question

        """
        
        try:
            # Initialize OpenAI client
            client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)
            
            # Submit application to OpenAI
            response = await client.chat.completions.create(
                model=settings.OPENAI.OPENAI_MODEL,
                max_tokens=settings.OPENAI.MAX_TOKENS,
                temperature=0.7,  #A little more creativity for the explanation
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Explain the results of this MOngoDB query in {context['detected_language']}:\n{context_json}"}
                ]
            )
            
            # Extract explanation
            explanation = response.choices[0].message.content
            
            return explanation
        
        except Exception as e:
            # In case of error, generate basic explanation
            logger.error(f"Error al generar explicación MongoDB: {str(e)}")
            
            # Create a simple explanation based on the available data
            if result.count == 0:
                return f"No documents were found in the collection {collection} that match your query."
            else:
                explanation = f"{result.count} documents were found in the {collection} collection."
                
                # Add information about the type of operation
                if operation == "findOne":
                    explanation = f"The requested document was found in the {collection} collection."
                elif operation == "aggregate":
                    explanation = f"Aggregation on collection {collection} returned {result.count} results."
                elif operation == "insertOne":
                    explanation = f"A new document has been successfully inserted into the {collection} collection."
                elif operation == "updateOne":
                    explanation = f"A document in the {collection} collection has been successfully updated."
                elif operation == "deleteOne":
                    explanation = f"A document has been successfully deleted from the {collection} collection."
                
                # Add information about fields if available
                if fields_in_results:
                    sample_fields = fields_in_results[:5]  # Limit to 5 fields to avoid cluttering the explanation.
                    explanation += f" Fields present in the results include: {', '.join(sample_fields)}"
                    if len(fields_in_results) > 5:
                        explanation += f" and {len(fields_in_results) - 5} more."
                    else:
                        explanation += "."
                
                # Add runtime information
                if result.query_time_ms > 0:
                    explanation += f" The query was executed in {result.query_time_ms:.1f} ms."
                    
                return explanation
    
    @staticmethod
    async def generate_sql_result_explanation(
        query: str,
        sql_query: str,
        result: QueryResult
    ) -> str:
        """
       Generates a natural language explanation of the results of an SQL query.

        Args:
            query: Original natural language query
            sql_query: Executed SQL query
            result: Query result

        Returns:
            Natural language explanation
        """
        # Check for results
        if result.count == 0:
            return "No records were found that match your query."
        
        # Limit output for the prompt
        result_sample = result.data[:5]
        
        # Analyze the structure of the results for a better explanation
        column_names = []
        if result.count > 0 and isinstance(result.data[0], dict):
            column_names = list(result.data[0].keys())
        
        # Analyze the SQL query to extract relevant information
        sql_lower = sql_query.lower()
        selected_tables = []
        join_tables = []
        
        # Detect tables in the query
        from_pattern = r'from\s+([a-zA-Z0-9_\.]+)'
        join_pattern = r'join\s+([a-zA-Z0-9_\.]+)'
        
        from_matches = re.findall(from_pattern, sql_lower)
        if from_matches:
            selected_tables.extend(from_matches)
        
        join_matches = re.findall(join_pattern, sql_lower)
        if join_matches:
            join_tables.extend(join_matches)
        
        # Detect SQL query type
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
        
        # Create a basic summary to include in the context
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
        
        # Preparing context for OpenAI
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
            query_language = "es"  # Default value if cannot be detected

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
        
        # Generate prompt for OpenAI
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
            # Initialize OpenAI client
            client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)
            
            # Submit application to OpenAI
            response = await client.chat.completions.create(
                model=settings.OPENAI.OPENAI_MODEL,
                max_tokens=settings.OPENAI.MAX_TOKENS,
                temperature=0.7,  # A little more creativity for the explanation
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Explica los resultados de esta consulta SQL en {context['detected_language']}:\n{context_json}"}
                ]
            )
            
            # Extract explanation
            explanation = response.choices[0].message.content
            
            return explanation
        
        except Exception as e:
            # In case of error, generate basic explanation
            logger.error(f"Error generating SQL explanation: {str(e)}")
            
            # Create a simple explanation based on the available data
            if result.count == 0:
                return "No records were found matching your query."
            else:
                explanation = f"{result.count} records were found in the database."
                
                # Add basic information about columns if available
                if column_names:
                    explanation += f" The fields present in the results are: {', '.join(column_names)}."
                
                # Add information about the tables involved
                if selected_tables or join_tables:
                    tables = list(set(selected_tables + join_tables))
                    explanation += f" The query was performed on {', '.join(tables)}."
                
                # Add runtime information
                if result.query_time_ms > 0:
                    explanation += f" The query was executed in {result.query_time_ms:.1f} ms."
                    
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
        Processes a natural language query and generates an explanation.

        Args:
            query: Natural language query
            db_schema: Database schema
            collection_name: Name of the collection/table to query (optional)
            config_id: Configuration ID (optional)

        Returns:
            Dictionary with the generated query, results, and explanation


        """
        try:
            # Determine the database type
            db_type = db_schema.get("type", "").lower()
            
            if db_type == "sql":
                # For SQL databases
                engine = db_schema.get("engine", "sqlite").lower()
                
                # Generate SQL query
                sql_query = await AIQuery.generate_sql_query(query, db_schema, engine)
                
                # Execute SQL query
                result_data, execution_time = await AIQuery.execute_sql_query(sql_query, db_schema)
                
                # Create QueryResult object
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
                
                # Generate explanation
                explanation = await AIQuery.generate_sql_result_explanation(query, sql_query, result)
                
                # Return full response
                return {
                    "explanation": explanation,
                    "query": {
                        "sql": sql_query,
                        "engine": engine
                    },
                    "result": result
                }
                
            elif db_type in ["nosql", "mongodb"]:
                # For MongoDB
                
                if db_connection:
                    debug_info = await Diagnostic.debug_mongodb_connection(db_connection)
                    logger.info(f"MongoDB Diagnostics before query: {json.dumps(debug_info, default=str)}")
                    
                    if debug_info.get("connection_status") != "connected":
                        return {
                            "explanation": "Could not connect to the MongoDB database",
                            "query": None,
                            "result": None,
                            "error": True,
                            "debug_info": debug_info
                        }
                
                # Generate MongoDB query
                mongo_query = await AIQuery.generate_mongodb_query(
                    query, db_schema, collection_name, db_connection
                )
                
                # Execute the MongoDB query
                # ​​Implementing MongoDB query execution...
                # (Assuming a method exists to execute the query)
                    
                # Placeholder for results
                result = QueryResult(
                    data=[],  # The real data would go here
                    count=0,
                    query_time_ms=0,
                    metadata={
                        "config_id": config_id
                    }
                )
                
                # Prepare the query object for return
                # Safely convert MongoDBQuery to dict
                if hasattr(mongo_query, "model_dump"):
                    query_dict = mongo_query.model_dump()
                elif hasattr(mongo_query, "dict"):
                    query_dict = mongo_query.dict()
                else:
                    # Fallback: Create dictionary manually
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
                
                # Generate explanation
                explanation = await AIQuery.generate_result_explanation(query, mongo_query, result)
                
                # Return full response
                return {
                    "explanation": explanation,
                    "query": query_dict,
                    "result": result
                }
            
            else:
                # Unsupported database type
                return {
                    "explanation": f"Unsupported database type: {db_type}",
                    "query": None,
                    "result": None,
                    "error": True
                }
                
        except Exception as e:
            logger.error(f"Error processing natural language query: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            
            return {
                "explanation": f"Error processing query: {str(e)}",
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
        Processes a natural language query and generates an explanation.

        Args:
            question: Natural language query
            db_schema: Database schema

        Returns:
            Dictionary with the generated query, results, and explanation of the data
            Determines which collection to query
        """

        # Create system prompt for MongoDB query
        system_prompt = f"""
        You are an assistant specialized in identifying the necessary collections related to the user's query.

        DATABASE STRUCTURE:
        {db_schema}

        Your task is to:
        1. Carefully analyze the user's query
        2. Understand the database schema
        3. Determine which collection(s) should be queried

        Respond ONLY with the collections to be queried in a list format, separated by commas.
        """
        
        try:
            # Initialize OpenAI client
            client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)

            # Submit application to OpenAI
            response = await client.chat.completions.create(
                model=settings.OPENAI.OPENAI_MODEL,
                max_tokens=settings.OPENAI.MAX_TOKENS,
                temperature=0.2,  # Low temperature for more deterministic responses
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question}
                ]
            )
            
            # Extract and process JSON response
            ai_response = response.choices[0].message.content
            json_text = AIQuery.clean_json_response(ai_response)
            
            # Analise JSON
            query_data = json.loads(json_text)
            
            return query_data
            
        except Exception as e:
            logger.error(f"Error generating MongoDB query: {str(e)}")
            
            
        
