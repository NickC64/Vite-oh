import re
from typing import Any

_COMPONENT_RE = re.compile(
    r"^(?P<scope>proposal|type):"
    r"(?P<action>veto|confirm-veto|subscribe|acknowledge|nudge|nudge-select|"
    r"workspace|confirm-delete|cancel-delete):"
    r"(?P<resource_id>[0-9a-f-]{36})$"
)


def component_id(scope: str, action: str, resource_id: str) -> str:
    value = f"{scope}:{action}:{resource_id}"
    if len(value) > 100:
        raise ValueError("Discord component IDs cannot exceed 100 characters")
    return value


def parse_component_id(value: str) -> tuple[str, str, str] | None:
    match = _COMPONENT_RE.fullmatch(value)
    if not match:
        return None
    return match.group("scope"), match.group("action"), match.group("resource_id")


def action_row(*components: dict[str, object]) -> list[dict[str, object]]:
    return [{"type": 1, "components": list(components)}]


def button(label: str, style: int, custom_id: str) -> dict[str, object]:
    return {"type": 2, "label": label, "style": style, "custom_id": custom_id}


def proposal_buttons(
    proposal_id: str, *, active: bool = True
) -> list[dict[str, object]]:
    workspace = action_row(
        button(
            "Open workspace",
            2,
            component_id("proposal", "workspace", proposal_id),
        )
    )
    if not active:
        return workspace
    return [
        *action_row(
            button("Veto", 4, component_id("proposal", "veto", proposal_id)),
            button(
                "Acknowledge",
                2,
                component_id("proposal", "acknowledge", proposal_id),
            ),
            button("Subscribe", 1, component_id("proposal", "subscribe", proposal_id)),
            button("Nudge", 2, component_id("proposal", "nudge", proposal_id)),
        ),
        *workspace,
    ]


def user_select(custom_id: str, placeholder: str) -> list[dict[str, object]]:
    return action_row(
        {
            "type": 5,
            "custom_id": custom_id,
            "placeholder": placeholder,
            "min_values": 1,
            "max_values": 1,
        }
    )


def link_button(label: str, url: str) -> list[dict[str, object]]:
    return action_row({"type": 2, "label": label, "style": 5, "url": url})


def delete_confirmation_buttons(
    scope: str, resource_id: str
) -> list[dict[str, object]]:
    return action_row(
        button("Delete", 4, component_id(scope, "confirm-delete", resource_id)),
        button("Cancel", 2, component_id(scope, "cancel-delete", resource_id)),
    )


def text_input(
    custom_id: str,
    label: str,
    *,
    required: bool,
    max_length: int,
    value: str | None = None,
    paragraph: bool = False,
) -> dict[str, object]:
    data: dict[str, object] = {
        "type": 4,
        "custom_id": custom_id,
        "label": label,
        "style": 2 if paragraph else 1,
        "required": required,
        "max_length": max_length,
    }
    if value is not None:
        data["value"] = value
    return data


def modal(title: str, custom_id: str, *inputs: dict[str, object]) -> dict[str, Any]:
    return {
        "type": 9,
        "data": {
            "title": title[:45],
            "custom_id": custom_id,
            "components": [action_row(item)[0] for item in inputs],
        },
    }


def modal_values(data: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}

    def visit(items: list[dict[str, Any]]) -> None:
        for item in items:
            if "value" in item and item.get("custom_id"):
                values[str(item["custom_id"])] = str(item.get("value", ""))
            visit(item.get("components") or [])

    visit(data.get("components") or [])
    return values
