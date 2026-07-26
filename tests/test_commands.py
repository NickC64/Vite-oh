from viteoh.commands import COMMANDS, command_path_and_options, focused_option


def test_proposal_command_schema_is_a_single_private_launcher() -> None:
    assert COMMANDS == [
        {
            "name": "proposal",
            "description": "Open your private Vite-oh proposal workspace",
            "type": 1,
        }
    ]


def test_nested_command_and_focused_option_parsing() -> None:
    data = {
        "name": "proposal",
        "options": [
            {
                "name": "type",
                "type": 2,
                "options": [
                    {
                        "name": "edit",
                        "type": 1,
                        "options": [
                            {
                                "name": "type",
                                "value": "abc",
                                "focused": True,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    assert command_path_and_options(data) == (
        ("proposal", "type", "edit"),
        {"type": "abc"},
    )
    assert focused_option(data)[0] == ("proposal", "type", "edit")
    assert focused_option(data)[1]["value"] == "abc"
