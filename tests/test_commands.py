from viteoh.commands import COMMANDS, command_path_and_options, focused_option


def test_proposal_command_schema_is_nested_and_uses_autocomplete() -> None:
    assert [command["name"] for command in COMMANDS] == ["proposal"]
    subcommands = {str(option["name"]): option for option in COMMANDS[0]["options"]}
    assert set(subcommands) == {
        "create",
        "list",
        "nudge",
        "delete",
        "configure",
        "preferences",
        "help",
        "type",
    }
    create_options = {
        str(option["name"]): option for option in subcommands["create"]["options"]
    }
    assert create_options["title"]["required"] is True
    assert create_options["type"]["autocomplete"] is True
    assert create_options["context"]["max_length"] == 1000
    assert subcommands["delete"]["options"][0]["autocomplete"] is True
    assert subcommands["nudge"]["options"][1]["type"] == 6
    type_commands = {
        str(option["name"]): option for option in subcommands["type"]["options"]
    }
    assert set(type_commands) == {"create", "edit", "delete", "list"}


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
