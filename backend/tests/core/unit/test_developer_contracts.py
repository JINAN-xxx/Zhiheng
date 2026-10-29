import pytest
from pydantic import ValidationError

from zhiheng.modules.developer.public import ApplicationCreate, ApplicationPatch, KeyCreate


def test_strict_contracts() -> None:
    assert ApplicationCreate(code='demo', name=' demo ').name == 'demo'
    assert ApplicationPatch(name=' next ', expected_version=1).name == 'next'
    with pytest.raises(ValidationError):
        KeyCreate(name='sdk', model_ids=[], monthly_budget=1.2)


def test_binding_options_are_frozen_and_detached_from_input() -> None:
    from uuid import uuid4

    from zhiheng.modules.models.public import ModelBinding

    options = {'temperature': 0.3, 'stop': ['END']}
    binding = ModelBinding(
        model_id=uuid4(), connection_id=uuid4(), upstream_model='frozen', max_output_tokens=64, options=options
    )
    options['temperature'] = 0.9
    assert binding.options.temperature == 0.3
    with pytest.raises(ValidationError):
        binding.options.temperature = 1.0
    assert binding.options.stop == ('END',)


def test_key_monthly_points_limit_is_an_exact_optional_decimal() -> None:
    from decimal import Decimal
    from uuid import uuid4

    fields = {'name': 'sdk', 'model_ids': [uuid4()]}
    assert KeyCreate(**fields).monthly_points_limit is None
    assert KeyCreate(**fields, monthly_points_limit='0').monthly_points_limit == Decimal(0)
    assert KeyCreate(**fields, monthly_points_limit='1.000000000000000001').monthly_points_limit == Decimal(
        '1.000000000000000001'
    )
    for invalid in (1, 1.2, '-1', 'NaN', 'Infinity', '0.0000000000000000001'):
        with pytest.raises(ValidationError):
            KeyCreate(**fields, monthly_points_limit=invalid)


def test_key_points_limit_serializes_without_exponents() -> None:
    from decimal import Decimal

    from zhiheng.modules.developer.public import KeyInfo

    for value, expected in [
        (None, None),
        (Decimal('0E-18'), '0.000000000000000000'),
        (Decimal('-0E-18'), '0.000000000000000000'),
        (Decimal('1E+18'), '1000000000000000000'),
    ]:
        info = KeyInfo.model_construct(monthly_points_limit=value)
        assert info.model_dump(mode='json', include={'monthly_points_limit'})['monthly_points_limit'] == expected
