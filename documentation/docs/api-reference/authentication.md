# CoreBrain API Documentation

## Authentication

CoreBrain API provides two authentication methods:

1. **API Keys** – For integration in client applications  
2. **JWT (JSON Web Tokens)** – For the dashboard and administrative operations

---

### API Keys

API Keys are the primary method to authenticate API requests from client applications. They are passed via the HTTP header `X-API-Key`.

#### Format

API Keys follow the format:

- `sk_live_XXXXXXXXXXXXXXXXXXXX` – For production use  
- `sk_test_XXXXXXXXXXXXXXXXXXXX` – For development and testing

#### Permission Levels

Each API Key is associated with a permission level:

| Level   | Description                                       |
|---------|---------------------------------------------------|
| `read`  | Read-only (queries)                               |
| `write` | Read + write (messages, conversations)            |
| `admin` | Full access (includes API Key management)         |

#### Endpoints

##### Validate API Key

  ```
  GET /api/auth/validate
  ```

  **Headers:**
  ```
  X-API-Key: tu_api_key_aquí
  ```

  **Successful response (200):**
  ```json
  {
    "valid": true,
    "level": "write",
    "name": "API Key for App"
  }
  ```

  #### Create API Key (requires JWT Admin authentication)

  ```
  POST /api/auth/api-keys
  ```

  **Body:**
  ```json
  {
    "name": "API Key for Mobile App",
    "user_id": "usr_123456789",
    "level": "write",
    "allowed_domains": ["miapp.com", "*.miapp.com"]
  }
  ```

  **Successful response (201):**
  ```json
  {
    "id": "key_123456789",
    "name": "API Key para App Móvil",
    "level": "write",
    "key": "sk_live_abcdefghijklmnopqrst",
    "created_at": "2025-03-23T12:34:56Z",
    "expires_at": null,
    "allowed_domains": ["miapp.com", "*.miapp.com"]
  }
  ```

  #### Revoke API Key (requires JWT Admin authentication)

  ```
  DELETE /api/auth/api-keys/{api_key_id}
  ```

  **Successful response (200):**
  ```json
  {
    "message": "API key successfully revoked"
  }
  ```

  ## JWT (JSON Web Tokens)

 JWTs are used to authenticate users in the dashboard and to perform administrative operations. They are sent via the HTTP header Authorization: Bearer {token}.

  ### Obtain token (Login)

  ```
  POST /api/auth/token
  ```

  **Body** (form-urlencoded):
  ```
  username=usuario@ejemplo.com&password=contraseña_secreta
  ```

  **Successful response (200):**
  ```json
  {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "bearer",
    "user_id": "usr_123456789",
    "email": "usuario@ejemplo.com",
    "name": "Nombre del Usuario",
    "role": "admin"
  }
  ```

  ### Create user (requires JWT Admin authentication)

  ```
  POST /api/auth/users
  ```

  **Body:**
  ```json
  {
    "email": "nuevo@usuario.com",
    "name": "Nuevo Usuario",
    "password": "contraseña_segura"
  }
  ```

  **Successful response (201):**
  ```json
  {
    "id": "usr_987654321",
    "email": "nuevo@usuario.com",
    "name": "Nuevo Usuario",
    "created_at": "2025-03-23T12:34:56Z",
    "role": "user"
  }
  ```

  ## Authentication Error Handling

  ### Invalid API Key

  **Response (401):**
  ```json
  {
    "detail": "Invalid or expired API key"
  }
  ```

  ### Invalid or expired JWT

  **Response (401):**
  ```json
  {
    "detail": "Could not validate credentials",
    "headers": {
      "WWW-Authenticate": "Bearer"
    }
  }
  ```

  ### Insufficient permissions

  **Response (403):**
  ```json
  {
    "detail": "You do not have sufficient permissions for this operation"
  }
  ```