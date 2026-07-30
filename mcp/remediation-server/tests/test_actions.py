"""Unit tests for actions.py -- mocks subprocess (docker restart) and httpx (fault
reset) so these run without a real Docker daemon or live services."""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import actions  # noqa: E402


def test_restart_service_calls_docker_restart_with_correct_container_name(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="faultline-checkout-1\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = actions.restart_service("checkout")

    assert captured["cmd"] == ["docker", "restart", "faultline-checkout-1"]
    assert "restarted faultline-checkout-1" in result


def test_restart_service_raises_on_docker_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="no such container")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="no such container"):
        actions.restart_service("checkout")


def test_rollback_deployment_posts_to_the_right_fault_reset_endpoint(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"db_connection_leak": False}

    def fake_post(url, timeout=None):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr(actions.httpx, "post", fake_post)
    result = actions.rollback_deployment("catalog")

    assert captured["url"] == f"{actions.SERVICE_URLS['catalog']}/internal/fault/reset"
    assert "reset fault config on catalog" in result


def test_rollback_deployment_rejects_unknown_service():
    with pytest.raises(ValueError, match="unknown service"):
        actions.rollback_deployment("postgres")
