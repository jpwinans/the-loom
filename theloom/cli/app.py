"""The Loom CLI — generated from the command registry.

Every command comes from theloom/cli/registry.py (one descriptor list); none
are defined ad hoc, so the surface stays consistent and testable. Protocol:
JSON in (argument or stdin), JSON out (stdout), {error, code} to stderr with
exit 1.
"""

from __future__ import annotations

from typing import Annotated

import typer

from theloom import __version__
from theloom.cli.io import output_error, output_success, parse_json_input
from theloom.cli.registry import COMMANDS, CommandDescriptor, run_handler
from theloom.cli.schema import top_level_required
from theloom.config import load_config
from theloom.errors import InputRequiredError, ParseError
from theloom.store.multigraph import MultiGraph

app = typer.Typer(
    name="loom",
    help=("The Loom — knowledge-graph substrate (Python, built on FalkorDB)."),
    no_args_is_help=True,
    add_completion=False,
)


def _generate_docs_callback(value: bool) -> None:
    """Eager flag: print the registry-derived command catalog and exit
    (``loom --generate-docs > COMMANDS.md``)."""
    if value:
        from theloom.cli.docs import generate_docs

        typer.echo(generate_docs(), nl=False)
        raise typer.Exit(0)


@app.callback()
def _root(
    generate_docs: Annotated[
        bool,
        typer.Option(
            "--generate-docs",
            help="Print the command catalog (COMMANDS.md content) and exit.",
            callback=_generate_docs_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """Keep Typer in subcommand mode so ``loom <command>`` is stable."""


@app.command()
def version() -> None:
    """Print the CLI version."""
    typer.echo(__version__)


@app.command()
def init(
    config_dir: Annotated[
        str | None,
        typer.Option("--config-dir", help="Configuration directory (default: ~/.loom)"),
    ] = None,
) -> None:
    """Initialize The Loom configuration directory and config file."""
    from pathlib import Path

    from theloom.operations.init import run_init

    try:
        target = Path(config_dir) if config_dir else Path.home() / ".loom"
        result = run_init(target, _build_multigraph())
    except Exception as error:
        output_error(error)
        raise typer.Exit(1) from error
    output_success(result)


def _build_multigraph() -> MultiGraph:
    from falkordb import FalkorDB  # deferred: only commands that hit the store pay

    config = load_config()
    db = FalkorDB(host=config.host, port=config.port)
    return MultiGraph(db, db.connection, default_graph=config.default_graph)


def _schema_hint(descriptor: CommandDescriptor) -> str:
    """The pointer appended to PARSE_ERROR/INPUT_REQUIRED messages: no field
    has been parsed yet at that point, so the most useful "expected shape" is
    a pointer to the full schema plus the top-level fields the caller owes."""
    hint = f"Run `loom {descriptor.name} --schema` for the complete input schema."
    required = top_level_required(descriptor.input_model)
    if required:
        hint += f" Required fields: {', '.join(required)}."
    return hint


def _make_command(descriptor: CommandDescriptor) -> None:
    def command(
        json_input: Annotated[
            str | None,
            typer.Argument(help="JSON input (or pipe via stdin)", show_default=False),
        ] = None,
        schema: Annotated[
            bool,
            typer.Option(
                "--schema",
                help="Print this command's input JSON Schema (from its Pydantic model) and exit.",
                is_eager=True,
            ),
        ] = False,
    ) -> None:
        if schema:
            output_success(descriptor.input_model.model_json_schema())
            return
        try:
            input_doc = parse_json_input(json_input, allow_empty=descriptor.allow_empty)
        except (ParseError, InputRequiredError) as error:
            output_error(type(error)(f"{error.message} {_schema_hint(descriptor)}"))
            raise typer.Exit(1) from error
        except Exception as error:
            output_error(error)
            raise typer.Exit(1) from error
        try:
            result = run_handler(descriptor.name, input_doc, _build_multigraph())
        except Exception as error:
            output_error(error)
            raise typer.Exit(1) from error
        output_success(result)

    command.__doc__ = descriptor.summary
    app.command(name=descriptor.name)(command)


for _descriptor in COMMANDS:
    _make_command(_descriptor)


def main() -> None:
    """Console-script entry point (``loom`` / ``the-loom``)."""
    app()


if __name__ == "__main__":
    main()
