"""Tests for ax_cli/avatar.py — covering generate_avatar, pick_colors, initials."""

from ax_cli.avatar import (
    _hash_name,
    _initials,
    _pick_colors,
    avatar_data_uri,
    generate_avatar,
)


def test_hash_name_deterministic():
    assert _hash_name("test") == _hash_name("test")


def test_hash_name_different():
    assert _hash_name("alpha") != _hash_name("beta")


def test_pick_colors_default():
    fg, bg = _pick_colors("agent1")
    assert fg.startswith("#")
    assert bg.startswith("#")


def test_pick_colors_sentinel():
    fg, bg = _pick_colors("s1", "sentinel")
    assert (fg, bg) == ("#22d3ee", "#0891b2")


def test_pick_colors_unknown_type():
    fg, bg = _pick_colors("x", "nonexistent")
    assert fg.startswith("#")


def test_initials_single():
    assert _initials("backend") == "BA"


def test_initials_underscore():
    result = _initials("backend_sentinel")
    assert result == "BS"


def test_initials_hyphen():
    result = _initials("my-agent")
    assert result == "MA"


def test_initials_multi_word():
    result = _initials("the big agent")
    assert result == "TB"


def test_generate_avatar_svg():
    svg = generate_avatar("test-agent")
    assert svg.startswith("<svg")
    assert "</svg>" in svg


def test_generate_avatar_contains_initials():
    svg = generate_avatar("backend_sentinel")
    assert "BS" in svg


def test_generate_avatar_custom_size():
    svg = generate_avatar("x", size=128)
    assert "128" in svg


def test_generate_avatar_deterministic():
    assert generate_avatar("bot") == generate_avatar("bot")


def test_generate_avatar_different_names():
    assert generate_avatar("alpha") != generate_avatar("beta")


def test_generate_avatar_agent_types():
    for t in ["default", "sentinel", "mcp", "cloud"]:
        svg = generate_avatar("x", agent_type=t)
        assert "<svg" in svg


def test_avatar_data_uri():
    uri = avatar_data_uri("bot")
    assert uri.startswith("data:image/svg+xml;base64,")


def test_avatar_data_uri_decodable():
    import base64

    uri = avatar_data_uri("bot")
    b64 = uri.split(",", 1)[1]
    decoded = base64.b64decode(b64).decode()
    assert "<svg" in decoded
