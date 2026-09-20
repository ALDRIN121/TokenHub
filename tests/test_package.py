from tokenhub.cli import main


def test_console_entrypoint_is_importable() -> None:
    assert callable(main)
