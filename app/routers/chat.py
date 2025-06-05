from fastapi import APIRouter, Depends, HTTPException, status, Query, Path, Body
from typing import List, Optional, Dict, Any
import json

from app.models.message import MessageCreate, MessageResponse, MessageWithAIResponse
from app.models.conversation import ConversationCreate, ConversationResponse, ConversationWithMessages
from app.services import chat_service
from app.middleware.authentication import get_api_key
from app.core.permissions import verify_permissions, PermissionError
from app.core.logging import LogEntry

router = APIRouter()

@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
    conversation_data: ConversationCreate = Body(...),
    api_key = Depends(get_api_key)
):
    """
    Crea una nueva conversación
    """
    try:
        # Verificar permisos básicos
        verify_permissions(api_key.level, "write")
        
        # Crear conversación
        print("Entra al conversation create")
        conversation = await chat_service.create_conversation(
            user_id=conversation_data.user_id,
            api_key_id=api_key.id,
            title=conversation_data.title,
            metadata=conversation_data.metadata
        )
        
        return conversation
    
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    
    except Exception as e:
        LogEntry("create_conversation_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al crear conversación"
        )

@router.get("/conversations/{conversation_id}", response_model=ConversationWithMessages)
async def get_conversation(
    conversation_id: str = Path(...),
    messages_limit: int = Query(10, ge=1, le=100),
    api_key = Depends(get_api_key)
):
    """
    Obtiene una conversación con sus mensajes
    """
    try:
        # Verificar permisos básicos
        verify_permissions(api_key.level, "read")
        
        # Obtener conversación y mensajes
        conversation = await chat_service.get_conversation_with_messages(
            conversation_id=conversation_id,
            api_key_id=api_key.id,
            limit=messages_limit
        )
        
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversación no encontrada"
            )
        
        return conversation
        
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    
    except HTTPException:
        raise
        
    except Exception as e:
        LogEntry("get_conversation_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("conversation_id", conversation_id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al obtener conversación"
        )

@router.post("/conversations/{conversation_id}/messages", response_model=MessageWithAIResponse)
async def process_message(
    conversation_id: str = Path(...),
    message: MessageCreate = Body(...),
    api_key = Depends(get_api_key)
):
    """
    Procesa un mensaje y obtiene respuesta de la IA
    """
    try:
        print(f"Working on conversation: {conversation_id}")
        
        # Verificar permisos básicos
        verify_permissions(api_key.level, "write")
        
        # Verificar que el conversation_id del path coincide con el del body
        if message.conversation_id != conversation_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El ID de conversación no coincide"
            )
        
        # Procesar mensaje
        response = await chat_service.process_message(
            content=message.content,
            conversation_id=conversation_id,
            user_id=None,  # No hay usuario en API key
            api_key_id=api_key.id,
            api_key_level=api_key.level,
            metadata=message.metadata
        )
        
        print("\nUser message:")
        print(response.user_message.content)
        
        print("\nAI response:")
        print(response.ai_response.content)
        
        print("\nToken usage:")
        print(json.dumps(response.ai_response.tokens, indent=2))
        
        print("\nProcessing time:")
        print(f"{response.ai_response.processing_time:.2f} seconds")
        
        return response
        
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    
    except HTTPException:
        raise
        
    except Exception as e:
        LogEntry("process_message_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("conversation_id", conversation_id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al procesar mensaje"
        )
