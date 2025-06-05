# Database
 
CoreBrain allows executing natural language queries over connected MongoDB databases.
 
## Endpoints
 
### Natural Language Query
 
```
POST /api/database/query
```
 
**Headers:**
```
X-API-Key: your_api_key_here  
Content-Type: application/json
```
 
**Body:**
```json
{
  "query": "What are the top 5 best-selling products this month?",
  "collection_name": "products",
  "limit": 5,
  "metadata": {
    "context": "dashboard_sales_report"
  }
}
```
 
**Successful Response (200):**
```json
{
  "natural_query": "What are the top 5 best-selling products this month?",
  "mongo_query": {
    "collection": "products",
    "operation": "find",
    "query": {},
    "sort": {"sales": -1},
    "limit": 5
  },
  "result": {
    "data": [
      {"_id": "prod_123", "name": "Laptop Pro", "sales": 256},
      {"_id": "prod_456", "name": "Smartphone X", "sales": 187},
      {"_id": "prod_789", "name": "Tablet Ultra", "sales": 145},
      {"_id": "prod_012", "name": "Monitor 4K", "sales": 98},
      {"_id": "prod_345", "name": "BT Headphones", "sales": 87}
    ],
    "count": 5,
    "query_time_ms": 25.4,
    "has_more": true,
    "metadata": {
      "total_count": 120,
      "skip": 0,
      "limit": 5,
      "collection": "products"
    }
  },
  "explanation": "The top 5 best-selling products this month are Laptop Pro (256 units), Smartphone X (187 units), Tablet Ultra (145 units), Monitor 4K (98 units), and BT Headphones (87 units). These represent the top 5 out of a total of 120 products in the catalog.",
  "metadata": {
    "processing_time": 1.2,
    "anthropic_model": "claude-3-opus-20240229"
  }
}
```
 
### Get Database Schema
 
```
GET /api/database/collections
```
 
**Headers:**
```
X-API-Key: your_api_key_here
```
 
**Successful Response (200):**
```json
{
  "collections": {
    "products": {
      "document_count": 120,
      "schema": {
        "name": {
          "type": "string",
          "example": "Laptop Pro"
        },
        "price": {
          "type": "number",
          "example": "999.99"
        },
        "category": {
          "type": "string",
          "example": "electronics"
        },
        "sales": {
          "type": "number",
          "example": "256"
        }
      }
    },
    "customers": {
      "document_count": 5430,
      "schema": {
        "name": {
          "type": "string",
          "example": "John Doe"
        },
        "email": {
          "type": "string",
          "example": "john@example.com"
        },
        "signup_date": {
          "type": "date",
          "example": "2024-01-15T10:30:00Z"
        }
      }
    }
  }
}
```