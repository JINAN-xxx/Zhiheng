"""Financial facts only; no invocation input, output or provider secrets."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from zhiheng.modules.billing.contracts import CreditLedgerEntry, CreditReservation, CreditValue


class InvocationReference(CreditValue):
    run_id: UUID
    source: str
    status: Literal['pending', 'unknown', 'known']
    created_at: datetime


class InvocationDetail(InvocationReference):
    id: UUID
    version: int
    user_id: UUID
    model_id: UUID
    operation: str
    application_id: UUID | None
    key_id: UUID | None
    points_enforced: bool
    reserved_tokens: int
    input_tokens: int | None
    output_tokens: int | None
    charged_tokens: int | None
    cache_tokens: int | None
    reasoning_tokens: int | None
    search_units: int | None
    currency: str | None
    reserved_cost: Decimal | None
    billable_cost: Decimal | None
    rate_card_id: UUID | None
    rate_version: int | None


class BillingTrace(CreditValue):
    invocation: InvocationDetail
    reservation: CreditReservation | None
    ledger: list[CreditLedgerEntry]
    ledger_total: int
    ledger_page: int
    ledger_page_size: int
