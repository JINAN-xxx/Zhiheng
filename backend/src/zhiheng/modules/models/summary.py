"""Resolve summary budgets from configured model capacity and explicit overrides."""

from dataclasses import dataclass

from zhiheng.modules.models.capacity import input_budget_for
from zhiheng.shared.agent import SUMMARY_MESSAGE_OVERHEAD


@dataclass(frozen=True)
class SummaryBudget:
    trigger_tokens: int
    keep_tokens: int
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class SummaryPolicy:
    trigger_tokens: int | None = None
    keep_tokens: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None

    def resolve(
        self,
        *,
        input_budget: int | None,
        model_window_tokens: int | None,
        model_max_output_tokens: int,
        prefix_tokens: int,
        current_tokens: int,
    ) -> SummaryBudget:
        message_budget = max(2, input_budget - prefix_tokens) if input_budget is not None else None
        output = min(
            self.output_tokens if self.output_tokens is not None else 4096 if input_budget is not None else 1024,
            model_max_output_tokens,
        )
        if input_budget is not None:
            output = min(output, max(1, input_budget - prefix_tokens - current_tokens - SUMMARY_MESSAGE_OVERHEAD))
        # A large trigger must have a matching summary input budget. The actual
        # summary model window and an explicit administrator cap remain binding.
        summary_input = input_budget_for(
            input_budget=(
                self.input_tokens
                if self.input_tokens is not None
                else input_budget
                if input_budget is not None
                else 65536
            ),
            context_window_tokens=model_window_tokens,
            max_output_tokens=output,
        )
        assert summary_input is not None
        summary_input = max(1, summary_input)
        capacity_trigger = max(2, message_budget * 7 // 10) if message_budget is not None else None
        trigger = self.trigger_tokens if self.trigger_tokens is not None else capacity_trigger or 16000
        trigger = min(trigger, max(2, summary_input * 7 // 10))
        if capacity_trigger is not None:
            trigger = min(trigger, capacity_trigger)
        keep = (
            self.keep_tokens
            if self.keep_tokens is not None
            else max(1, message_budget // 5)
            if message_budget is not None
            else 5000
        )
        keep = min(keep, max(1, trigger // 3))
        return SummaryBudget(trigger, keep, summary_input, output)
