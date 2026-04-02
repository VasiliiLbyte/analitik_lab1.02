"""Input validation helpers: INN checksum, KPP format, phone, email, text sanitisation."""

from __future__ import annotations

import re

_INN_WEIGHTS_10 = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN_WEIGHTS_12_1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN_WEIGHTS_12_2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)
_PHONE_RE = re.compile(
    r"^(\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}$"
)
_JUNK_RE = re.compile(
    r"^[\d\s\W]+$", re.UNICODE
)
_ORG_PREFIX_RE = re.compile(
    r"^\s*(ООО|ИП|ОАО|ЗАО|ПАО|АО|НКО|АНО|ГБУ|МУП|ГУП|ФГУП|ФГБУ)\b",
    re.IGNORECASE,
)


def _checksum(digits: str, weights: tuple[int, ...]) -> int:
    return sum(int(d) * w for d, w in zip(digits, weights)) % 11 % 10


def validate_inn(value: str) -> tuple[bool, str]:
    """Validate Russian ИНН (10 or 12 digits) including checksum."""
    cleaned = value.strip().replace(" ", "")
    if not cleaned.isdigit():
        return False, "ИНН должен содержать только цифры."
    if len(cleaned) == 10:
        if _checksum(cleaned, _INN_WEIGHTS_10) != int(cleaned[9]):
            return False, "Неверная контрольная сумма ИНН (10 цифр)."
        return True, cleaned
    if len(cleaned) == 12:
        c1 = _checksum(cleaned, _INN_WEIGHTS_12_1) == int(cleaned[10])
        c2 = _checksum(cleaned, _INN_WEIGHTS_12_2) == int(cleaned[11])
        if not (c1 and c2):
            return False, "Неверная контрольная сумма ИНН (12 цифр)."
        return True, cleaned
    return False, "ИНН должен содержать 10 или 12 цифр."


def validate_kpp(value: str) -> tuple[bool, str]:
    """Validate KPP: exactly 9 digits."""
    cleaned = value.strip().replace(" ", "")
    if not cleaned.isdigit() or len(cleaned) != 9:
        return False, "КПП должен содержать ровно 9 цифр."
    return True, cleaned


def validate_phone(value: str) -> tuple[bool, str]:
    cleaned = value.strip()
    if _PHONE_RE.match(cleaned):
        return True, cleaned
    return False, "Укажите телефон в формате +7 (XXX) XXX-XX-XX."


def validate_email(value: str) -> tuple[bool, str]:
    cleaned = value.strip()
    if _EMAIL_RE.match(cleaned):
        return True, cleaned
    return False, "Неверный формат email."


def validate_contact_info(value: str) -> tuple[bool, str]:
    """Phone or email accepted."""
    ok_phone, phone = validate_phone(value)
    if ok_phone:
        return True, phone
    ok_email, email = validate_email(value)
    if ok_email:
        return True, email
    return False, "Укажите телефон (+7 XXX XXX-XX-XX) или email."


def validate_org_name(value: str) -> tuple[bool, str]:
    cleaned = value.strip()
    if len(cleaned) < 3:
        return False, "Название организации слишком короткое (мин. 3 символа)."
    if len(cleaned) > 300:
        return False, "Название организации слишком длинное (макс. 300 символов)."
    if not _ORG_PREFIX_RE.match(cleaned):
        return (
            False,
            "Укажите юр. форму и название (например: ООО \"Ромашка\", ИП Иванов, АО ТехСервис).",
        )
    return True, cleaned


def validate_address(value: str) -> tuple[bool, str]:
    cleaned = value.strip()
    if len(cleaned) < 10:
        return False, "Адрес слишком короткий (мин. 10 символов)."
    if len(cleaned) > 500:
        return False, "Адрес слишком длинный (макс. 500 символов)."
    return True, cleaned


def validate_contact_person(value: str) -> tuple[bool, str]:
    cleaned = value.strip()
    if len(cleaned) < 2:
        return False, "Имя контактного лица слишком короткое."
    if len(cleaned) > 200:
        return False, "Имя контактного лица слишком длинное."
    return True, cleaned


def is_junk_text(text: str) -> bool:
    """Detect messages that contain only digits, emoji, or special characters."""
    cleaned = text.strip()
    if not cleaned:
        return True
    if _JUNK_RE.match(cleaned):
        return True
    return False


def sanitise_for_llm(text: str, max_length: int = 2000) -> str:
    """Truncate and clean text before sending to LLM."""
    return text.strip()[:max_length]
