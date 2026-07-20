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
        "description": "Create a new consent-based proposal",
        "type": 1,
        "options": [
            {
                "name": "title",
                "description": "Short title for the proposal",
                "type": 3,
                "required": True,
                "max_length": 100,
            },
            {
                "name": "context",
                "description": "Optional explanation or supporting link",
                "type": 3,
                "required": False,
                "max_length": 1000,
            },
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
        "description": "(Manage Server) Delete an active proposal",
        "type": 1,
        "options": [
            {
                "name": "proposal",
                "description": "Active proposal to delete",
                "type": 3,
                "required": True,
                "autocomplete": True,
            }
        ],
    },
    {
        "name": "nudge",
        "description": "Anonymously notify a member about an active proposal",
        "type": 1,
        "options": [
            {
                "name": "proposal",
                "description": "Active proposal to bring to their attention",
                "type": 3,
                "required": True,
                "autocomplete": True,
            },
            {
                "name": "user",
                "description": "Member to notify",
                "type": 6,
                "required": True,
            },
        ],
    },
    {
        "name": "nudges",
        "description": "View or change whether this server may nudge you",
        "type": 1,
        "options": [
            {
                "name": "enabled",
                "description": "Allow anonymous proposal nudges from this server",
                "type": 5,
                "required": False,
            }
        ],
    },
    {"name": "help", "description": "Show available commands", "type": 1},
]

HELP_TEXT = """**Available Commands**

`/setup [channel] [duration_minutes]` — Configure or view this server.
`/new <title> [context]` — Create a general proposal.
`/sub` — Subscribe to notifications about new proposals.
`/unsub` — Stop notifications about new proposals.
`/view` — View active proposals.
`/delete <proposal>` — Delete a proposal (Manage Server required).
`/nudge <proposal> <user>` — Anonymously notify one member.
`/nudges [enabled]` — View or change your nudge preference.
`/help` — Show this help.

Use **Veto** to object anonymously, **Acknowledge** to privately record that
you saw a proposal, and **Subscribe** to receive updates."""
