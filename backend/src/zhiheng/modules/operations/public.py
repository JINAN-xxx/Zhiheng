"""Credential-free operations use cases, DTOs and explicit migration inventory."""

from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.operations.contracts import (
    ConfigInfo,
    CreateConfig,
    CreateDictionary,
    CreateDictionaryItem,
    CreateNotice,
    DictionaryInfo,
    DictionaryItemInfo,
    LogInfo,
    LogKind,
    LogOutcome,
    NoticeInfo,
    NoticeKind,
    NoticeStatus,
    Page,
    PatchConfig,
    PatchDictionary,
    PatchDictionaryItem,
    PatchNotice,
)
from zhiheng.modules.operations.orm import Config, Dictionary, DictionaryItem, Notice, OperationLog
from zhiheng.modules.operations.service import OperationsService

__all__ = [
    'ConfigInfo',
    'CreateConfig',
    'CreateDictionary',
    'CreateDictionaryItem',
    'CreateNotice',
    'DictionaryInfo',
    'DictionaryItemInfo',
    'LogInfo',
    'LogKind',
    'LogOutcome',
    'NoticeInfo',
    'NoticeKind',
    'NoticeStatus',
    'OperationsService',
    'Page',
    'PatchConfig',
    'PatchDictionary',
    'PatchDictionaryItem',
    'PatchNotice',
    'schema_tables',
]


def schema_tables() -> tuple[Table, ...]:
    """Declaration inventory for explicit migrations; application startup never creates tables."""
    return tuple(
        Base.metadata.tables[model.__tablename__]
        for model in (Dictionary, DictionaryItem, Config, Notice, OperationLog)
    )
