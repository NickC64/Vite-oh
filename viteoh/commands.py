from typing import Any

COMMANDS: list[dict[str, object]] = [
    {
        "name": "proposal",
        "description": "Open your private Vite-oh proposal workspace",
        "type": 1,
    }
]


HELP_TEXT = """**Vite-oh proposals**

Proposals pass at their fixed deadline unless a server member anonymously vetoes.
Acknowledgement records only that you saw a proposal; it is not support.

Run `/proposal` to open your private workspace for creating and browsing
proposals, preferences, server settings, and proposal types."""


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
