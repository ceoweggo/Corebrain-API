#!/bin/bash

# Script to install all necessary dependencies
# Make sure you have Python and pip installed

echo "🚀 Installing project dependencies..."
echo "================================================"

# Update pip
echo "📦 Updating pip..."
python -m pip install --upgrade pip

# Install all libraries in the specified order
echo "📚 Installing libraries in order..."

pip install uvicorn
pip install fastapi
pip install openai
pip install anthropic
pip install python-dotenv
pip install pydantic[email]
pip install python-jose
pip install passlib
pip install redis
pip install motor
pip install requests
pip install pymongo
pip install python-multipart
pip install langdetect
pip install bcrypt

echo "✅ Installation completed!"
echo "================================================"
echo "📋 Libraries installed (in order):"
echo "   1. uvicorn        - ASGI server"
echo "   2. fastapi        - Web framework"
echo "   3. openai         - OpenAI client"
echo "   4. anthropic      - Anthropic client"
echo "   5. python-dotenv  - Environment variables"
echo "   6. pydantic[email] - Validation with email support"
echo "   7. python-jose    - JWT tokens"
echo "   8. passlib        - Password hashing"
echo "   9. redis          - Redis client"
echo "  10. motor          - Async MongoDB driver"
echo "  11. requests       - HTTP client"
echo "  12. pymongo        - Sync MongoDB driver"
echo "  13. python-multipart - Form handling"
echo "  14. langdetect     - Language detection"
echo "  15. bcrypt     - Encryption"
echo ""
echo "📝 Library descriptions:"
echo "   • uvicorn: ASGI server for running FastAPI applications"
echo "   • fastapi: Modern, fast web framework for building APIs with Python"
echo "   • openai: Official OpenAI Python client library"
echo "   • anthropic: Official Anthropic Python client library"
echo "   • python-dotenv: Load environment variables from .env files"
echo "   • pydantic[email]: Data validation with email validation support"
echo "   • python-jose: JSON Web Token implementation"
echo "   • passlib: Password hashing and verification library"
echo "   • redis: Python client for Redis in-memory data store"
echo "   • motor: Asynchronous MongoDB driver for Python"
echo "   • requests: Simple HTTP library for Python"
echo "   • pymongo: Synchronous MongoDB driver for Python"
echo "   • python-multipart: Handle multipart/form-data requests"
echo "   • langdetect: Language detection library"
echo "   • bcrypt: Encryption library"
echo ""
echo "🎉 Ready to start developing!"