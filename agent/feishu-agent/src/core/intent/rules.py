"""Intent rules helpers."""

from __future__ import annotations

import re
from typing import Iterable


def compile_trigger_patterns(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    return [re.compile(pattern) for pattern in patterns if pattern]
