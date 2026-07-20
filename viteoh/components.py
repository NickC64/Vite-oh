import re

_COMPONENT_RE = re.compile(
    r"^proposal:(?P<action>veto|confirm-veto|cancel-veto|subscribe):"
    r"(?P<proposal_id>[0-9a-f-]{36})$"
)


def component_id(action: str, proposal_id: str) -> str:
    value = f"proposal:{action}:{proposal_id}"
    if len(value) > 100:
        raise ValueError("Discord component IDs cannot exceed 100 characters")
    return value


def parse_component_id(value: str) -> tuple[str, str] | None:
    match = _COMPONENT_RE.fullmatch(value)
    if not match:
        return None
    return match.group("action"), match.group("proposal_id")


def action_row(*buttons: dict[str, object]) -> list[dict[str, object]]:
    return [{"type": 1, "components": list(buttons)}]


def button(label: str, style: int, custom_id: str) -> dict[str, object]:
    return {"type": 2, "label": label, "style": style, "custom_id": custom_id}


def proposal_buttons(proposal_id: str) -> list[dict[str, object]]:
    return action_row(
        button("Veto", 4, component_id("veto", proposal_id)),
        button("Subscribe", 1, component_id("subscribe", proposal_id)),
    )


def veto_confirmation_buttons(proposal_id: str) -> list[dict[str, object]]:
    return action_row(
        button("Yes", 4, component_id("confirm-veto", proposal_id)),
        button("No", 2, component_id("cancel-veto", proposal_id)),
    )
