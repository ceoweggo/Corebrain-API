# System Components
 
CoreBrain consists of several specialized modules that work together to provide a seamless experience. Below are the main components:
 
## 1. Authentication Module
 
![Authentication Module](../assets/images/auth-module.png)
 
This module manages:
 
- **API Keys**: Creation, validation, and revocation of API keys.
- **JWT Tokens**: For authentication within the dashboard.
- **Permission Control**: Validation of access levels for different operations.
 
### Key Components
 
- `AuthService`: Core service for authentication operations.
- `ApiKeyRepository`: Handles storage and retrieval of API keys.
- `PermissionsManager`: Verifies permissions for different actions.
 
## 2. Chat Module
 
![Chat Module](../assets/images/chat-module.png)
 
Manages the conversational flow between users and the AI:
 
- **Conversations**: Creation and management of conversation threads.
- **Messages**: Processing of incoming and outgoing messages.
- **History**: Maintains conversation context.
 
### Key Components
 
- `ChatService`: Coordinates message processing.
- `ConversationRepository`: Stores and retrieves conversations.
- `MessageRepository`: Manages individual messages.
 
## 3. AI Processing Module
 
![AI Module](../assets/images/ai-module.png)
 
Bridge between natural language and AI models:
 
- **Anthropic Client**: Connects to the Claude API.
- **Prompt Management**: Creates effective instructions for the AI.
- **Response Processing**: Extracts and formats AI responses.
 
### Key Components
 
- `AIService`: Manages interactions with Anthropic Claude.
- `PromptManager`: Builds and optimizes system prompts.
- `ResponseProcessor`: Processes and filters responses.
 
## 4. Database Module
 
![Database Module](../assets/images/db-module.png)
 
Enables interaction between AI and databases:
 
- **MongoDB Connections**: Manages database connections.
- **Query Processing**: Translates natural language into MongoDB operations.
- **Sanitization**: Validates and sanitizes queries.
 
### Key Components
 
- `DatabaseService`: Main interface for database operations.
- `QueryTranslator`: Converts natural language queries into MongoDB queries.
- `ResultFormatter`: Formats results for consumption.
 
## 5. Analytics Module
 
![Analytics Module](../assets/images/analytics-module.png)
 
Tracks and analyzes system usage:
 
- **Event Tracking**: Logs important system events.
- **Usage Metrics**: Tracks token consumption and query usage.
- **Costs**: Calculates associated usage costs.
 
### Key Components
 
- `AnalyticsService`: Coordinates analytics collection and processing.
- `EventTracker`: Logs key system events.
- `CostCalculator`: Estimates and tracks API usage costs.
 
## 6. SDK
 
![SDK Structure](../assets/images/sdk-structure.png)
 
Client library for application integration:
 
- **API Client**: Connects with API endpoints.
- **Cache Management**: Optimizes performance with local caching.
- **Error Handling**: Unified handling of errors and retries.
 
### Key Components
 
- `CoreBrainClient`: Main class for interacting with the API.
- `ConversationManager`: Manages conversations from the client side.
- `MessageManager`: Sends and receives messages.