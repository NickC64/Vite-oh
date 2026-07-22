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
        "template",
    }
    assert subcommands["create"]["options"][0]["autocomplete"] is True
    assert subcommands["delete"]["options"][0]["autocomplete"] is True
    assert subcommands["nudge"]["options"][1]["type"] == 6
    template_commands = {
        str(option["name"]): option for option in subcommands["template"]["options"]
    }
    assert set(template_commands) == {"create", "edit", "delete", "list"}


def test_nested_command_and_focused_option_parsing() -> None:
    data = {
        "name": "proposal",
        "options": [
            {
                "name": "template",
                "type": 2,
                "options": [
                    {
                        "name": "edit",
                        "type": 1,
                        "options": [
                            {
                                "name": "template",
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
        ("proposal", "template", "edit"),
        {"template": "abc"},
    )
    assert focused_option(data)[0] == ("proposal", "template", "edit")
    assert focused_option(data)[1]["value"] == "abc"
