## Analytics

CoreBrain provides endpoints to retrieve usage and performance statistics.

## Endpoints

### Get usage statistics

```
GET /api/analytics/usage
```

**Query parameters:**
- `days` - Number of days to analyze (default: 30)
- `group_by` - Group by 'day', 'week', or 'month' (default: 'day')

**Headers:**
```
X-API-Key: tu_api_key_aquí
```

**Successful response (200):**
```json
{
  "start_date": "2025-02-21T00:00:00Z",
  "end_date": "2025-03-23T00:00:00Z",
  "group_by": "day",
  "stats": [
    {
      "date": "2025-03-01",
      "total": 256,
      "events": {
        "message_processed": 187,
        "nl_query_processed": 69
      }
    },
    {
      "date": "2025-03-02",
      "total": 312,
      "events": {
        "message_processed": 245,
        "nl_query_processed": 67
      }
    }
  ]
}
```

### Get cost statistics

```
GET /api/analytics/costs
```

**Query parameters:**
- `days` - Number of days to analyze (default: 30)
- `group_by` - Group by 'day', 'week', or 'month' (default: 'day')

**Headers:**
```
X-API-Key: tu_api_key_aquí
```

**Successful response (200):**
```json
{
  "start_date": "2025-02-21T00:00:00Z",
  "end_date": "2025-03-23T00:00:00Z",
  "group_by": "day",
  "total_usd": 45.67,
  "costs": [
    {
      "date": "2025-03-01",
      "tokens": {
        "input": 125670,
        "output": 74320,
        "total": 199990
      },
      "usd": {
        "input": 1.88,
        "output": 5.57,
        "total": 7.45
      }
    },
    {
      "date": "2025-03-02",
      "tokens": {
        "input": 145230,
        "output": 82450,
        "total": 227680
      },
      "usd": {
        "input": 2.18,
        "output": 6.18,
        "total": 8.36
      }
    }
  ]
}
```

### Get top queries

```
GET /api/analytics/top-queries
```

**Query parameters:**
- `limit` - Maximum number of queries to return (default: 10)
- `days` - Number of days to analyze (default: 7)

**Headers:**
```
X-API-Key: tu_api_key_aquí
```

**Successful response (200):**
```json
[
  {
    "query": "What are the best-selling products?",
    "count": 45,
    "collections": ["products"],
    "last_used": "2025-03-23T10:15:30Z"
  },
  {
    "query": "Who are our best customers?",
    "count": 32,
    "collections": ["customers", "orders"],
    "last_used": "2025-03-22T15:45:12Z"
  }
]
```