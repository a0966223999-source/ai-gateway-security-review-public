"""Strict producer locator, separate from the immutable canonical RPC request.

A locator identifies a target only. It grants no runtime or Source authority.
Identifiers are nonempty, at most 256 UTF-8 bytes, without control characters.
"""
import json


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)

MAX_IDENTIFIER_BYTES = 256
MAX_LOCATOR_BYTES = 2048


def validate_target_locator(value):
    if (not isinstance(value, dict)
            or set(value) != {'v', 'kind', 'threadId', 'environmentId'}
            or type(value['v']) is not int or value['v'] != 2
            or value['kind'] != 'ordinary-work'):
        raise ValueError('TARGET_LOCATOR_SCHEMA')
    for field in ('threadId', 'environmentId'):
        identifier = value[field]
        if (not isinstance(identifier, str) or not identifier.strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in identifier)):
            raise ValueError('TARGET_LOCATOR_IDENTIFIER')
        try:
            size = len(identifier.encode('utf-8'))
        except UnicodeError as exc:
            raise ValueError('TARGET_LOCATOR_IDENTIFIER') from exc
        if size > MAX_IDENTIFIER_BYTES:
            raise ValueError('TARGET_LOCATOR_IDENTIFIER')
    return dict(value)


def parse_target_locator(raw, method='register'):
    if not isinstance(raw, str):
        raise ValueError('TARGET_LOCATOR_JSON_REQUIRED')
    if raw == '':
        return None
    if method != 'register':
        raise ValueError('TARGET_LOCATOR_REGISTER_ONLY')
    try:
        if len(raw.encode('utf-8')) > MAX_LOCATOR_BYTES:
            raise ValueError('TARGET_LOCATOR_SIZE')
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('TARGET_LOCATOR_DUPLICATE_FIELD')
                result[key] = value
            return result
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('TARGET_LOCATOR_NONFINITE')))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('TARGET_LOCATOR_JSON_REQUIRED') from exc
    value = validate_target_locator(value)
    if canonical(value) != raw:
        raise ValueError('TARGET_LOCATOR_EXACT_CANONICAL_REQUIRED')
    return value
