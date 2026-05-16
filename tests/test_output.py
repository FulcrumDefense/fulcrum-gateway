"""Tests for ax_cli/output.py — mention_prefix, print_table, handle_error."""

from unittest.mock import MagicMock, PropertyMock, patch

import httpx
import pytest
import typer

from ax_cli.output import handle_error, mention_prefix, print_table


def test_mention_prefix_whitespace_only():
    assert mention_prefix("   ") == ""


def test_mention_prefix_none():
    assert mention_prefix(None) == ""


def test_mention_prefix_empty():
    assert mention_prefix("") == ""


def test_mention_prefix_with_handle():
    assert mention_prefix("alice") == "@alice"


def test_mention_prefix_already_prefixed():
    assert mention_prefix("@alice") == "@alice"


def test_mention_prefix_strips_whitespace():
    assert mention_prefix("  bob  ") == "@bob"


def test_print_table_auto_keys(capsys):
    with patch("ax_cli.output.console") as mock_console:
        print_table(
            ["Agent Name", "Status"],
            [{"agent_name": "bot", "status": "online"}],
        )
        mock_console.print.assert_called_once()
        table = mock_console.print.call_args[0][0]
        assert table.columns[0].header == "Agent Name"
        assert table.columns[1].header == "Status"


def test_print_table_explicit_keys(capsys):
    with patch("ax_cli.output.console") as mock_console:
        print_table(
            ["Name", "Value"],
            [{"n": "foo", "v": "bar"}],
            keys=["n", "v"],
        )
        mock_console.print.assert_called_once()


def _make_http_status_error(status_code, response_text="", response_json=None, url="http://test.local/api"):
    request = httpx.Request("GET", url)
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.text = response_text
    if response_json is not None:
        response.json.return_value = response_json
    else:
        response.json.side_effect = Exception("not json")
    err = httpx.HTTPStatusError("error", request=request, response=response)
    return err


def test_handle_error_html_response():
    err = _make_http_status_error(
        502,
        response_text="<html><body>Bad Gateway</body></html>",
    )
    with pytest.raises(typer.Exit):
        handle_error(err)


def test_handle_error_plain_text_response():
    err = _make_http_status_error(
        500,
        response_text="Internal server error occurred",
    )
    with pytest.raises(typer.Exit):
        handle_error(err)


def test_handle_error_plain_text_with_invalid_credential(capsys):
    err = _make_http_status_error(
        401,
        response_text="invalid_credential: token expired",
    )
    with pytest.raises(typer.Exit):
        handle_error(err)


def test_handle_error_json_with_invalid_credential_dict():
    err = _make_http_status_error(
        401,
        response_json={"detail": {"error": "invalid_credential"}},
    )
    with pytest.raises(typer.Exit):
        handle_error(err)


def test_handle_error_request_url_host_exception():
    response = MagicMock(spec=httpx.Response)
    response.status_code = 401
    response.text = "invalid_credential"
    response.json.side_effect = Exception("not json")

    request = MagicMock()
    request.url = "http://test.local/"
    type(request).url = PropertyMock(
        side_effect=[
            MagicMock(__str__=lambda self: "http://test.local/"),
            MagicMock(host=PropertyMock(side_effect=Exception("no host"))),
        ]
    )

    err = httpx.HTTPStatusError("error", request=httpx.Request("GET", "http://test.local/"), response=response)
    err._request = MagicMock()
    err._request.url.__str__ = lambda self: "http://test.local/"

    url_mock = MagicMock()
    url_mock.host = property(lambda self: (_ for _ in ()).throw(Exception("boom")))

    err2 = _make_http_status_error(
        401,
        response_text="invalid_credential present here",
    )
    err2.request = MagicMock()
    err2.request.url = MagicMock()
    err2.request.url.__str__ = MagicMock(return_value="http://test.local/")
    type(err2.request.url).host = PropertyMock(side_effect=Exception("no host attr"))

    with pytest.raises(typer.Exit):
        handle_error(err2)
