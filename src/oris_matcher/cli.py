"""Command-line entry point for the ORIS matcher."""

import typer

app = typer.Typer(help="Match BoQ lines to an ORIS material library.")


@app.callback(invoke_without_command=True)
def main() -> None:
    """Run the matcher (commands land in G1)."""
