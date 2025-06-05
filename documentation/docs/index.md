# CoreBrain API
 
![CoreBrain Logo](assets/images/corebrain-logo.png){ align=center width=300 }
 
**AI platform that connects your data with the advanced intelligence of language models.**
 
CoreBrain is an API that enables developers to connect their MongoDB databases with language models like Claude from Anthropic to create powerful conversational experiences that can analyze, interpret, and respond to questions about stored data.
 
## Key Features
 
### 🧠 Advanced Intelligence
Powered by Claude from Anthropic, CoreBrain offers deep natural language understanding and the ability to handle complex instructions.
 
### 🔍 Natural Language Queries
Allows users to query MongoDB databases using natural language, without needing to know query syntax.
 
### 🛡️ Security by Design
Granular permission system ensures users only access the data they’re authorized to see.
 
### 📊 Built-in Analytics
Detailed tracking of usage, performance, and costs to help optimize integration.
 
### 🔄 Easy Integration
SDKs available for multiple languages and platforms, including JavaScript, Python, and more.
 
## Use Cases
 
CoreBrain is ideal for:
 
- **Internal assistants for companies** that need to answer questions about business data  
- **Data analysis applications** requiring a conversational interface  
- **Support chatbots** that need to query databases to resolve issues  
- **Automation tools** that need to interpret data and make decisions  
- **Interactive dashboards** where users can ask questions about metrics  
 
## Architecture
 
CoreBrain consists of three main components:
 
1. **api.corebrain.ai**: Backend powered by FastAPI that processes messages and queries, connecting with MongoDB and Anthropic.
2. **dashboard.corebrain.ai**: Admin interface for users.
3. **sdk.corebrain.ai**: SDK for integration in client applications.
 
## Getting Started
 
To start using CoreBrain, follow these steps:
 
1. [Sign up](https://dashboard.corebrain.ai/register) for an account.
2. [Create an API key](getting-started/configuration.md#crear-api-key) from your dashboard.
3. [Install the SDK](sdk/installation.md) in your application.
4. [Start querying your data](getting-started/first-steps.md) using natural language.
 
```javascript
// Example integration with JavaScript
import { CoreBrain } from 'corebrain-sdk';
 
const client = new CoreBrain({
  apiKey: 'your_api_key',
});
 
// Create a conversation
const conversation = await client.conversations.create({
  title: 'Sales analysis'
});
 
// Send a message and receive a response
const response = await client.messages.send({
  conversationId: conversation.id,
  content: 'What were our total sales last month?'
});
 
console.log(response.aiResponse.content);
```
 
## Next Steps
 
- Explore the [API documentation](api-reference/authentication.md)  
- Learn how to [configure permissions](security/permissions.md)  
- Discover [integration examples](integration/examples.md)  
- Join our [developer community](https://discord.gg/corebrain)  
 
## License
 
CoreBrain is distributed under the [MIT License](https://opensource.org/licenses/MIT).
 
---
 
Have a question? [Contact us](mailto:support@corebrain.ai) or check our [FAQ](faq.md).