"""Strict untrusted Work observations, never route, identity, consent, or grant."""
import json

from targeted_locator import canonical, parse_target_locator

MAX_SELF_REPORT_BYTES = 2048
MAX_IDENTIFIER_BYTES = 256


def validate_self_report(value):
    if (type(value) is not dict or set(value) != {'v', 'kind', 'observations'}
            or type(value['v']) is not int or value['v'] != 1
            or value['kind'] != 'work-runtime-hints'):
        raise ValueError('SELF_REPORT_SCHEMA')
    observations = value['observations']
    if (type(observations) is not dict
            or set(observations) not in ({'CODEX_THREAD_ID'},
                                        {'CODEX_THREAD_ID', 'CODEX_ENVIRONMENT_ID'})):
        raise ValueError('SELF_REPORT_SCHEMA')
    for identifier in observations.values():
        if (type(identifier) is not str
                or all(character.isspace() or character == '\ufeff' for character in identifier)
                or any(ord(character) < 32 or ord(character) == 127 for character in identifier)):
            raise ValueError('SELF_REPORT_IDENTIFIER')
        try:
            size = len(identifier.encode('utf-8'))
        except UnicodeError as exc:
            raise ValueError('SELF_REPORT_IDENTIFIER') from exc
        if size > MAX_IDENTIFIER_BYTES:
            raise ValueError('SELF_REPORT_IDENTIFIER')
    return dict(value, observations=dict(observations))


def parse_self_report(raw, method='register'):
    if type(raw) is not str:
        raise ValueError('SELF_REPORT_JSON_REQUIRED')
    if raw == '':
        return None
    if method != 'register':
        raise ValueError('SELF_REPORT_REGISTER_ONLY')
    try:
        if len(raw.encode('utf-8')) > MAX_SELF_REPORT_BYTES:
            raise ValueError('SELF_REPORT_SIZE')

        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('SELF_REPORT_DUPLICATE_FIELD')
                result[key] = value
            return result

        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('SELF_REPORT_NONFINITE')))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('SELF_REPORT_JSON_REQUIRED') from exc
    value = validate_self_report(value)
    if canonical(value) != raw:
        raise ValueError('SELF_REPORT_EXACT_CANONICAL_REQUIRED')
    return value


def parse_registration_sidebands(target_locator_json='', self_report_json='', method='register'):
    """Validate every boundary without combining an asserted target and hints."""
    target = parse_target_locator(target_locator_json, method)
    report = parse_self_report(self_report_json, method)
    if target is not None and report is not None:
        raise ValueError('REGISTRATION_SIDEBANDS_MUTUALLY_EXCLUSIVE')
    return target, report
