"""Export public API schema fixtures without starting application services."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from pydantic import SecretStr

from zhiheng.api.main import create_app
from zhiheng.bootstrap.settings import Settings

SCHEMAS = ('resources-v1.json', 'inference-v1.json')


async def schema_documents() -> dict[str, object]:
    settings = Settings(
        database_url=SecretStr('postgresql+asyncpg://schema:unused@127.0.0.1:1/schema'),
        redis_url=SecretStr('redis://127.0.0.1:1/0'),
        credential_key=SecretStr(Fernet.generate_key().decode()),
    )
    app = create_app(settings)
    documents = {}
    # ASGITransport does not enter lifespan, construct a container, or open network connections.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://schema.test') as client:
        for name in SCHEMAS:
            response = await client.get('/openapi/' + name)
            response.raise_for_status()
            documents[name] = response.json()
    return documents


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    documents = asyncio.run(schema_documents())
    args.output.mkdir(parents=True, exist_ok=True)
    for name, document in documents.items():
        (args.output / name).write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n')
    print(f'Public API schema fixtures exported: {args.output}')


if __name__ == '__main__':
    main()
