import base64
import hashlib
import secrets

from cryptography.fernet import Fernet


def _derive_fernet_key(passphrase: str) -> bytes:
    digest = hashlib.sha256(passphrase.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def decrypt_signing_key(encrypted_blob: str, passphrase: str) -> str:
    fernet = Fernet(_derive_fernet_key(passphrase))
    return fernet.decrypt(encrypted_blob.encode("utf-8")).decode("utf-8")


def encrypt_signing_key(raw_key: str, passphrase: str) -> str:
    fernet = Fernet(_derive_fernet_key(passphrase))
    return fernet.encrypt(raw_key.encode("utf-8")).decode("utf-8")


def generate_raw_signing_key() -> str:
    return secrets.token_urlsafe(48)


def rotate_signing_key(passphrase: str) -> tuple[str, str]:
    raw_key = generate_raw_signing_key()
    encrypted_key = encrypt_signing_key(raw_key, passphrase)
    return raw_key, encrypted_key
