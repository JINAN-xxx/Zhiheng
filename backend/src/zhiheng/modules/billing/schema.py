"""Migration-only declaration inventory owned by billing."""

from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.billing.orm import (
    CreditAccount,
    CreditCommand,
    CreditControl,
    CreditHold,
    CreditKeyMonth,
    CreditLedger,
    CreditRate,
    Invocation,
    TokenCommand,
    UsageAccount,
)


def schema_tables() -> tuple[Table, ...]:
    return tuple(
        Base.metadata.tables[model.__tablename__]
        for model in (
            UsageAccount,
            TokenCommand,
            Invocation,
            CreditAccount,
            CreditControl,
            CreditRate,
            CreditCommand,
            CreditKeyMonth,
            CreditHold,
            CreditLedger,
        )
    )
