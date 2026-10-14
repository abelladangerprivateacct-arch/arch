import jwt
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"

# This payload is enough for testing; no admin record needed
payload = {
    "user_id": "test_admin_user",  # just a placeholder, can be anything
    "is_admin": True,
    "exp": datetime.utcnow() + timedelta(days=1)  # valid for 1 day
}

token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
print("Admin JWT token:", token)
