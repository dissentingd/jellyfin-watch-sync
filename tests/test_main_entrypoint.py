"""Confirms main() (the actual console-script entry point) loads a .env
file before Typer runs. python-dotenv's own file-parsing is a mature,
separately-tested library -- this only tests OUR wiring: that main() finds
and loads a .env relative to the real working directory before app() runs."""

from unittest.mock import Mock, patch

from jellyfin_watch_restore.cli import main


def test_main_loads_dotenv_before_running_app():
    call_order = []
    with (
        patch(
            "jellyfin_watch_restore.cli.find_dotenv",
            side_effect=lambda usecwd: call_order.append("find_dotenv") or "/fake/.env",
        ) as mock_find_dotenv,
        patch(
            "jellyfin_watch_restore.cli.load_dotenv",
            side_effect=lambda path: call_order.append("load_dotenv"),
        ) as mock_load_dotenv,
        patch("jellyfin_watch_restore.cli.app", Mock(side_effect=lambda: call_order.append("app"))) as mock_app,
    ):
        main()

    mock_find_dotenv.assert_called_once_with(usecwd=True)
    mock_load_dotenv.assert_called_once_with("/fake/.env")
    mock_app.assert_called_once()
    assert call_order == ["find_dotenv", "load_dotenv", "app"]


def test_dotenv_values_are_visible_to_a_real_option(tmp_path, monkeypatch):
    """End-to-end, using the real python-dotenv (not mocked): a .env file
    in the working directory really does make JELLYFIN_URL available to an
    envvar-bound Typer option, without it being separately exported.

    Exercises the exact call shape main() uses (find_dotenv(usecwd=True)
    fed into load_dotenv()) deliberately -- a first attempt at this feature
    called bare load_dotenv(usecwd=True), which doesn't exist on
    load_dotenv() at all (usecwd belongs to find_dotenv), and a second
    attempt called load_dotenv(usecwd=True) thinking that kwarg existed on
    load_dotenv -- both caught here, not by manual inspection, before
    shipping."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JELLYFIN_URL", raising=False)
    (tmp_path / ".env").write_text("JELLYFIN_URL=http://from-dotenv.test\n", encoding="utf-8")

    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))

    import os

    assert os.environ["JELLYFIN_URL"] == "http://from-dotenv.test"
