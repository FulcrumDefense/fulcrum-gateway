"""Tests for auth.py helper functions — avoids the CLI commands that hang."""

from ax_cli.commands.auth import (
    _candidate_space_id,
    _invalid_credential_recovery_copy,
    _mask_token_prefix,
    _select_login_space,
)

# ---- _mask_token_prefix ----


def test_mask_empty():
    assert _mask_token_prefix("") == "***"


def test_mask_short():
    assert _mask_token_prefix("ab") == "**"


def test_mask_normal():
    result = _mask_token_prefix("axp_u_TestKey.Secret")
    assert result.startswith("axp_u_")
    assert "********" in result


def test_mask_whitespace():
    assert _mask_token_prefix("   ") == "***"


# ---- _candidate_space_id ----


def test_candidate_space_id_from_id():
    assert _candidate_space_id({"id": "s1"}) == "s1"


def test_candidate_space_id_from_space_id():
    assert _candidate_space_id({"space_id": "s2"}) == "s2"


def test_candidate_space_id_missing():
    assert _candidate_space_id({}) is None


# ---- _select_login_space ----


def test_select_single_space():
    result = _select_login_space([{"id": "s1"}])
    assert result == {"id": "s1"}


def test_select_default_space():
    spaces = [
        {"id": "s1", "name": "Regular"},
        {"id": "s2", "name": "Default", "is_default": True},
    ]
    result = _select_login_space(spaces)
    assert result["id"] == "s2"


def test_select_current_space():
    spaces = [
        {"id": "s1", "name": "A"},
        {"id": "s2", "name": "B", "is_current": True},
    ]
    result = _select_login_space(spaces)
    assert result["id"] == "s2"


def test_select_personal_space():
    spaces = [
        {"id": "s1", "name": "Team"},
        {"id": "s2", "name": "Personal", "is_personal": True},
    ]
    result = _select_login_space(spaces)
    assert result["id"] == "s2"


def test_select_personal_by_mode():
    spaces = [
        {"id": "s1", "name": "Team"},
        {"id": "s2", "name": "Mine", "space_mode": "personal"},
    ]
    result = _select_login_space(spaces)
    assert result["id"] == "s2"


def test_select_ambiguous_returns_none():
    spaces = [
        {"id": "s1", "name": "A"},
        {"id": "s2", "name": "B"},
        {"id": "s3", "name": "C"},
    ]
    assert _select_login_space(spaces) is None


# ---- _invalid_credential_recovery_copy ----


def test_recovery_copy_with_host():
    msg = _invalid_credential_recovery_copy("paxai.app")
    assert "paxai.app" in msg
    assert "axctl login" in msg


def test_recovery_copy_no_host():
    msg = _invalid_credential_recovery_copy(None)
    assert "the configured host" in msg
    assert "<your-host>" in msg
