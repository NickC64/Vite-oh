import re
import socket
import threading
import time
from collections.abc import Iterator
from datetime import timedelta

import pytest
import uvicorn
from playwright.sync_api import Browser, Page, expect, sync_playwright

from tests.test_workspace import web_system
from viteoh.domain import GuildConfig, Proposal, ProposalStatus, ProposalType, utcnow


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
    repository.types[("guild", "custom-type")] = ProposalType(
        id="custom-type",
        guild_id="guild",
        name="Policy",
        normalized_name="policy",
        description="Changes to server policy",
        created_by="admin",
        updated_by="admin",
        created_at=now,
        updated_at=now,
    )
    proposal = Proposal(
        id=repository.next_id,
        guild_id="guild",
        guild_name="Test Guild",
        output_channel_id="channel",
        title="Browser proposal",
        normalized_title="browser proposal",
        context="Visible browser-test context.",
        reservation_id="browser-proposal",
        status=ProposalStatus.ACTIVE,
        created_at=now,
        deadline_at=now + timedelta(hours=1),
        message_id="browser-message",
    )
    repository.proposals[proposal.id] = proposal
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
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: (
            console_errors.append(message.text) if message.type == "error" else None
        ),
    )
    page.goto(f"{workspace_url}/launch?code=good")
    expect(page.get_by_role("heading", name="Proposals", exact=True)).to_be_visible()
    expect(page.locator(".viteoh-mark")).to_have_count(0)
    page.goto(f"{workspace_url}/launch?code=second")

    expect(page.get_by_role("link", name="Test Guild")).to_be_visible()
    expect(page.get_by_role("link", name="Second Guild")).to_be_visible()
    page.get_by_role("link", name="Test Guild").first.click()
    expect(page).to_have_url(f"{workspace_url}/app?guild=guild")
    page.get_by_role("link", name="History archive").click()
    expect(page.get_by_role("heading", name="History archive")).to_be_visible()
    page.get_by_role("link", name="Overview").click()
    page.get_by_label("Workspace navigation").get_by_role(
        "link", name="Create proposal"
    ).click()
    page.get_by_role("spinbutton", name="Hours").fill("2")
    page.get_by_role("spinbutton", name="Minutes").fill("0")
    expect(page.locator("[data-duration-summary]")).to_have_text("Selected: 2 hours.")
    expect(page.locator("[data-preview-deadline]")).to_have_text(
        "Fixed 2 hours after creation"
    )
    page.get_by_role("link", name="Overview").click()
    page.get_by_role("link", name="Preferences").click()
    switch = page.locator(".switch-control").first
    assert switch.evaluate(
        """element => {
          const box = element.getBoundingClientRect();
          return box.width === 42 && box.height === 24;
        }"""
    )
    page.locator(".switch-input").first.check()
    assert switch.evaluate(
        """element => {
          const track = element.getBoundingClientRect();
          const thumb = element.firstElementChild.getBoundingClientRect();
          return thumb.left >= track.left && thumb.right <= track.right;
        }"""
    )
    page.get_by_role("link", name="Proposal types").click()
    page.get_by_role("button", name="Edit").click()
    expect(
        page.get_by_role("dialog").get_by_role("heading", name="Edit Policy")
    ).to_be_visible()
    page.get_by_role("button", name="Cancel").click()
    page.get_by_role("button", name="Delete").click()
    expect(
        page.get_by_role("dialog").get_by_role("heading", name="Delete Policy?")
    ).to_be_visible()
    page.get_by_role("button", name="Cancel").click()
    page.get_by_role("link", name="Overview").click()
    page.get_by_role("link", name="Browser proposal").click()
    expect(page.get_by_role("link", name="Back to proposals")).to_be_visible()
    expect(page.locator("[data-local-datetime=long]")).not_to_contain_text("UTC")
    page.get_by_role("button", name="Nudge member").click()
    nudge_dialog = page.locator("#nudge-proposal-dialog")
    expect(nudge_dialog).to_be_visible()
    nudge_dialog.locator("[data-member-search]").fill("ta")
    expect(nudge_dialog.get_by_role("button", name="Target Member")).to_be_visible()
    nudge_dialog.get_by_role("button", name="Target Member").click()
    expect(
        nudge_dialog.get_by_role("button", name="Send anonymous nudge")
    ).to_be_enabled()
    nudge_dialog.get_by_role("button", name="Cancel").click()
    rendered_time = page.evaluate(
        """() => {
          const container = document.createElement("div");
          const time = document.createElement("time");
          time.dataset.relative = new Date(Date.now() + 120000).toISOString();
          time.textContent = "<t:123:R>";
          container.append(time);
          document.body.append(container);
          document.dispatchEvent(
            new CustomEvent("htmx:afterSwap", { detail: { target: container } })
          );
          return time.textContent;
        }"""
    )
    assert rendered_time == "in 2 minutes"

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
    assert not console_errors


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
