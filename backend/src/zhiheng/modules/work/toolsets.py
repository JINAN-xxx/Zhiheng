"""Named tool groups; loading a group never grants execution permission."""

from types import MappingProxyType

WORK_CODE_TOOLS = frozenset({'run_python', 'execute', 'write_file', 'read_file', 'list_files', 'publish_outputs'})
WORK_CORE_TOOLS = frozenset(
    {
        'list_inputs',
        'read_document',
        'read_sheet',
        'load_skill',
        'request_input',
        'write_todos',
        'web_search',
        'load_tools',
    }
)
WORK_TOOLSETS = MappingProxyType(
    {
        'inspection': ('inspect_file', 'read_history'),
        'python': ('run_python', 'list_files', 'read_file', 'write_file', 'publish_outputs'),
        'shell': ('execute', 'list_files', 'read_file', 'write_file', 'publish_outputs'),
        'files': ('list_files', 'read_file', 'write_file', 'publish_outputs'),
    }
)
WORK_CODE_TOOLSETS = frozenset({'python', 'shell', 'files'})
