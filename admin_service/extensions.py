from flask_bcrypt import Bcrypt
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from config import Config


bcrypt = Bcrypt()

cors = CORS()

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=Config.RATELIMIT_STORAGE_URI,
)