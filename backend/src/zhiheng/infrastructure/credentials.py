from cryptography.fernet import Fernet, InvalidToken

from zhiheng.shared.errors import AppError


class CredentialCipher:
    def __init__(self, key: str) -> None:
        self._cipher = Fernet(key.encode())

    def encrypt(self, value: str) -> str:
        return self._cipher.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._cipher.decrypt(value.encode()).decode()
        except (InvalidToken, UnicodeError) as exc:
            raise AppError('credential_unavailable', '无法读取模型连接凭据', 503) from exc
