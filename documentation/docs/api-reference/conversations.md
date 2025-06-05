
# Conversations

Conversations are threads of messages between the user and the AI. Each conversation has a unique identifier and can contain multiple messages.

## Endpoints

### Create conversation
```
POST /api/chat/conversations
```

**Headers:**
```
X-API-Key: tu_api_key_aquí
Content-Type: application/json
```

**Body:**
```json
{
  "title": "Sales Analysis",
  "metadata": {
    "source": "app_mobile",
    "user_reference": "user_12345"
  }
}
```

**Successful response (201):**
```json
{
  "id": "conv_123456789",
  "title": "Sales Analysis",
  "created_at": "2025-03-23T12:34:56Z",
  "updated_at": "2025-03-23T12:34:56Z",
  "last_message_at": null,
  "message_count": 0
}
```

### Get conversation

```
GET /api/chat/conversations/{conversation_id}
```

**Query parameters:**
- `messages_limit` - Maximum number of messages to return (default: 10)

**Headers:**
```
X-API-Key: tu_api_key_aquí
```

**Successful response (200):**
```json
{
  "id": "conv_123456789",
  "title": "Sales Analysis",
  "created_at": "2025-03-23T12:34:56Z",
  "updated_at": "2025-03-23T12:34:56Z",
  "last_message_at": "2025-03-23T12:35:30Z",
  "message_count": 2,
  "messages": [
    {
      "id": "msg_123456789",
      "content": "How many sales did we have last month?",
      "is_user": true,
      "created_at": "2025-03-23T12:35:00Z",
      "metadata": {}
    },
    {
      "id": "msg_987654321",
      "content": "Last month you had a total of 1,254 sales amounting to $45,678.",
      "is_user": false,
      "created_at": "2025-03-23T12:35:30Z",
      "metadata": {
        "model": "claude-3-opus-20240229",
        "tokens": {
          "input": 22,
          "output": 24
        }
      }
    }
  ]
}
```

### List conversations

```
GET /api/chat/conversations
```

**Query parameters:**
- `limit` - Maximum number of conversations (default: 20)
- `offset` - Offset for pagination (default: 0)

**Headers:**
```
X-API-Key: tu_api_key_aquí
```

**Successful response (200):**
```json
{
  "conversations": [
    {
      "id": "conv_123456789",
      "title": "Sales Analysis",
      "created_at": "2025-03-23T12:34:56Z",
      "updated_at": "2025-03-23T12:35:30Z",
      "last_message_at": "2025-03-23T12:35:30Z",
      "message_count": 2
    },
    {
      "id": "conv_987654321",
      "title": "Technical Support",
      "created_at": "2025-03-23T10:15:30Z",
      "updated_at": "2025-03-23T10:20:45Z",
      "last_message_at": "2025-03-23T10:20:45Z",
      "message_count": 5
    }
  ],
  "count": 2
}
```

### Update conversation

```
PATCH /api/chat/conversations/{conversation_id}
```

**Headers:**
```
X-API-Key: tu_api_key_aquí
Content-Type: application/json
```

**Body:**
```json
{
  "title": "Sales Analysis Q1 2025"
}
```

**Successful response (200):**
```json
{
  "id": "conv_123456789",
  "title": "Sales Analysis Q1 2025",
  "created_at": "2025-03-23T12:34:56Z",
  "updated_at": "2025-03-23T13:45:30Z",
  "last_message_at": "2025-03-23T12:35:30Z",
  "message_count": 2
}
```

### Delete conversation

```
DELETE /api/chat/conversations/{conversation_id}
```

**Headers:**
```
X-API-Key: tu_api_key_aquí
```

**Successful response (200):**
```json
{
  "message": "Conversation deleted successfully"
}
```

# docs/api-reference/messages.md

# Messages

Messages are the main interaction component with the AI. A user message generates a response from the AI using the conversation context.

## Endpoints

### Send message

```
POST /api/chat/conversations/{conversation_id}/messages
```

**Headers:**
```
X-API-Key: tu_api_key_aquí
Content-Type: application/json
```

**Body:**
```json
{
  "content": "How many active users did we have last week?",
  "conversation_id": "conv_123456789",
  "metadata": {
    "source": "dashboard_analytics",
    "user_locale": "es-MX"
  }
}
```

**Successful response (200):**
```json
{
  "user_message": {
    "id": "msg_123456789",
    "content": "How many active users did we have last week?",
    "is_user": true,
    "created_at": "2025-03-23T14:15:30Z",
    "metadata": {
      "source": "dashboard_analytics",
      "user_locale": "es-MX"
    }
  },
  "ai_response": {
    "id": "msg_987654321",
    "content": "Last week you had 2,543 active users, representing a 15% increase compared to the previous week.",
    "model": "claude-3-opus-20240229",
    "created_at": "2025-03-23T14:15:32Z",
    "tokens": {
      "input": 35,
      "output": 28
    },
    "processing_time": 1.8,
    "metadata": {
      "anthropic_version": "0.5.0",
      "model": "claude-3-opus-20240229",
      "queries_executed": 1,
      "cost": {
        "input_usd": 0.000525,
        "output_usd": 0.0021,
        "total_usd": 0.002625
      }
    }
  }
}
```

### Get messages from a conversation

```
GET /api/chat/conversations/{conversation_id}/messages
```

**Query parameters:**
- `limit` -  Maximum number of messages (default: 50)
- `before` - Message ID before which to get messages (for pagination)

**Headers:**
```
X-API-Key: tu_api_key_aquí
```

**Successful response (200):**
```json
{
  "messages": [
    {
      "id": "msg_123456789",
      "content": "How many active users did we have last week?",
      "is_user": true,
      "created_at": "2025-03-23T14:15:30Z",
      "metadata": {
        "source": "dashboard_analytics"
      }
    },
    {
      "id": "msg_987654321",
      "content": "Last week you had 2,543 active users, representing a 15% increase compared to the previous week.",
      "is_user": false,
      "created_at": "2025-03-23T14:15:32Z",
      "metadata": {
        "model": "claude-3-opus-20240229",
        "tokens": {
          "input": 35,
          "output": 28
        }
      }
    }
  ],
  "count": 2
}
```