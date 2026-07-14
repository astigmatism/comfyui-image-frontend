from __future__ import annotations

import pytest
from app.errors import AppError
from app.security import validate_password


def test_password_minimum_length_is_eight_characters() -> None:
    validate_password("12345678")

    with pytest.raises(AppError) as exc_info:
        validate_password("1234567")

    assert exc_info.value.code == "weak_password"
    assert exc_info.value.message == "Password must contain at least 8 characters."
