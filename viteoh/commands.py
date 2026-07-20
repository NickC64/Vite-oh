COMMANDS: list[dict[str, object]] = [
    {
        "name": "new",
        "description": "Propose a new member",
        "type": 1,
        "options": [
            {
                "name": "name",
                "description": "Name of the proposed member",
                "type": 3,
                "required": True,
                "max_length": 100,
            }
        ],
    },
    {
        "name": "sub",
        "description": "Subscribe to new proposal notifications",
        "type": 1,
    },
    {
        "name": "unsub",
        "description": "Unsubscribe from new proposal notifications",
        "type": 1,
    },
    {"name": "view", "description": "View all current proposals", "type": 1},
    {
        "name": "delete",
        "description": "(Owner only) Delete a specific proposal",
        "type": 1,
        "options": [
            {
                "name": "name",
                "description": "Name of the member being proposed",
                "type": 3,
                "required": True,
                "max_length": 100,
            }
        ],
    },
    {"name": "help", "description": "Show available commands", "type": 1},
]

HELP_TEXT = """**Available Commands**

`/new <name>` — Create a proposal that passes after 48 hours unless vetoed.
`/sub` — Subscribe to notifications about new proposals.
`/unsub` — Stop notifications about new proposals.
`/view` — View active proposals.
`/help` — Show this help.

Use **Veto** on a proposal to veto anonymously. Use **Subscribe** to receive
updates about that proposal."""
