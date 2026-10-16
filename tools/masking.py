"""Masks PII (names, phone numbers) before anything is written to
turn_logs. Business logic always uses the real values; only the persisted
log copy is masked, per the project's privacy rules.

Masking known PII *keys* (patient_name, phone_number) isn't enough on its
own: a tool's human-readable "message" field often repeats the same name
in plain prose ("Booked Anita Test Patient with..."). So this does two
passes: mask known-key values structurally, then scrub every occurrence
of those same original values out of every other string in the dict too
(messages, nested lists/dicts included).
"""
import re

_PHONE_RE = re.compile(r"\b\d{10}\b")
_NAME_KEYS = {"patient_name", "name"}
_PHONE_KEYS = {"phone_number", "phone"}


def mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 4:
        return "***"
    return digits[:2] + "X" * (len(digits) - 4) + digits[-2:]


def mask_name(name: str) -> str:
    parts = name.strip().split()
    return " ".join(f"{p[0]}{'*' * (len(p) - 1)}" if p else p for p in parts)


def mask_transcript(text: str) -> str:
    """Best-effort masking of bare 10-digit phone numbers inside free text."""
    return _PHONE_RE.sub(lambda m: mask_phone(m.group()), text)


def _collect_pii_values(node, mapping: dict[str, str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and value:
                if key in _NAME_KEYS:
                    mapping[value] = mask_name(value)
                elif key in _PHONE_KEYS:
                    mapping[value] = mask_phone(value)
            _collect_pii_values(value, mapping)
    elif isinstance(node, list):
        for item in node:
            _collect_pii_values(item, mapping)


def _apply_mask(node, mapping: dict[str, str]):
    if isinstance(node, dict):
        return {k: _apply_mask(v, mapping) for k, v in node.items()}
    if isinstance(node, list):
        return [_apply_mask(item, mapping) for item in node]
    if isinstance(node, str):
        text = node
        for original, masked in mapping.items():
            if original:
                text = text.replace(original, masked)
        return mask_transcript(text)
    return node


def mask_dict(data: dict) -> dict:
    """Masks every known PII value anywhere in a tool-call args/result
    dict, including repeats of that same value inside free-text fields
    like "message", and inside nested lists/dicts (alternatives, options)."""
    mapping: dict[str, str] = {}
    _collect_pii_values(data, mapping)
    return _apply_mask(data, mapping)
