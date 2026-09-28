class AgentServiceError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = (
            status_code
            if status_code is not None
            else (404 if code == 'AGENT_NOT_FOUND' else 409 if 'CONFLICT' in code else 422)
        )

    def public_dict(self) -> dict[str, str]:
        return {'code': self.code, 'message': self.message}
