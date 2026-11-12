"""Choose a bounded execution lane from the request frozen at admission."""

from typing import Literal

MODEL_TYPE_PATH = ('binding', 'model_type')


def execution_lane(hint: object) -> Literal['generation', 'short']:
    # The path selects ModelRequest.binding.model_type frozen at admission.
    # Full request validation and malformed-request settlement belong to the executor.
    return 'short' if isinstance(hint, str) and hint in {'embedding', 'rerank'} else 'generation'
