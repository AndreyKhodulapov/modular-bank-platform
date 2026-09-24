import uuid
from datetime import date, datetime, timedelta

import pytest

from exceptions import InvalidOperationError
from models import Client, ClientStatus

VALID = {
    "first_name": "Ivan",
    "last_name": "Petrov",
    "birth_date": date(1990, 1, 1),
    "email": "ivan@example.com",
    "phone": "+79161234567",
}


def test_generates_uuid4_when_client_id_missing():
    client = Client(**VALID)
    assert uuid.UUID(client.client_id).version == 4


def test_keeps_provided_client_id_stripped():
    client = Client(**VALID, client_id="  CL-1 ")
    assert client.client_id == "CL-1"


@pytest.mark.parametrize("client_id", ["", "   ", 42])
def test_rejects_invalid_client_id(client_id):
    with pytest.raises(InvalidOperationError):
        Client(**VALID, client_id=client_id)


def test_new_client_is_active_without_accounts():
    client = Client(**VALID)
    assert client.status is ClientStatus.ACTIVE
    assert not client.is_blocked
    assert client.account_ids == []


def test_full_name_without_middle_name():
    assert Client(**VALID).full_name == "Petrov Ivan"


def test_full_name_with_middle_name():
    assert Client(**VALID, middle_name="Sergeevich").full_name == "Petrov Ivan Sergeevich"


def test_names_are_stripped():
    client = Client(**{**VALID, "first_name": "  Ivan ", "last_name": "Petrov "}, middle_name=" Sergeevich")
    assert client.full_name == "Petrov Ivan Sergeevich"


def test_contacts():
    assert Client(**VALID).contacts == {"email": "ivan@example.com", "phone": "+79161234567"}


@pytest.mark.parametrize("attribute", ["client_id", "first_name", "birth_date", "email", "phone", "status"])
def test_attributes_are_read_only(attribute):
    client = Client(**VALID)
    with pytest.raises(AttributeError):
        setattr(client, attribute, "changed")


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
        ("email", "ivan@example.com\n"),
        ("phone", "123"),
        ("phone", "phone"),
        ("phone", "+79161234567\n"),
        ("phone", "١٢٣٤٥٦٧٨٩٠"),  # Arabic-Indic digits
        ("today", datetime(2026, 1, 1)),
    ],
)
def test_rejects_invalid_fields(field, value):
    with pytest.raises(InvalidOperationError):
        Client(**{**VALID, field: value})


@pytest.mark.parametrize(
    ("today", "accepted"),
    [
        (date(2026, 3, 14), False),  # one day before the 18th birthday
        (date(2026, 3, 15), True),  # the 18th birthday itself
    ],
)
def test_client_must_be_at_least_18(today, accepted):
    data = {**VALID, "birth_date": date(2008, 3, 15), "today": today}
    if accepted:
        assert Client(**data).age_on(today) == 18
    else:
        with pytest.raises(InvalidOperationError, match="at least 18"):
            Client(**data)


@pytest.mark.parametrize(
    ("on", "expected"),
    [
        (date(2022, 2, 28), 17),
        (date(2022, 3, 1), 18),  # leap-day birthday counts from 1 March in non-leap years
        (date(2024, 2, 29), 20),
    ],
)
def test_age_on_handles_leap_day_birthday(on, expected):
    client = Client(**{**VALID, "birth_date": date(2004, 2, 29)}, today=date(2022, 3, 1))
    assert client.age_on(on) == expected


def test_block_and_unblock():
    client = Client(**VALID)
    client.block()
    assert client.status is ClientStatus.BLOCKED
    assert client.is_blocked
    client.unblock()
    assert client.status is ClientStatus.ACTIVE


def test_repeated_block_or_unblock_is_rejected():
    client = Client(**VALID)
    with pytest.raises(InvalidOperationError):
        client.unblock()
    client.block()
    with pytest.raises(InvalidOperationError):
        client.block()


def test_account_ids_returns_a_copy():
    client = Client(**VALID)
    client.add_account_id("A-1")
    client.account_ids.append("A-2")
    assert client.account_ids == ["A-1"]


def test_duplicate_account_id_is_rejected():
    client = Client(**VALID)
    client.add_account_id("A-1")
    with pytest.raises(InvalidOperationError):
        client.add_account_id("A-1")


def test_equality_is_by_client_id():
    same_data_a = Client(**VALID)
    same_data_b = Client(**VALID)
    assert same_data_a != same_data_b
    assert Client(**VALID, client_id="CL-1") == Client(**{**VALID, "first_name": "Petr"}, client_id="CL-1")
    assert len({same_data_a, same_data_b, same_data_a}) == 2


def test_to_summary_dict():
    client = Client(**VALID, middle_name="Sergeevich", client_id="CL-1")
    assert client.to_summary_dict() == {
        "client_id": "CL-1",
        "full_name": "Petrov Ivan Sergeevich",
        "email": "ivan@example.com",
        "phone": "+79161234567",
    }
