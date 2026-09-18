from threading import Event

import pytest
from playwright.sync_api import sync_playwright

from geoagent.interactive import wait_for_enter


def test_terminal_wait_dispatches_browser_callbacks(monkeypatch):
    received = Event()

    def fake_input(prompt):
        if not received.wait(3):
            raise EOFError("Browser events were blocked by the terminal wait")
        return ""

    monkeypatch.setattr("builtins.input", fake_input)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.expose_function("loginEvent", lambda: received.set())
            page.evaluate("setTimeout(() => window.loginEvent(), 150)")
            assert wait_for_enter(page, "Login ready?") == ""
            assert received.is_set()
        finally:
            browser.close()


def test_terminal_eof_is_propagated(monkeypatch):
    def fake_input(prompt):
        raise EOFError("No terminal input")

    monkeypatch.setattr("builtins.input", fake_input)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            with pytest.raises(EOFError, match="No terminal input"):
                wait_for_enter(page, "Login ready?")
        finally:
            browser.close()
