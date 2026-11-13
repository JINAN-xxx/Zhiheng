"""Execution policy belongs to application use cases, outside process entrypoints."""

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / 'src/zhiheng'


def test_execution_use_cases_have_one_application_home() -> None:
    expected = ('admission', 'authorization', 'executor', 'lifecycle', 'recovery')
    assert all((SOURCE / 'application/execution' / f'{name}.py').is_file() for name in expected)
    assert not (SOURCE / 'application/inference.py').exists()
    assert not (SOURCE / 'application/lifecycle.py').exists()


def test_process_entrypoints_only_assemble_and_trigger_execution() -> None:
    container = ast.parse((SOURCE / 'bootstrap/container.py').read_text())
    worker = ast.parse((SOURCE / 'worker/main.py').read_text())
    assert not any(
        isinstance(node, ast.AsyncFunctionDef) and node.name == 'require_model_execution'
        for node in ast.walk(container)
    ), 'Execution authorization must be an application use case'
    assert not any(
        isinstance(node, ast.AsyncFunctionDef) and node.name == 'recover_expired' for node in ast.walk(worker)
    ), 'Recovery transactions must be owned by the application use case'


def test_chat_context_depends_on_public_execution_interfaces() -> None:
    source = (SOURCE / 'application/chat/context.py').read_text()
    assert 'ChatRunService' not in source, 'Chat admission must depend on context in one direction'
    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.Attribute)
        and node.attr.startswith('_')
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == 'admission'
        for node in ast.walk(tree)
    ), 'Context must receive its own run and usage interfaces'
