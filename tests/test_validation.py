from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from nanobar_api import ValidationError, parse, to_json_schema


@dataclass
class Address:
    city: str
    zip_code: str | None = None


@dataclass
class Person:
    name: str
    age: int
    height: float
    active: bool
    tags: list[str]
    address: Address
    nickname: str | None = None
    scores: list[int] = field(default_factory=list)
    note: str = "n/a"
    payload: Any = None
    metadata: dict[str, str] = field(default_factory=dict)


# --- parse: happy paths ---


def test_parse_full_object() -> None:
    person = parse(
        Person,
        {
            "name": "Ada",
            "age": 30,
            "height": 1.7,
            "active": True,
            "tags": ["a", "b"],
            "address": {"city": "London"},
        },
    )
    assert person.name == "Ada"
    assert person.age == 30
    assert person.height == 1.7
    assert person.active is True
    assert person.tags == ["a", "b"]
    assert person.address == Address(city="London", zip_code=None)
    assert person.nickname is None
    assert person.scores == []
    assert person.note == "n/a"


def test_parse_optional_field_present() -> None:
    person = parse(
        Person,
        {
            "name": "Ada",
            "age": 30,
            "height": 1.7,
            "active": True,
            "tags": [],
            "address": {"city": "London", "zip_code": "E1"},
            "nickname": "Countess",
        },
    )
    assert person.nickname == "Countess"
    assert person.address.zip_code == "E1"


def test_parse_int_widens_to_float() -> None:
    person = parse(
        Person,
        {"name": "Ada", "age": 30, "height": 2, "active": True, "tags": [], "address": {"city": "London"}},
    )
    assert person.height == 2.0
    assert isinstance(person.height, float)


def test_parse_any_field_accepts_anything() -> None:
    person = parse(
        Person,
        {
            "name": "Ada",
            "age": 30,
            "height": 1.7,
            "active": True,
            "tags": [],
            "address": {"city": "London"},
            "payload": {"anything": [1, "two", None]},
        },
    )
    assert person.payload == {"anything": [1, "two", None]}


def test_parse_missing_field_with_default_is_fine() -> None:
    person = parse(
        Person,
        {"name": "Ada", "age": 30, "height": 1.7, "active": True, "tags": [], "address": {"city": "London"}},
    )
    assert person.scores == []


# --- parse: errors ---


def test_parse_rejects_non_dataclass() -> None:
    with pytest.raises(TypeError):
        parse(dict, {})


def test_parse_rejects_non_mapping_input() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse(Address, ["not", "a", "mapping"])  # type: ignore[arg-type]
    assert "expected an object" in exc_info.value.errors[0]


def test_parse_missing_required_field() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse(Address, {})
    assert exc_info.value.errors == ["city: required field missing"]


def test_parse_accumulates_multiple_errors() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse(
            Person,
            {"name": 123, "age": "not an int", "height": 1.7, "active": True, "tags": [], "address": {"city": "L"}},
        )
    assert len(exc_info.value.errors) == 2


def test_parse_wrong_type_str() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse(Address, {"city": 123})
    assert "expected str" in exc_info.value.errors[0]


def test_parse_bool_rejected_for_int_field() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse(
            Person,
            {"name": "Ada", "age": True, "height": 1.7, "active": True, "tags": [], "address": {"city": "L"}},
        )
    assert "got bool" in exc_info.value.errors[0]


def test_parse_bool_rejected_for_float_field() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse(
            Person,
            {"name": "Ada", "age": 30, "height": True, "active": True, "tags": [], "address": {"city": "L"}},
        )
    assert "got bool" in exc_info.value.errors[0]


def test_parse_non_bool_rejected_for_bool_field() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse(
            Person,
            {"name": "Ada", "age": 30, "height": 1.7, "active": "yes", "tags": [], "address": {"city": "L"}},
        )
    assert "expected bool" in exc_info.value.errors[0]


def test_parse_null_rejected_for_non_optional_plain_field() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse(Address, {"city": None})
    assert "got NoneType" in exc_info.value.errors[0]


def test_parse_null_rejected_for_non_optional_union_field() -> None:
    @dataclass
    class Flexible:
        value: int | str

    with pytest.raises(ValidationError) as exc_info:
        parse(Flexible, {"value": None})
    assert "must not be null" in exc_info.value.errors[0]


def test_parse_null_accepted_for_optional() -> None:
    address = parse(Address, {"city": "London", "zip_code": None})
    assert address.zip_code is None


