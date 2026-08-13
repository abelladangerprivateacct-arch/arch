from werkzeug.security import generate_password_hash
from werkzeug.security import check_password_hash


def hash_pin(pin):
    return generate_password_hash(
        pin,
        method="scrypt",
    )


def verify_pin(pin, pin_hash):
    return check_password_hash(
        pin_hash,
        pin,
    )