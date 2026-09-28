import pytest
from fastapi import FastAPI

from config.env import AppConfig
from utils.server_util import APIDocsUtil


@pytest.mark.asyncio
async def test_redoc_uses_proxy_safe_relative_openapi_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AppConfig, 'app_disable_redoc', False)
    app = FastAPI(title='Docs Test')

    response = await APIDocsUtil._custom_redoc(app, 'https://example.com/redoc.js', 'https://example.com/favicon.png')

    assert 'spec-url="./openapi.json"' in response.body.decode()


@pytest.mark.asyncio
async def test_swagger_uses_proxy_safe_relative_openapi_and_oauth_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AppConfig, 'app_disable_swagger', False)
    app = FastAPI(title='Docs Test')

    response = await APIDocsUtil._custom_swagger(
        app,
        'https://example.com/swagger.js',
        'https://example.com/swagger.css',
        'https://example.com/favicon.png',
    )

    body = response.body.decode()
    assert "url: './openapi.json'" in body
    assert "oauth2RedirectUrl: window.location.origin + './docs/oauth2-redirect'" in body