def test_parse_list_wrong_type() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse(
            Person,
            {"name": "Ada", "age": 30, "height": 1.7, "active": True, "tags": "not a list", "address": {"city": "L"}},
        )
    assert "expected a list" in exc_info.value.errors[0]


def test_parse_list_item_errors_accumulate() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse(
            Person,
            {"name": "Ada", "age": 30, "height": 1.7, "active": True, "tags": [1, "ok", 2], "address": {"city": "L"}},
        )
    assert len(exc_info.value.errors) == 2
    assert "tags[0]" in exc_info.value.errors[0]
    assert "tags[2]" in exc_info.value.errors[1]


def test_parse_dict_field_round_trips() -> None:
    person = parse(
        Person,
        {
            "name": "Ada",
            "age": 30,
            "height": 1.7,
            "active": True,
            "tags": [],
            "address": {"city": "L"},
            "metadata": {"role": "engineer"},
        },
    )
    assert person.metadata == {"role": "engineer"}


def test_parse_dict_field_wrong_type() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse(
            Person,
            {
                "name": "Ada",
                "age": 30,
                "height": 1.7,
                "active": True,
                "tags": [],
                "address": {"city": "L"},
                "metadata": "not a dict",
            },
        )
    assert "expected an object" in exc_info.value.errors[0]


def test_parse_dict_field_item_errors_accumulate() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse(
            Person,
            {
                "name": "Ada",
                "age": 30,
                "height": 1.7,
                "active": True,
                "tags": [],
                "address": {"city": "L"},
                "metadata": {"a": "ok", "b": 2},
            },
        )
    assert len(exc_info.value.errors) == 1
    assert "metadata.b" in exc_info.value.errors[0]


def test_parse_nested_dataclass_wrong_type() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse(
            Person,
            {"name": "Ada", "age": 30, "height": 1.7, "active": True, "tags": [], "address": "not an object"},
        )
    assert "expected an object" in exc_info.value.errors[0]


def test_parse_nested_dataclass_errors_are_prefixed() -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse(
            Person,
            {"name": "Ada", "age": 30, "height": 1.7, "active": True, "tags": [], "address": {}},
        )
    assert exc_info.value.errors == ["address.city: required field missing"]


def test_coerce_union_tries_each_arg() -> None:
    @dataclass
    class Flexible:
        value: int | str

    parsed = parse(Flexible, {"value": "hello"})
    assert parsed.value == "hello"
    parsed_int = parse(Flexible, {"value": 5})
    assert parsed_int.value == 5


def test_coerce_union_all_args_fail() -> None:
    @dataclass
    class Flexible:
        value: int | bool

    with pytest.raises(ValidationError):
        parse(Flexible, {"value": "not int or bool"})


def test_coerce_unsupported_type_raises() -> None:
    @dataclass
    class Unsupported:
        value: set[str]

    with pytest.raises(TypeError):
        parse(Unsupported, {"value": set()})


# --- to_json_schema ---


def test_to_json_schema_rejects_non_dataclass() -> None:
    with pytest.raises(TypeError):
        to_json_schema(dict)


def test_to_json_schema_basic_shape() -> None:
    schema = to_json_schema(Address)
    assert schema == {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "zip_code": {"type": "string", "nullable": True},
        },
        "required": ["city"],
    }


def test_to_json_schema_full_shape() -> None:
    schema = to_json_schema(Person)
    assert schema["type"] == "object"
    assert schema["properties"]["age"] == {"type": "integer"}
    assert schema["properties"]["height"] == {"type": "number"}
    assert schema["properties"]["active"] == {"type": "boolean"}
    assert schema["properties"]["tags"] == {"type": "array", "items": {"type": "string"}}
    assert schema["properties"]["address"]["type"] == "object"
    assert schema["properties"]["nickname"] == {"type": "string", "nullable": True}
    assert schema["properties"]["payload"] == {}
    assert schema["properties"]["metadata"] == {"type": "object", "additionalProperties": {"type": "string"}}
    assert set(schema["required"]) == {"name", "age", "height", "active", "tags", "address"}


def test_to_json_schema_no_required_when_all_have_defaults() -> None:
    @dataclass
    class AllDefaults:
        note: str = "hi"

    schema = to_json_schema(AllDefaults)
    assert "required" not in schema


def test_to_json_schema_union_of_multiple_non_none_types() -> None:
    @dataclass
    class Flexible:
        value: int | str

    schema = to_json_schema(Flexible)
    assert schema["properties"]["value"] == {"anyOf": [{"type": "integer"}, {"type": "string"}]}
