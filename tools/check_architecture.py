"""Check dependency directions without importing application or supplier code."""

import argparse
import ast
import sys
from dataclasses import dataclass
from importlib.util import resolve_name
from pathlib import Path

PACKAGE = 'zhiheng'
DOMAIN_PATH_DEPTH = 3
PUBLIC_PATH_DEPTH = 4
MIGRATION_SYMBOL_DEPTH = 5
LEGACY_ROOTS = frozenset({'config', 'plugins', 'common', 'utils', 'exceptions', 'server', 'legacy'})
FRAMEWORK_ROOTS = frozenset({'fastapi', 'starlette', 'sqlalchemy', 'arq'})
SUPPLIER_ROOTS = frozenset(
    {
        'openai',
        'anthropic',
        'cohere',
        'groq',
        'mistralai',
        'ollama',
        'cerebras',
        'llama_api_client',
        'portkey_ai',
        'litellm',
        'langgraph',
        'lightrag',
        'pypdf',
        'pymilvus',
        'grpc',
        'PIL',
        'openpyxl',
    }
)
INVERTED_LAYERS = frozenset({'api', 'worker', 'bootstrap', 'application', 'cli'})


@dataclass(frozen=True)
class ArchitectureViolation:
    path: Path
    line: int
    rule: str
    target: str


def _supplier_import(target: str) -> bool:
    root = target.split('.', maxsplit=1)[0]
    return (
        root in SUPPLIER_ROOTS
        or root == 'langchain'
        or root.startswith('langchain_')
        or target == 'google.genai'
        or target.startswith(('google.genai.', 'google.generativeai'))
    )


def _rule(source: str, target: str) -> str | None:
    origin = source.split('.')
    destination = target.split('.')
    root = destination[0]
    if (
        origin[-1] == 'public'
        and destination[:3] == origin[:3]
        and any(part == 'orm' or part.endswith('_orm') for part in destination[3:])
    ):
        return 'public-persistence-import'
    if origin[:3] == [PACKAGE, 'api', 'resources'] and (
        root == 'sqlalchemy' or destination[:2] == [PACKAGE, 'infrastructure']
    ):
        return 'resource-protocol-persistence'
    if root in LEGACY_ROOTS or root.startswith('module_'):
        return 'legacy-runtime-import'
    if origin[:2] == [PACKAGE, 'shared']:
        business_dependency = destination[:1] == [PACKAGE] and destination[1:2] not in (
            [],
            ['shared'],
        )
        if business_dependency or root in FRAMEWORK_ROOTS or _supplier_import(target):
            return 'shared-dependency'
    if origin[:2] == [PACKAGE, 'runtime'] and destination[:2] == [PACKAGE, 'modules']:
        return 'runtime-business-dependency'
    if (
        origin[:2] == [PACKAGE, 'modules']
        and destination[:1] == [PACKAGE]
        and len(destination) > 1
        and destination[1] in INVERTED_LAYERS
    ):
        return 'module-layer-inversion'
    if destination[:2] == [PACKAGE, 'modules'] and len(destination) >= DOMAIN_PATH_DEPTH:
        same_module = origin[:DOMAIN_PATH_DEPTH] == destination[:DOMAIN_PATH_DEPTH]
        public_interface = len(destination) >= PUBLIC_PATH_DEPTH and destination[DOMAIN_PATH_DEPTH] == 'public'
        migration_inventory = (
            source == PACKAGE + '.bootstrap.schema'
            and len(destination) == MIGRATION_SYMBOL_DEPTH
            and destination[3:] == ['schema', 'schema_tables']
        )
        adapter_constructor = source == PACKAGE + '.bootstrap.adapters' and target in {
            PACKAGE + '.modules.knowledge.adapters.native.NativeParser',
            PACKAGE + '.modules.knowledge.adapters.native.validate_document_source',
            PACKAGE + '.modules.knowledge.adapters.mineru.MinerUClient',
            PACKAGE + '.modules.knowledge.adapters.mineru_cloud.create_mineru_client',
            PACKAGE + '.modules.knowledge.adapters.mineru.unpack_result',
            PACKAGE + '.modules.knowledge.adapters.milvus.MilvusStore',
            PACKAGE + '.modules.knowledge.adapters.lightrag.sdk.GraphSDK',
            PACKAGE + '.modules.models.adapters.factory.create_provider',
        }
        if not same_module and not public_interface and not migration_inventory and not adapter_constructor:
            return 'module-private-import'
    if root in {'pypdf', 'PIL', 'openpyxl'} and origin[:4] != [PACKAGE, 'modules', 'knowledge', 'adapters']:
        return 'parser-sdk-outside-owning-adapter'
    if _supplier_import(target) and 'adapters' not in origin[DOMAIN_PATH_DEPTH:]:
        return 'sdk-outside-adapter'
    return None


def _module_name(source_root: Path, path: Path) -> tuple[str, str]:
    relative = path.relative_to(source_root).with_suffix('')
    parts = list(relative.parts)
    if parts[-1] == '__init__':
        parts.pop()
        module = '.'.join([PACKAGE, *parts])
        return module, module
    module = '.'.join([PACKAGE, *parts])
    return module, module.rpartition('.')[0]


