"""Keep request audit records while removing public-share bearer URLs."""

import logging
import re
import traceback


def _encoded_path(route: str) -> str:
    return ''.join(f'(?:{re.escape(character)}|%{ord(character):02x})' for character in route)


SHARE_ROUTES = '(?:' + '|'.join(_encoded_path(route) for route in ('/api/shared/messages/', '/share/')) + ')'
SHARE_PATH = re.compile(SHARE_ROUTES, re.IGNORECASE)
SHARE_TEXT = re.compile('(' + SHARE_ROUTES + r')[^\s\"\'<>]*', re.IGNORECASE)
REDACTED = '[REDACTED]'
UVICORN_ACCESS_FIELDS = 5


class ShareTokenLogFilter(logging.Filter):
    """Supports Uvicorn's access argument tuple and its exception traceback records."""

    def filter(self, record: logging.LogRecord) -> bool:
        arguments = record.args
        if record.name == 'uvicorn.access' and isinstance(arguments, tuple) and len(arguments) == UVICORN_ACCESS_FIELDS:
            client, method, target, version, status = arguments
            if isinstance(target, str):
                path = target.partition('?')[0]
                private = SHARE_PATH.search(path)
                if private is not None:
                    record.args = (client, method, path[: private.end()] + REDACTED, version, status)
        else:
            record.msg = SHARE_TEXT.sub(r'\1' + REDACTED, record.getMessage())
            record.args = ()
        if record.exc_info:
            # The protocol logs escaped application exceptions after the HTTP error handler.
            rendered = ''.join(traceback.format_exception(*record.exc_info))
            record.exc_text = SHARE_TEXT.sub(r'\1' + REDACTED, rendered)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = SHARE_TEXT.sub(r'\1' + REDACTED, record.exc_text)
        return True


def uvicorn_log_config() -> dict[str, object]:
    """The CLI and programmatic ASGI servers must pass this as Uvicorn's log_config."""
    return {
        'version': 1,
        'disable_existing_loggers': False,
        'filters': {'share_tokens': {'()': ShareTokenLogFilter}},
        'formatters': {
            'default': {
                '()': 'uvicorn.logging.DefaultFormatter',
                'fmt': '%(levelprefix)s %(message)s',
                'use_colors': None,
            },
            'access': {
                '()': 'uvicorn.logging.AccessFormatter',
                'fmt': '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            },
        },
        'handlers': {
            'default': {
                'formatter': 'default',
                'filters': ['share_tokens'],
                'class': 'logging.StreamHandler',
                'stream': 'ext://sys.stderr',
            },
            'access': {
                'formatter': 'access',
                'filters': ['share_tokens'],
                'class': 'logging.StreamHandler',
                'stream': 'ext://sys.stdout',
            },
        },
        'loggers': {
            'uvicorn': {'handlers': ['default'], 'level': 'INFO', 'propagate': False},
            'uvicorn.error': {'level': 'INFO'},
            'uvicorn.access': {'handlers': ['access'], 'level': 'INFO', 'propagate': False},
        },
    }
