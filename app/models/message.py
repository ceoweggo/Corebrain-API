
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, Dict, Any, List
import uuid

class MessageBase(BaseModel):
    content: str
    
class MessageCreate(MessageBase):
    conversation_id: str
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)

class MessageUpdate(BaseModel):
    content: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class MessageInDB(MessageBase):
    id: str
    conversation_id: str
    user_id: Optional[str] = None
    api_key_id: Optional[str] = None
    is_user: bool
    created_at: datetime
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    model_config = {"from_attributes": True}

class MessageResponse(BaseModel):
    id: str
    content: str
    is_user: bool
    created_at: datetime
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    model_config = {"from_attributes": True}

class AIResponse(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    model: str
    created_at: datetime = Field(default_factory=datetime.now)
    tokens: Dict[str, int] = {"input": 0, "output": 0, "total": 0}
    processing_time: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    model_config = {"from_attributes": True}

class MessageWithAIResponse(BaseModel):
    user_message: MessageResponse
    ai_response: AIResponse
    
    model_config = {"from_attributes": True}
