"""Tests for the read-only Apple Mail tools (osascript is mocked)."""

from __future__ import annotations

import json
import subprocess
import types

import pytest

from openjarvis.tools import apple_mail as am


def _proc(stdout="", stderr="", returncode=0):
    return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(am.sys, "platform", "darwin")
    calls = []

    def install(result):
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(am.subprocess, "run", fake_run)
        return calls

    return install


_ROWS = [
    {"id": "2", "subject": "Neu", "sender": "Bob <b@y.ch>",
     "date": "2026-09-23T08:00:00.000Z", "read": False},
]


def test_unread_lists_and_marks_untrusted(mac):
    calls = mac(_proc(json.dumps(_ROWS)))
    result = am.AppleMailUnreadTool().execute(max_results=5)
    assert result.success
    assert "id=2" in result.content and "[ungelesen]" in result.content
    assert "nicht vertrauenswürdig" in result.content
    cmd = calls[0]
    assert cmd[:4] == ["osascript", "-l", "JavaScript", "-e"]
    assert cmd[-2:] == ["unread", "5"]


def test_unread_empty(mac):
    mac(_proc("[]"))
    assert am.AppleMailUnreadTool().execute().content == "Keine ungelesenen Mails."


def test_limit_is_clamped(mac):
    calls = mac(_proc("[]"))
    am.AppleMailUnreadTool().execute(max_results=9999)
    assert calls[0][-1] == str(am._MAX_RESULTS)


def test_search_passes_query_as_argument_not_code(mac):
    calls = mac(_proc(json.dumps(_ROWS)))
    evil = "'); Application('Mail').send(); ('"
    am.AppleMailSearchTool().execute(query=evil)
    script = calls[0][4]
    assert evil not in script  # never interpolated into the script
    assert calls[0][5:7] == ["search", evil]


def test_search_requires_query(mac):
    mac(_proc("[]"))
    assert not am.AppleMailSearchTool().execute(query="  ").success


def test_read_formats_and_truncates(mac):
    msg = {"id": "2", "subject": "Hallo", "sender": "Bob", "to": ["sophie@me.ch"],
           "date": "2026-09-23T08:00:00.000Z", "content": "x" * 10000}
    mac(_proc(json.dumps(msg)))
    result = am.AppleMailReadTool().execute(id="2")
    assert result.success
    assert "Betreff: Hallo" in result.content
    assert "[... gekürzt ...]" in result.content
    assert len(result.content) < 5000


def test_read_rejects_non_numeric_id(mac):
    calls = mac(_proc("null"))
    assert not am.AppleMailReadTool().execute(id="1 or 1").success
    assert calls == []


def test_read_not_found(mac):
    mac(_proc("null"))
    assert not am.AppleMailReadTool().execute(id="99").success


def test_permission_denied_gives_help(mac):
    mac(_proc(stderr="execution error: Not authorized to send Apple events (-1743)",
              returncode=1))
    result = am.AppleMailUnreadTool().execute()
    assert not result.success and "Automation" in result.content


def test_timeout_handled(mac):
    mac(subprocess.TimeoutExpired(cmd="osascript", timeout=60))
    assert not am.AppleMailUnreadTool().execute().success


def test_non_mac_fails_cleanly(monkeypatch):
    monkeypatch.setattr(am.sys, "platform", "linux")
    result = am.AppleMailUnreadTool().execute()
    assert not result.success and "macOS" in result.content


def test_tools_are_untrusted_and_read_only():
    for cls in (am.AppleMailUnreadTool, am.AppleMailSearchTool, am.AppleMailReadTool):
        assert cls.is_local is False
    for verb in ("send(", ".delete(", "readStatus = true", "move("):
        assert verb not in am._JXA


def test_tools_registered():
    from openjarvis.core.registry import ToolRegistry

    for name, cls in (
        ("apple_mail_unread", am.AppleMailUnreadTool),
        ("apple_mail_search", am.AppleMailSearchTool),
        ("apple_mail_read", am.AppleMailReadTool),
    ):
        if not ToolRegistry.contains(name):
            ToolRegistry.register_value(name, cls)
        assert ToolRegistry.get(name) is cls
