from typing import Any


def _option(
    name: str,
    description: str,
    option_type: int,
    *,
    required: bool = False,
    autocomplete: bool = False,
    **extra: object,
) -> dict[str, object]:
    return {
        "name": name,
        "description": description,
        "type": option_type,
        "required": required,
        **({"autocomplete": True} if autocomplete else {}),
        **extra,
    }


COMMANDS: list[dict[str, object]] = [
    {
        "name": "proposal",
        "description": "Create and manage consent-based proposals",
        "type": 1,
        "options": [
            {
                "name": "create",
                "description": "Create a guided proposal",
                "type": 1,
                "options": [
                    _option(
                        "template",
                        "Proposal template (defaults to General)",
                        3,
                        autocomplete=True,
                    )
                ],
            },
            {"name": "list", "description": "List active proposals", "type": 1},
            {
                "name": "nudge",
                "description": "Anonymously notify a member about a proposal",
                "type": 1,
                "options": [
                    _option(
                        "proposal",
                        "Active proposal",
                        3,
                        required=True,
                        autocomplete=True,
                    ),
                    _option("user", "Member to notify", 6, required=True),
                ],
            },
            {
                "name": "delete",
                "description": "Delete an active proposal (Manage Server)",
                "type": 1,
                "options": [
                    _option(
                        "proposal",
                        "Active proposal",
                        3,
                        required=True,
                        autocomplete=True,
                    )
                ],
            },
            {
                "name": "configure",
                "description": "Configure this server (Manage Server)",
                "type": 1,
                "options": [
                    _option(
                        "channel",
                        "Channel for proposal announcements",
                        7,
                        channel_types=[0, 5],
                    ),
                    _option(
                        "duration_minutes",
                        "Proposal duration from 1 minute to 7 days",
                        4,
                        min_value=1,
                        max_value=10080,
                    ),
                ],
            },
            {
                "name": "preferences",
                "description": "View or change your proposal notifications",
                "type": 1,
                "options": [
                    _option("new_proposals", "DM me about new proposals", 5),
                    _option("nudges", "Allow anonymous proposal nudges", 5),
                ],
            },
            {
                "name": "help",
                "description": "Explain Vite-oh and its commands",
                "type": 1,
            },
            {
                "name": "template",
                "description": "Manage this server's proposal templates",
                "type": 2,
                "options": [
                    {
                        "name": "create",
                        "description": "Create a custom template (Manage Server)",
                        "type": 1,
                        "options": [
                            _option(
                                "context_required",
                                "Require context when using this template",
                                5,
                            )
                        ],
                    },
                    {
                        "name": "edit",
                        "description": "Edit a custom template (Manage Server)",
                        "type": 1,
                        "options": [
                            _option(
                                "template",
                                "Custom template to edit",
                                3,
                                required=True,
                                autocomplete=True,
                            ),
                            _option(
                                "context_required",
                                "Change whether context is required",
                                5,
                            ),
                        ],
                    },
                    {
                        "name": "delete",
                        "description": "Delete a custom template (Manage Server)",
                        "type": 1,
                        "options": [
                            _option(
                                "template",
                                "Custom template to delete",
                                3,
                                required=True,
                                autocomplete=True,
                            )
                        ],
                    },
                    {
                        "name": "list",
                        "description": "List built-in and custom templates",
                        "type": 1,
                    },
                ],
            },
        ],
    }
]


HELP_TEXT = """**Vite-oh proposals**

Proposals pass at their fixed deadline unless a server member anonymously vetoes.
Acknowledgement records only that you saw a proposal; it is not a vote of support.

`/proposal create [template]` — Open a guided proposal form.
`/proposal list` — List active proposals.
`/proposal nudge <proposal> <user>` — Anonymously notify one member.
`/proposal preferences` — View or change notifications and nudges.
`/proposal configure` — Configure this server (Manage Server).
`/proposal delete <proposal>` — Delete a proposal (Manage Server).
`/proposal template ...` — Manage guided templates.
`/proposal help` — Show this help."""


def command_path_and_options(
    data: dict[str, Any],
) -> tuple[tuple[str, ...], dict[str, Any]]:
    path = [str(data.get("name", ""))]
    options = data.get("options") or []
    while len(options) == 1 and int(options[0].get("type", 0)) in {1, 2}:
        branch = options[0]
        path.append(str(branch.get("name", "")))
        options = branch.get("options") or []
    return tuple(path), {
        str(option.get("name", "")): option.get("value")
        for option in options
        if int(option.get("type", 0)) not in {1, 2}
    }


def focused_option(
    data: dict[str, Any],
) -> tuple[tuple[str, ...], dict[str, Any] | None]:
    path = [str(data.get("name", ""))]
    options = data.get("options") or []
    while True:
        branch = next(
            (option for option in options if int(option.get("type", 0)) in {1, 2}),
            None,
        )
        if branch is None:
            break
        path.append(str(branch.get("name", "")))
        options = branch.get("options") or []
    return tuple(path), next(
        (option for option in options if option.get("focused")), None
    )
