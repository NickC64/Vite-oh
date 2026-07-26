import re
import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn
from playwright.sync_api import Browser, Page, expect, sync_playwright

from tests.test_workspace import web_system
from viteoh.domain import GuildConfig, utcnow


@pytest.fixture
def workspace_url() -> Iterator[str]:
    client, repository, _, _ = web_system()
    now = utcnow()
    repository.guilds["guild-2"] = GuildConfig(
        guild_id="guild-2",
        guild_name="Second Guild",
        output_channel_id="channel-2",
        proposal_timeout_seconds=60,
        configured_by="admin",
        created_at=now,
        updated_at=now,
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            client.app,
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("Workspace test server did not start.")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def workspace_browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def workspace_page(workspace_browser: Browser) -> Iterator[Page]:
    page = workspace_browser.new_page()
    yield page
    page.close()


def test_workspace_server_rail_theme_and_mobile_drawer(
    workspace_page: Page, workspace_url: str
) -> None:
    page = workspace_page
    page.goto(f"{workspace_url}/launch?code=good")
    expect(page.get_by_role("heading", name="Proposals", exact=True)).to_be_visible()
    page.goto(f"{workspace_url}/launch?code=second")

    expect(page.get_by_role("link", name="Test Guild")).to_be_visible()
    expect(page.get_by_role("link", name="Second Guild")).to_be_visible()
    page.get_by_role("link", name="Test Guild").first.click()
    expect(page).to_have_url(f"{workspace_url}/app?guild=guild")
    page.get_by_role("link", name="History archive").click()
    expect(page.get_by_role("heading", name="History archive")).to_be_visible()
    page.get_by_role("link", name="Overview").click()

    current_theme = page.locator("html").get_attribute("data-theme")
    page.locator("[data-theme-toggle]").last.click()
    selected_theme = "light" if current_theme == "dark" else "dark"
    expect(page.locator("html")).to_have_attribute("data-theme", selected_theme)
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-theme", selected_theme)

    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="Open navigation").click()
    expect(page.locator("body")).to_have_class(re.compile(r"\bdrawer-open\b"))
    page.wait_for_timeout(250)
    assert (
        page.locator(".server-rail").evaluate(
            "(element) => element.getBoundingClientRect().x"
        )
        == 0
    )
    assert (
        page.locator(".workspace-sidebar").evaluate(
            "(element) => element.getBoundingClientRect().right"
        )
        == 312
    )
    page.keyboard.press("Escape")
    expect(page.locator("body")).not_to_have_class(re.compile(r"\bdrawer-open\b"))


def test_server_switching_works_without_javascript(
    workspace_browser: Browser, workspace_url: str
) -> None:
    context = workspace_browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        page.goto(f"{workspace_url}/launch?code=good")
        page.goto(f"{workspace_url}/launch?code=second")
        page.get_by_role("link", name="Test Guild").first.click()
        expect(page).to_have_url(f"{workspace_url}/app?guild=guild")
    finally:
        context.close()
