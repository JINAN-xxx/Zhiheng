"""Valid frozen execution inputs for unit fixtures; no model or persistence shortcuts."""

from zhiheng.modules.chat.execution import ChatExecutionSpec


def execution_spec(**changes: object) -> ChatExecutionSpec:
    return ChatExecutionSpec.model_validate(
        {
            'version': 1,
            'reservation_input_tokens': 100,
            'base_checkpoint_id': None,
            'expected_head': None,
            'incremental': False,
            'message_ids': ['prefix:0', 'question'],
            'trigger_tokens': 1000,
            'keep_tokens': 300,
            'summary_output_tokens': 64,
            'summary_input_tokens': 4096,
            'input_budget': 8192,
            'summary_used': False,
            **changes,
        }
    )
