import os

from cryptography.fernet import Fernet, InvalidToken


class CredentialConfigurationError(RuntimeError):
    pass


class CredentialDecryptionError(RuntimeError):
    pass


def credential_cipher() -> Fernet:
    raw_key = os.getenv("SPOTINOTIFS_CREDENTIAL_KEY", "").strip()
    if not raw_key:
        raise CredentialConfigurationError("Anchor credential encryption is not configured")
    try:
        return Fernet(raw_key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as error:
        raise CredentialConfigurationError("Anchor credential encryption is misconfigured") from error


def encrypt_api_key(api_key: str) -> str:
    normalized = api_key.strip()
    if not normalized:
        raise ValueError("Anchor API key is required")
    return credential_cipher().encrypt(normalized.encode("utf-8")).decode("ascii")


def decrypt_api_key(ciphertext: str) -> str:
    try:
        return credential_cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as error:
        raise CredentialDecryptionError("Stored Anchor credential could not be decrypted") from error
