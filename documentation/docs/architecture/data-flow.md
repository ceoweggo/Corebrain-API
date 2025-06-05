# Data Flow
 
This section explains in detail how data flows through the different components of CoreBrain, from the initial request to the final response.
 
## Message Processing
 
![Message Processing Flow](../assets/images/message-flow.png)
 
### 1. Client Request
 
The flow begins when a client application sends a message using the CoreBrain SDK:
 
```javascript
const response = await client.messages.send({
  conversationId: "conv_123",
  content: "How many users signed up last week?"
});
```
 
### 2. Validation and Authentication
 
When the request reaches the API:
 
1. The API key is verified in the `X-API-Key` header.
2. Permissions for the requested operation are checked.
3. The format and content of the request are validated.
 
### 3. Contextual Processing
 
The chat service:
 
1. Retrieves the specified conversation or creates a new one.
2. Loads the previous message history for context.
3. Saves the user's message to the database.
 
### 4. Query Analysis
 
The system determines whether the message requires access to the database:
 
1. Analyzes the content of the message for keywords.
2. If needed, fetches metadata about the available collections.
3. Prepares the context for the AI model.
 
### 5. Interaction with the AI
 
The AI service:
 
1. Constructs a prompt for Claude with the message, history, and database context.
2. Sends the request to the Anthropic API.
3. Claude generates a response, which may include MongoDB queries.
 
### 6. Query Execution (if needed)
 
If Claude suggests a MongoDB query:
 
1. The system extracts the query from the response.
2. Sanitizes the query to prevent malicious operations.
3. Executes the query against the specified database.
4. Formats the results to send them back to Claude.
 
### 7. Final Response Generation
 
With the query results:
 
1. A new prompt is generated for Claude including the results.
2. Claude interprets the data and generates a final response.
3. The system stores the response in the database.
4. The cost of the operation is calculated and recorded.
 
### 8. Return to the Client
 
Finally:
 
1. The response is formatted according to the API specification.
2. It is sent back to the client through the SDK.
3. Metrics and analytics are updated.
 
## Token and Cost Flow
 
![Token and Cost Flow](../assets/images/token-flow.png)
 
Each interaction with Claude consumes tokens, which have an associated cost. CoreBrain tracks this consumption in detail:
 
1. Counts input (prompt) and output (response) tokens.
2. Calculates the cost in USD based on Anthropic’s current rates.
3. Stores costs associated with the conversation.
4. Updates global metrics for billing and analytics.
 
## Performance Optimizations
 
CoreBrain implements several optimizations to improve performance and reduce costs:
 
1. **Caching**: Frequently asked responses are cached to avoid unnecessary calls to Claude.
2. **Pre-analysis**: The system analyzes whether a message requires database access before querying Claude.
3. **Context Limiting**: Only relevant information is sent to Claude to reduce token usage.
4. **Parallel Processing**: Database queries can be executed in parallel when possible.