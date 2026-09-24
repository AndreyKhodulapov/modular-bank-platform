from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta

import pytest

from exceptions import InvalidOperationError
from models import Owner

VALID = {
    "first_name": "Ivan",
    "last_name": "Petrov",
    "birth_date": date(1990, 1, 1),
    "email": "ivan@example.com",
    "phone": "+79161234567",
}


def test_full_name_without_middle_name():
    owner = Owner(**VALID)
    assert owner.full_name == "Petrov Ivan"


def test_full_name_with_middle_name():
    owner = Owner(**VALID, middle_name="Sergeevich")
    assert owner.full_name == "Petrov Ivan Sergeevich"


def test_to_dict_serializes_birth_date_as_iso_string():
    owner = Owner(**VALID, middle_name="Sergeevich")
    assert owner.to_dict() == {
        "first_name": "Ivan",
        "last_name": "Petrov",
        "middle_name": "Sergeevich",
        "birth_date": "1990-01-01",
        "email": "ivan@example.com",
        "phone": "+79161234567",
    }


def test_owner_is_immutable():
    owner = Owner(**VALID)
    with pytest.raises(FrozenInstanceError):
        owner.first_name = "Petr"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("first_name", ""),
        ("first_name", "   "),
        ("last_name", None),
        ("middle_name", ""),
        ("birth_date", "1990-01-01"),
        ("birth_date", datetime(1990, 1, 1)),
        ("birth_date", date.today() + timedelta(days=1)),
        ("email", "not-an-email"),
        ("email", ""),
        ("phone", "123"),
        ("phone", "phone"),
    ],
)
def test_owner_rejects_invalid_fields(field, value):
    with pytest.raises(InvalidOperationError):
        Owner(**{**VALID, field: value})