def _from_targets(node: ast.ImportFrom, package: str) -> list[str]:
    if node.level:
        try:
            target = resolve_name('.' * node.level + (node.module or ''), package)
        except (ImportError, ValueError):
            return ['<invalid-relative-import>']
    else:
        target = node.module or ''
    if target == PACKAGE or target.startswith(PACKAGE + '.') or target == 'google':
        return [target + '.' + alias.name for alias in node.names]
    return [target]


def _importer_reference(function: ast.AST, modules: set[str], functions: set[str]) -> bool:
    if isinstance(function, ast.Name):
        return function.id in functions
    return (
        isinstance(function, ast.Attribute)
        and function.attr == 'import_module'
        and isinstance(function.value, ast.Name)
        and function.value.id in modules
    )


def _dynamic_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    modules = {'importlib'}
    functions = {'__import__'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.asname or alias.name for alias in node.names if alias.name == 'importlib')
        elif isinstance(node, ast.ImportFrom) and node.module == 'importlib':
            functions.update(alias.asname or alias.name for alias in node.names if alias.name == 'import_module')
    while True:
        module_aliases = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id in modules
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        aliases = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and _importer_reference(node.value, modules, functions)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        if aliases <= functions and module_aliases <= modules:
            break
        functions.update(aliases)
        modules.update(module_aliases)
    return modules, functions


def _is_dynamic_call(node: ast.Call, modules: set[str], functions: set[str]) -> bool:
    return _importer_reference(node.func, modules, functions)


def _structural_violations(source: str, path: Path, node: ast.AST) -> list[ArchitectureViolation]:
    violations = []
    if (
        source == PACKAGE + '.bootstrap.container'
        and isinstance(node, ast.AsyncWith)
        and any('.database.sessions' in ast.unparse(item.context_expr) for item in node.items)
    ):
        violations.append(
            ArchitectureViolation(path, node.lineno, 'bootstrap-business-transaction', 'database.sessions')
        )
    if (
        source.startswith(PACKAGE + '.api.')
        and isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {'fail', 'create', 'request_cancel', 'release', 'completed', 'publish'}
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == 'store'
    ):
        violations.append(ArchitectureViolation(path, node.lineno, 'transport-domain-state-mutation', node.func.attr))
    if (
        source.startswith(PACKAGE + '.runtime.')
        and isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == 'payload'
        and isinstance(node.slice, ast.Constant)
        and node.slice.value in {'binding', 'skill_slugs', 'skills', 'agent_id'}
    ):
        violations.append(ArchitectureViolation(path, node.lineno, 'runtime-domain-payload', str(node.slice.value)))
    if source.endswith('.public') and (
        (isinstance(node, ast.FunctionDef) and node.name == 'schema_tables')
        or (isinstance(node, ast.ImportFrom) and any(alias.name == 'schema_tables' for alias in node.names))
    ):
        violations.append(ArchitectureViolation(path, node.lineno, 'public-migration-inventory', 'schema_tables'))
    if (
        source.startswith(PACKAGE + '.api.')
        and isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {'put', 'delete'}
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == 'storage'
    ):
        violations.append(ArchitectureViolation(path, node.lineno, 'transport-storage-mutation', node.func.attr))
    return violations


def _file_violations(source_root: Path, path: Path) -> list[ArchitectureViolation]:
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    except (SyntaxError, UnicodeError) as error:
        return [ArchitectureViolation(path, getattr(error, 'lineno', None) or 1, 'invalid-source', str(error))]
    source, package = _module_name(source_root, path)
    modules, functions = _dynamic_names(tree)
    violations = []
    for node in ast.walk(tree):
        targets: list[str] = []
        violations.extend(_structural_violations(source, path, node))
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            targets = _from_targets(node, package)
        elif isinstance(node, ast.Call) and _is_dynamic_call(node, modules, functions):
            argument = node.args[0] if node.args else None
            if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                violations.append(ArchitectureViolation(path, node.lineno, 'unresolved-dynamic-import', '<dynamic>'))
                continue
            target = argument.value
            if target.startswith('.'):
                violations.append(ArchitectureViolation(path, node.lineno, 'unresolved-dynamic-import', target))
                continue
            targets = [target]
        else:
            continue
        for target in targets:
            rule = 'invalid-relative-import' if target == '<invalid-relative-import>' else _rule(source, target)
            if rule is not None:
                violations.append(ArchitectureViolation(path, node.lineno, rule, target))
    return violations


def check_boundaries(source_root: Path) -> list[ArchitectureViolation]:
    """Return every prohibited import, including imports nested in functions."""
    if not source_root.is_dir():
        raise ValueError(f'Core source directory does not exist: {source_root}')
    return [
        violation for path in sorted(source_root.rglob('*.py')) for violation in _file_violations(source_root, path)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_root', type=Path)
    args = parser.parse_args()
    try:
        violations = check_boundaries(args.source_root)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    for violation in violations:
        print(f'{violation.path}:{violation.line}: {violation.rule}: {violation.target}')
    if not violations:
        print(f'Architecture boundaries passed: {args.source_root}')
    return int(bool(violations))


if __name__ == '__main__':
    raise SystemExit(main())
