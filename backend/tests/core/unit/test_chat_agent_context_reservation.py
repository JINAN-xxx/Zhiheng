"""Checkpoint input estimates use the same text/image units as native Run admission."""

from langchain_core.messages import AIMessage, HumanMessage

from zhiheng.infrastructure.agents.adapters.agent import checkpoint_reservation_tokens, context_input_tokens
from zhiheng.modules.models.public import ModelImage, ModelMessage, estimate_messages


def test_checkpoint_estimate_counts_images_once_without_base64_or_reasoning_bytes() -> None:
    messages = [
        HumanMessage(
            content=[
                {'type': 'text', 'text': '中文a'},
                {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + 'A' * 100000}},
                {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,short'}},
            ]
        ),
        AIMessage(
            content=[
                {'type': 'text', 'text': '答'},
                {'type': 'reasoning', 'reasoning': 'hidden ' * 1000},
            ],
            additional_kwargs={'reasoning_content': 'legacy hidden ' * 1000},
        ),
    ]
    # Two message overheads + ten UTF-8 text bytes + two fixed 2048 image estimates.
    assert checkpoint_reservation_tokens(messages) == 4138


def test_checkpoint_estimate_keeps_summary_and_recent_business_messages() -> None:
    messages = [
        HumanMessage(content='summary'),
        HumanMessage(content='问'),
        AIMessage(content='回答', additional_kwargs={'reasoning_content': '不可重复计费的推理'}),
    ]
    assert checkpoint_reservation_tokens(messages) == 64


def test_context_counter_uses_character_budget_and_fixed_images() -> None:
    messages = [
        HumanMessage(
            content=[
                {'type': 'text', 'text': '中文a'},
                {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + 'A' * 100000}},
                {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,short'}},
            ]
        ),
        AIMessage(
            content=[{'type': 'text', 'text': '答'}, {'type': 'reasoning', 'reasoning': 'hidden ' * 1000}],
            additional_kwargs={'reasoning_content': 'legacy hidden ' * 1000},
        ),
    ]
    # Two message overheads + ceil(one ASCII / 2) + three non-ASCII * 2 + two images.
    native = (
        ModelMessage(
            'user',
            '中文a',
            (
                ModelImage('data:image/jpeg;base64,' + 'A' * 100000),
                ModelImage('data:image/jpeg;base64,short'),
            ),
        ),
        ModelMessage('assistant', '答'),
    )
    assert context_input_tokens(messages) == estimate_messages(native) == 4135
