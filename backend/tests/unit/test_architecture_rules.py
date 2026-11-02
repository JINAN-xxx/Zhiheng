import importlib.util
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

CHECKER_PATH = Path(__file__).resolve().parents[3] / 'tools/check_architecture.py'
NESTED_IMPORT_LINE = 2


@pytest.fixture
def checker() -> ModuleType:
    assert CHECKER_PATH.is_file(), 'The architecture dependency checker has not been implemented'
    spec = importlib.util.spec_from_file_location('architecture_rules_under_test', CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path: Path) -> Callable[[str, str], Path]:
    root = tmp_path / 'zhiheng'

    def write(relative: str, content: str) -> Path:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding='utf-8')
        return root

    return write


def test_allows_public_interfaces_and_same_module_internals(checker: ModuleType, source: Callable) -> None:
    root = source(
        'modules/chat/service.py',
        'from zhiheng.modules.models.public import ModelInvoker\nfrom .repository import ChatRepository\n',
    )

    assert checker.check_boundaries(root) == []


def test_rejects_private_import_inside_a_function(checker: ModuleType, source: Callable) -> None:
    root = source(
        'modules/chat/service.py',
        'def send():\n    from zhiheng.modules.models.repository import ModelRepository\n',
    )

    violations = checker.check_boundaries(root)

    assert len(violations) == 1
    assert violations[0].rule == 'module-private-import'
    assert violations[0].line == NESTED_IMPORT_LINE


def test_rejects_relative_private_import_from_another_module(checker: ModuleType, source: Callable) -> None:
    root = source('modules/chat/service.py', 'from ..models import repository\n')

    assert [item.rule for item in checker.check_boundaries(root)] == ['module-private-import']


def test_allows_relative_public_import_from_another_module(checker: ModuleType, source: Callable) -> None:
    root = source('modules/chat/service.py', 'from ..models import public\n')

    assert checker.check_boundaries(root) == []


def test_parent_namespace_cannot_expose_another_module(checker: ModuleType, source: Callable) -> None:
    root = source('modules/chat/service.py', 'from zhiheng.modules import models\n')

    assert [item.rule for item in checker.check_boundaries(root)] == ['module-private-import']


@pytest.mark.parametrize(
    'statement',
    ['from zhiheng.modules.identity.public import Principal', 'from zhiheng import modules', 'import sqlalchemy'],
)
def test_shared_layer_cannot_depend_on_business_or_frameworks(
    checker: ModuleType, source: Callable, statement: str
) -> None:
    root = source('shared/context.py', statement + '\n')

    assert [item.rule for item in checker.check_boundaries(root)] == ['shared-dependency']


@pytest.mark.parametrize(
    'statement',
    [
        'from zhiheng.application.inference import invoke',
        'from zhiheng import application',
        'from ... import application',
    ],
)
def test_business_module_cannot_import_application(checker: ModuleType, source: Callable, statement: str) -> None:
    root = source('modules/chat/service.py', statement + '\n')

    assert [item.rule for item in checker.check_boundaries(root)] == ['module-layer-inversion']


@pytest.mark.parametrize(
    'statement', ['from zhiheng.modules.work.public import WorkRun', 'from zhiheng import modules']
)
def test_runtime_cannot_depend_on_business_modules(checker: ModuleType, source: Callable, statement: str) -> None:
    root = source('runtime/leases.py', statement + '\n')

    assert [item.rule for item in checker.check_boundaries(root)] == ['runtime-business-dependency']


@pytest.mark.parametrize(
    'statement',
    [
        'from config.env import AppConfig',
        'from plugins.ai.service import model_service',
        'from legacy.backend import app',
    ],
)
def test_new_core_cannot_import_legacy_runtime(checker: ModuleType, source: Callable, statement: str) -> None:
    root = source('bootstrap/dependencies.py', statement + '\n')

    assert [item.rule for item in checker.check_boundaries(root)] == ['legacy-runtime-import']


@pytest.mark.parametrize(
    'statement', ['from langchain_openai import ChatOpenAI', 'from google import genai', 'import groq']
)
def test_supplier_sdk_is_restricted_to_adapters(checker: ModuleType, source: Callable, statement: str) -> None:
    root = source('modules/models/service.py', statement + '\n')

    assert [item.rule for item in checker.check_boundaries(root)] == ['sdk-outside-adapter']


def test_supplier_sdk_is_allowed_inside_an_adapter(checker: ModuleType, source: Callable) -> None:
    root = source('modules/models/adapters/openai.py', 'from langchain_openai import ChatOpenAI\n')

    assert checker.check_boundaries(root) == []


@pytest.mark.parametrize(
    'statement',
    [
        "import importlib as loader\nloader.import_module('zhiheng.modules.models.repository')",
        "from importlib import import_module as load\nload('zhiheng.modules.models.repository')",
        "import importlib\nload = importlib.import_module\nload('zhiheng.modules.models.repository')",
        "import importlib\nloader = importlib\nloader.import_module('zhiheng.modules.models.repository')",
        "from importlib import import_module\nload = import_module\nload('zhiheng.modules.models.repository')",
        "__import__('zhiheng.modules.models.repository')",
    ],
)
def test_literal_dynamic_import_cannot_bypass_boundaries(checker: ModuleType, source: Callable, statement: str) -> None:
    root = source('modules/chat/service.py', statement + '\n')

    assert [item.rule for item in checker.check_boundaries(root)] == ['module-private-import']


def test_unresolved_dynamic_import_is_reported(checker: ModuleType, source: Callable) -> None:
    root = source('modules/chat/service.py', 'import importlib\nimportlib.import_module(module_name)\n')

    assert [item.rule for item in checker.check_boundaries(root)] == ['unresolved-dynamic-import']


def test_cli_exits_nonzero_and_reports_source_line(source: Callable) -> None:
    assert CHECKER_PATH.is_file(), 'The architecture dependency checker has not been implemented'
    root = source('modules/chat/service.py', 'from zhiheng.modules.models.repository import ModelRepository\n')

    result = subprocess.run(
        [sys.executable, '-B', str(CHECKER_PATH), str(root)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert 'service.py:1' in result.stdout
    assert 'module-private-import' in result.stdout


@pytest.mark.parametrize(
    'statement',
    [
        'from sqlalchemy import select',
        'def query():\n    from sqlalchemy.orm import Session',
        'import importlib\nprovider = importlib.import_module("openai")',
        'from zhiheng.infrastructure.database import Base',
    ],
)
def test_resource_protocol_cannot_bypass_application_data_or_provider_boundaries(
    checker: ModuleType, source: Callable, statement: str
) -> None:
    root = source('api/resources/knowledge.py', statement + '\n')
    assert checker.check_boundaries(root), 'Resource HTTP adapters must not implement persistence or supplier calls'


def test_resource_protocol_uses_public_domain_contract_and_application_use_case(
    checker: ModuleType, source: Callable
) -> None:
    root = source(
        'api/resources/knowledge.py',
        'from zhiheng.modules.knowledge.public import BaseInfo\nfrom zhiheng.application.access import CallerContext\n',
    )
    assert checker.check_boundaries(root) == []
