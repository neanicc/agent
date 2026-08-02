from loopguard.cli_docs import _normalize_help


def test_normalize_help_removes_terminal_styles_and_trailing_space() -> None:
    rendered = "\x1b[1;33mUsage:\x1b[0m loopguard  \nnext\t \n"

    assert _normalize_help(rendered) == "Usage: loopguard\nnext"
