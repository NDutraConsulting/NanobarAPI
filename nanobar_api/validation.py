from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

_PRIMITIVES: tuple[type, ...] = (str, int, float, bool)

_PRIMITIVE_SCHEMA: dict[type, dict[str, str]] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
}


class ValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _has_default(field: dataclasses.Field[Any]) -> bool:
    return field.default is not dataclasses.MISSING or field.default_factory is not dataclasses.MISSING


def _is_union(field_type: Any) -> bool:
    origin = get_origin(field_type)
    return origin is Union or origin is UnionType


def _coerce(name: str, value: Any, field_type: Any) -> Any:
    if _is_union(field_type):
        args = get_args(field_type)
        is_optional = type(None) in args
        non_none = [a for a in args if a is not type(None)]
        if value is None:
            if is_optional:
                return None
            raise ValidationError([f"{name}: must not be null"])
        for arg in non_none:
            try:
                return _coerce(name, value, arg)
            except ValidationError:
                continue
        raise ValidationError([f"{name}: does not match {field_type}"])

    if get_origin(field_type) is list:
        if not isinstance(value, list):
            raise ValidationError([f"{name}: expected a list, got {type(value).__name__}"])
        (item_type,) = get_args(field_type)
        result: list[Any] = []
        errors: list[str] = []
        for index, item in enumerate(value):
            try:
                result.append(_coerce(f"{name}[{index}]", item, item_type))
            except ValidationError as exc:
                errors.extend(exc.errors)
        if errors:
            raise ValidationError(errors)
        return result

    if get_origin(field_type) is dict:
        if not isinstance(value, dict):
            raise ValidationError([f"{name}: expected an object, got {type(value).__name__}"])
        _, value_type = get_args(field_type)
        coerced: dict[str, Any] = {}
        dict_errors: list[str] = []
        for key, item in value.items():
            try:
                coerced[key] = _coerce(f"{name}.{key}", item, value_type)
            except ValidationError as exc:
                dict_errors.extend(exc.errors)
        if dict_errors:
            raise ValidationError(dict_errors)
        return coerced

    if isinstance(field_type, type) and dataclasses.is_dataclass(field_type):
        if not isinstance(value, Mapping):
            raise ValidationError([f"{name}: expected an object, got {type(value).__name__}"])
        try:
            return parse(field_type, value)
        except ValidationError as exc:
            raise ValidationError([f"{name}.{error}" for error in exc.errors]) from exc

    if field_type is Any:
        return value

    if field_type in _PRIMITIVES:
        if field_type is bool:
            if not isinstance(value, bool):
                raise ValidationError([f"{name}: expected bool, got {type(value).__name__}"])
            return value
        if isinstance(value, bool):
            raise ValidationError([f"{name}: expected {field_type.__name__}, got bool"])
        if isinstance(value, field_type):
            return value
        if field_type is float and isinstance(value, int):
            return float(value)
        raise ValidationError([f"{name}: expected {field_type.__name__}, got {type(value).__name__}"])

    raise TypeError(f"unsupported field type for {name!r}: {field_type!r}")


def parse[T](cls: type[T], data: Mapping[str, Any]) -> T:
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls!r} is not a dataclass")
    if not isinstance(data, Mapping):
        raise ValidationError([f"expected an object, got {type(data).__name__}"])

    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    errors: list[str] = []

    for field in dataclasses.fields(cls):
        if field.name not in data:
            if not _has_default(field):
                errors.append(f"{field.name}: required field missing")
            continue
        try:
            kwargs[field.name] = _coerce(field.name, data[field.name], hints[field.name])
        except ValidationError as exc:
            errors.extend(exc.errors)

    if errors:
        raise ValidationError(errors)

    return cls(**kwargs)


def _type_to_schema(field_type: Any) -> dict[str, Any]:
    if _is_union(field_type):
        args = get_args(field_type)
        is_optional = type(None) in args
        non_none = [a for a in args if a is not type(None)]
        schema = (
            _type_to_schema(non_none[0]) if len(non_none) == 1 else {"anyOf": [_type_to_schema(a) for a in non_none]}
        )
        if is_optional:
            schema = {**schema, "nullable": True}
        return schema

    if get_origin(field_type) is list:
        (item_type,) = get_args(field_type)
        return {"type": "array", "items": _type_to_schema(item_type)}

    if get_origin(field_type) is dict:
        _, value_type = get_args(field_type)
        return {"type": "object", "additionalProperties": _type_to_schema(value_type)}

    if isinstance(field_type, type) and dataclasses.is_dataclass(field_type):
        return to_json_schema(field_type)

    if field_type in _PRIMITIVE_SCHEMA:
        return dict(_PRIMITIVE_SCHEMA[field_type])

    return {}


def to_json_schema(cls: type) -> dict[str, Any]:
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls!r} is not a dataclass")

    hints = get_type_hints(cls)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for field in dataclasses.fields(cls):
        properties[field.name] = _type_to_schema(hints[field.name])
        if not _has_default(field):
            required.append(field.name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema
