COMMANDS: list[dict[str, object]] = [
    {
        "name": "setup",
        "description": "Configure this server's proposal channel and vote duration",
        "type": 1,
        "options": [
            {
                "name": "channel",
                "description": "Channel for proposal announcements",
                "type": 7,
                "required": False,
                "channel_types": [0, 5],
            },
            {
                "name": "duration_minutes",
                "description": "Voting duration from 1 minute to 7 days",
                "type": 4,
                "required": False,
                "min_value": 1,
                "max_value": 10080,
            },
        ],
    },
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
        "description": "(Manage Server) Delete a specific proposal",
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

`/setup [channel] [duration_minutes]` — Configure or view this server.
`/new <name>` — Create a proposal using this server's configured duration.
`/sub` — Subscribe to notifications about new proposals.
`/unsub` — Stop notifications about new proposals.
`/view` — View active proposals.
`/delete <name>` — Delete a proposal (Manage Server required).
`/help` — Show this help.

Use **Veto** on a proposal to veto anonymously. Use **Subscribe** to receive
updates about that proposal."""
