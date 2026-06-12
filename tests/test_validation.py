import pytest

from qbo_mcp.qbo_client import escape_qbo_string, validate_date, validate_id


class TestValidateId:
    def test_accepts_numeric(self) -> None:
        # Given a numeric string id
        value = "123"

        # When validate_id is called
        result = validate_id(value)

        # Then it is returned unchanged
        assert result == "123"

    @pytest.mark.parametrize("bad", ["12a", "", "1; DROP TABLE x", " 1", "1.0", "-1"])
    def test_rejects_non_numeric(self, bad: str) -> None:
        # Given a non-numeric string

        # When validate_id is called
        # Then it raises ValueError
        with pytest.raises(ValueError):
            validate_id(bad)


class TestValidateDate:
    def test_accepts_iso(self) -> None:
        # Given a valid ISO date string
        value = "2026-06-12"

        # When validate_date is called
        result = validate_date(value)

        # Then it is returned unchanged
        assert result == "2026-06-12"

    @pytest.mark.parametrize("bad", ["06/12/2026", "not-a-date", "2026-13-01", ""])
    def test_rejects_bad(self, bad: str) -> None:
        # Given a string that is not a valid ISO date

        # When validate_date is called
        # Then it raises ValueError
        with pytest.raises(ValueError):
            validate_date(bad)


class TestEscapeQboString:
    def test_doubles_single_quotes(self) -> None:
        # Given a string containing a single quote
        value = "O'Brien"

        # When escape_qbo_string is called
        result = escape_qbo_string(value)

        # Then each single quote is doubled
        assert result == "O''Brien"

    def test_idempotent_on_already_escaped(self) -> None:
        # Given a string whose quotes are already doubled
        value = "O''Brien"

        # When escape_qbo_string is called again
        result = escape_qbo_string(value)

        # Then each quote is doubled again (the helper is not idempotent — callers must only escape once)
        assert result == "O''''Brien"

    def test_no_change_when_no_quote(self) -> None:
        # Given a string with no single quotes
        value = "Acme Co"

        # When escape_qbo_string is called
        result = escape_qbo_string(value)

        # Then it is returned unchanged
        assert result == "Acme Co"

    def test_handles_multiple_quotes(self) -> None:
        # Given a string with multiple single quotes
        value = "a'b'c"

        # When escape_qbo_string is called
        result = escape_qbo_string(value)

        # Then every quote is doubled
        assert result == "a''b''c"
