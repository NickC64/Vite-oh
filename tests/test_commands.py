from viteoh.commands import COMMANDS


def test_generalized_command_schema_uses_autocomplete_and_user_picker() -> None:
    commands = {str(command["name"]): command for command in COMMANDS}
    new_options = {str(option["name"]): option for option in commands["new"]["options"]}
    assert new_options["title"]["required"] is True
    assert new_options["context"]["max_length"] == 1000
    delete_option = commands["delete"]["options"][0]
    assert delete_option["name"] == "proposal"
    assert delete_option["autocomplete"] is True
    nudge_options = commands["nudge"]["options"]
    assert nudge_options[0]["autocomplete"] is True
    assert nudge_options[1]["type"] == 6
