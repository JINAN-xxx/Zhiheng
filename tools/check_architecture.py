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
        if not same_module and not public_interface:
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
