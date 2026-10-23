from http import HTTPStatus


def error_payload(code: str, message: str, status: int) -> dict[str, object]:
    kind = (
        'authentication_error'
        if status == HTTPStatus.UNAUTHORIZED
        else 'rate_limit_error'
        if status == HTTPStatus.TOO_MANY_REQUESTS
        else 'invalid_request_error'
        if status < HTTPStatus.INTERNAL_SERVER_ERROR
        else 'server_error'
    )
    return {'error': {'message': message, 'type': kind, 'param': None, 'code': code}}
