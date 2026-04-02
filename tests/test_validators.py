"""Tests for bot.utils.validators — INN checksum, KPP, phone, email, junk detection."""

from bot.utils.validators import (
    is_junk_text,
    sanitise_for_llm,
    validate_address,
    validate_contact_info,
    validate_contact_person,
    validate_inn,
    validate_kpp,
    validate_org_name,
)


class TestValidateINN:
    def test_valid_10_digit_inn(self):
        ok, result = validate_inn("7806341520")
        assert ok is True
        assert result == "7806341520"

    def test_valid_12_digit_inn(self):
        ok, result = validate_inn("500100732259")
        assert ok is True
        assert result == "500100732259"

    def test_invalid_length(self):
        ok, result = validate_inn("12345")
        assert ok is False
        assert "10 или 12" in result

    def test_non_digit(self):
        ok, result = validate_inn("78063ABC20")
        assert ok is False
        assert "только цифры" in result

    def test_bad_checksum_10(self):
        ok, result = validate_inn("7806341521")
        assert ok is False
        assert "контрольная сумма" in result.lower() or "контрольн" in result.lower()

    def test_strips_spaces(self):
        ok, result = validate_inn("  7806341520  ")
        assert ok is True
        assert result == "7806341520"


class TestValidateKPP:
    def test_valid_kpp(self):
        ok, result = validate_kpp("781601001")
        assert ok is True
        assert result == "781601001"

    def test_invalid_kpp_length(self):
        ok, result = validate_kpp("12345")
        assert ok is False
        assert "9 цифр" in result

    def test_non_digit_kpp(self):
        ok, result = validate_kpp("78160A001")
        assert ok is False


class TestValidateContactInfo:
    def test_valid_phone(self):
        ok, result = validate_contact_info("+7 (999) 123-45-67")
        assert ok is True

    def test_valid_email(self):
        ok, result = validate_contact_info("user@example.com")
        assert ok is True
        assert result == "user@example.com"

    def test_invalid_contact(self):
        ok, result = validate_contact_info("not-a-contact")
        assert ok is False


class TestValidateOrgName:
    def test_valid_name(self):
        ok, result = validate_org_name('ООО "Тестовая компания"')
        assert ok is True

    def test_too_short(self):
        ok, _ = validate_org_name("AB")
        assert ok is False

    def test_too_long(self):
        ok, _ = validate_org_name("A" * 301)
        assert ok is False


class TestValidateAddress:
    def test_valid_address(self):
        ok, _ = validate_address("г. Москва, ул. Ленина, д. 1")
        assert ok is True

    def test_too_short(self):
        ok, _ = validate_address("Москва")
        assert ok is False


class TestValidateContactPerson:
    def test_valid(self):
        ok, _ = validate_contact_person("Иванов Иван Иванович")
        assert ok is True

    def test_too_short(self):
        ok, _ = validate_contact_person("И")
        assert ok is False


class TestJunkText:
    def test_only_digits(self):
        assert is_junk_text("12345") is True

    def test_only_symbols(self):
        assert is_junk_text("!!!???...") is True

    def test_empty(self):
        assert is_junk_text("") is True

    def test_normal_text(self):
        assert is_junk_text("Нужен анализ воды") is False


class TestSanitise:
    def test_truncation(self):
        result = sanitise_for_llm("x" * 5000, max_length=100)
        assert len(result) == 100

    def test_strip(self):
        result = sanitise_for_llm("  hello  ")
        assert result == "hello"
