# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""Implementation of tool support over LSP."""

from __future__ import annotations

import copy
import json
import os
import pathlib
import re
import sys
import sysconfig
from typing import Sequence, Any
from typing import Any, Sequence, cast


# **********************************************************
# Update sys.path before importing any bundled libraries.
# **********************************************************
def update_sys_path(path_to_add: str, strategy: str) -> None:
    """Add given path to `sys.path`."""
    if path_to_add not in sys.path and os.path.isdir(path_to_add):
        if strategy == "useBundled":
            sys.path.insert(0, path_to_add)
        elif strategy == "fromEnvironment":
            sys.path.append(path_to_add)


# Ensure that we can import LSP libraries, and other bundled libraries.
update_sys_path(
    os.fspath(pathlib.Path(__file__).parent.parent / "libs"),
    os.getenv("LS_IMPORT_STRATEGY", "useBundled"),
)

# **********************************************************
# Imports needed for the language server.
# **********************************************************
import utils  # noqa: E402
from lsprotocol.types import (  # noqa: E402
    Hover, INITIALIZE,
    MarkupContent, MarkupKind, TEXT_DOCUMENT_CODE_ACTION,
    TEXT_DOCUMENT_DID_CHANGE,
    TEXT_DOCUMENT_DID_CLOSE,
    TEXT_DOCUMENT_DID_OPEN,
    TEXT_DOCUMENT_DID_SAVE,
    AnnotatedTextEdit,
    CodeAction,
    CodeActionKind,
    CodeActionOptions,
    CodeActionParams,
    Diagnostic,
    DiagnosticSeverity,
    DiagnosticTag,
    DidChangeTextDocumentParams,
    DidCloseTextDocumentParams,
    DidOpenTextDocumentParams,
    DidSaveTextDocumentParams,
    InitializeParams,
    MessageType,
    OptionalVersionedTextDocumentIdentifier,
    Position,
    Range,
    TextDocumentEdit,
    TextEdit,
    TraceValues,
    WorkspaceEdit,
)
from pygls import protocol, server, uris, workspace  # noqa: E402
from typing_extensions import TypedDict  # noqa: E402

WORKSPACE_SETTINGS: dict[str, dict[str, Any]] = {}
INTERPRETER_PATHS: dict[str, str] = {}

MAX_WORKERS = 5
LSP_SERVER = server.LanguageServer(
    name="Ruff",
    version="2022.0.23",
    max_workers=MAX_WORKERS,
)


# **********************************************************
# Tool specific code goes below this.
# **********************************************************

TOOL_MODULE = "ruff"

TOOL_DISPLAY = "Ruff"

TOOL_ARGS = ["--no-cache", "--no-fix", "--quiet", "--format", "json", "-"]

# **********************************************************
# Linting features start here
# **********************************************************


@LSP_SERVER.feature(TEXT_DOCUMENT_DID_OPEN)
def did_open(params: DidOpenTextDocumentParams) -> None:
    """LSP handler for textDocument/didOpen request."""
    document = LSP_SERVER.workspace.get_document(params.text_document.uri)
    diagnostics: list[Diagnostic] = _linting_helper(document)
    LSP_SERVER.publish_diagnostics(document.uri, diagnostics)


@LSP_SERVER.feature(TEXT_DOCUMENT_DID_SAVE)
def did_save(params: DidSaveTextDocumentParams) -> None:
    """LSP handler for textDocument/didSave request."""
    document = LSP_SERVER.workspace.get_document(params.text_document.uri)
    diagnostics: list[Diagnostic] = _linting_helper(document)
    LSP_SERVER.publish_diagnostics(document.uri, diagnostics)



NOQA_REGEX = re.compile(r"(?i:# noqa)(?::\s?(?P<codes>([A-Z]+[0-9]+(?:[,\s]+)?)+))?")
CODE_REGEX = re.compile(r"[A-Z]+[0-9]+")


@LSP_SERVER.feature(HOVER)
def hover(params: HoverParams) -> Hover | None:
    """LSP handler for textDocument/hover request."""
    document = LSP_SERVER.workspace.get_document(params.text_document.uri)
    match = NOQA_REGEX.search(document.lines[params.position.line])
    if not match:
        return
    codes = match.group("codes")
    if not codes:
        return

    codes_start = match.start("codes")
    for m in CODE_REGEX.finditer(codes):
        start, end = m.span()
        start += codes_start
        end += codes_start
        if start <= params.position.character < end:
            code = m.group()
            settings = copy.deepcopy(_get_settings_by_document(document))
            result = _run_tool_on_document(
                document,
                settings,
                ["--explain", code, document.path],
                use_stdin=False,
            )
            return Hover(
                contents=MarkupContent(
                    kind=MarkupKind.Markdown,
                    value=(result.stdout or result.stderr).strip(),
                )
            )


@LSP_SERVER.feature(TEXT_DOCUMENT_DID_CHANGE)
def did_change(params: DidChangeTextDocumentParams) -> None:
    """LSP handler for textDocument/didSave request."""
    document = LSP_SERVER.workspace.get_document(params.text_document.uri)
    diagnostics: list[Diagnostic] = _linting_helper(document)
    LSP_SERVER.publish_diagnostics(document.uri, diagnostics)


@LSP_SERVER.feature(TEXT_DOCUMENT_DID_CLOSE)
def did_close(params: DidCloseTextDocumentParams) -> None:
    """LSP handler for textDocument/didClose request."""
    document = LSP_SERVER.workspace.get_document(params.text_document.uri)
    # Publishing empty diagnostics to clear the entries for this file.
    LSP_SERVER.publish_diagnostics(document.uri, [])



def _linting_helper(document: workspace.Document) -> list[Diagnostic]:
    result = _run_tool_on_document(document, use_stdin=True)
    if result is None:
        return []
    return _parse_output_using_regex(result.stdout) if result.stdout else []


def _parse_output_using_regex(content: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    line_at_1 = True
    column_at_1 = True

    line_offset = 1 if line_at_1 else 0
    col_offset = 1 if column_at_1 else 0

    # Ruff's output looks like:
    # [
    #   {
    #     "code": "F841",
    #     "message": "Local variable `x` is assigned to but never used",
    #     "fixed": false,
    #     "location": {
    #       "row": 2,
    #       "column": 5
    #     },
    #     "fix": {
    #       "content: "",
    #       "location": {
    #         "row": 2,
    #         "column: 5
    #       },
    #       "end_location": {
    #         "row": 3,
    #         "column: 0
    #       }
    #     },
    #     "filename": "/path/to/test.py"
    #   },
    #   ...
    # ]
    for check in json.loads(content):
        start = Position(
            line=max([int(check["location"]["row"]) - line_offset, 0]),
            character=int(check["location"]["column"]) - col_offset,
        )
        end = Position(
            line=max([int(check["end_location"]["row"]) - line_offset, 0]),
            character=int(check["end_location"]["column"]) - col_offset,
        )
        diagnostic = Diagnostic(
            range=Range(start=start, end=end),
            message=check.get("message"),
            severity=_get_severity(check["code"], check.get("type", "Error")),
            code=check["code"],
            source=TOOL_DISPLAY,
            data=check.get("fix"),
            tags=(
                [DiagnosticTag.Unnecessary]
                if check["code"]
                in {
                    "F401",  # `module` imported but unused
                    "F841",  # local variable `name` is assigned to but never used
                }
                else None
            ),
        )
        diagnostics.append(diagnostic)

    return diagnostics


def _get_severity(*_codes: list[str]) -> DiagnosticSeverity:
    return DiagnosticSeverity.Warning


# **********************************************************
# Linting features end here
# **********************************************************

# **********************************************************
# Code Action features start here
# **********************************************************


class TextDocument(TypedDict):
    uri: str
    version: int


class Location(TypedDict):
    row: int
    column: int


class Fix(TypedDict):
    content: str
    location: Location
    end_location: Location


@LSP_SERVER.command("ruff.applyAutofix")
def apply_autofix(arguments: tuple[TextDocument]):
    uri = arguments[0]["uri"]
    text_document = LSP_SERVER.workspace.get_document(uri)
    LSP_SERVER.apply_edit(
        _create_workspace_edits(text_document, _formatting_helper(text_document) or []),
        "Ruff: Fix all auto-fixable problems",
    )


@LSP_SERVER.feature(
    TEXT_DOCUMENT_CODE_ACTION,
    CodeActionOptions(
        code_action_kinds=[
            CodeActionKind.SourceOrganizeImports,
            CodeActionKind.QuickFix,
            CodeActionKind.SourceFixAll,
        ],
        resolve_provider=True,
    ),
)
def code_action(params: CodeActionParams) -> list[CodeAction] | None:

    text_document = LSP_SERVER.workspace.get_document(params.text_document.uri)

    if utils.is_stdlib_file(text_document.path):
        # Don't format standard library files.
        # Publishing empty diagnostics clears the entry.
        return None

    # Generate the "Ruff: Organize Imports" edit
    if (
        params.context.only
        and len(params.context.only) == 1
        and CodeActionKind.SourceOrganizeImports in params.context.only
    ):
        results = _formatting_helper(text_document, select="I001")
        if results is not None:
            return [
                CodeAction(
                    title="Ruff: Organize Imports",
                    kind=CodeActionKind.SourceOrganizeImports,
                    data=params.text_document.uri,
                    edit=_create_workspace_edits(text_document, results),
                    diagnostics=[],
                )
            ]
        else:
            return []

    # Generate the "Ruff: Fix All" edit.
    if (
        params.context.only
        and len(params.context.only) == 1
        and CodeActionKind.SourceFixAll in params.context.only
    ):
        return [
            CodeAction(
                title="Ruff: Fix All",
                kind=CodeActionKind.SourceFixAll,
                data=params.text_document.uri,
                edit=_create_workspace_edits(
                    text_document, _formatting_helper(text_document) or []
                ),
                diagnostics=[
                    diagnostic
                    for diagnostic in params.context.diagnostics
                    if diagnostic.source == "Ruff" and diagnostic.data is not None
                ],
            ),
        ]

    actions: list[CodeAction] = []

    # Add "Ruff: Organize Imports" as a supported action.
    if (
        not params.context.only
        or CodeActionKind.SourceOrganizeImports in params.context.only
    ):
        actions.append(
            CodeAction(
                title="Ruff: Organize Imports",
                kind=CodeActionKind.SourceOrganizeImports,
                data=params.text_document.uri,
                edit=None,
                diagnostics=[],
            ),
        )

    # Add "Ruff: Fix All" as a supported action.
    if not params.context.only or CodeActionKind.SourceFixAll in params.context.only:
        actions.append(
            CodeAction(
                title="Ruff: Fix All",
                kind=CodeActionKind.SourceFixAll,
                data=params.text_document.uri,
                edit=None,
                diagnostics=[],
            ),
        )

    # Add "Ruff: Autofix" for every fixable diagnostic.
    if not params.context.only or CodeActionKind.QuickFix in params.context.only:
        for diagnostic in params.context.diagnostics:
            if diagnostic.source == "Ruff":
                if diagnostic.data is not None:
                    actions.append(
                        CodeAction(
                            title=(
                                f"Ruff: Fix {diagnostic.code}"
                                if diagnostic.code
                                else "Ruff: Fix"
                            ),
                            kind=CodeActionKind.QuickFix,
                            data=params.text_document.uri,
                            edit=_create_workspace_edit(
                                text_document, cast(Fix, diagnostic.data)
                            ),
                            diagnostics=[diagnostic],
                        ),
                    )

    return actions if actions else None


def _formatting_helper(
    document: workspace.Document, *, select: str | None = None
) -> list[TextEdit] | None:
    result = _run_tool_on_document(
        document,
        use_stdin=True,
        extra_args=["--fix", "--select", select] if select else ["--fix"],
    )
    if result is None:
        return []

    if result.stdout:
        new_source = _match_line_endings(document, result.stdout)

        # Skip last line ending in a notebook cell.
        if document.uri.startswith("vscode-notebook-cell"):
            if new_source.endswith("\r\n"):
                new_source = new_source[:-2]
            elif new_source.endswith("\n"):
                new_source = new_source[:-1]

        if new_source != document.source:
            return [
                TextEdit(
                    range=Range(
                        start=Position(line=0, character=0),
                        end=Position(line=len(document.lines), character=0),
                    ),
                    new_text=new_source,
                )
            ]
    return None


def _create_workspace_edits(
    document: workspace.Document,
    results: Sequence[TextEdit | AnnotatedTextEdit],
) -> WorkspaceEdit:
    return WorkspaceEdit(
        document_changes=[
            TextDocumentEdit(
                text_document=OptionalVersionedTextDocumentIdentifier(
                    uri=document.uri,
                    version=0 if document.version is None else document.version,
                ),
                edits=list(results),
            )
        ],
    )


def _create_workspace_edit(document: workspace.Document, fix: Fix) -> WorkspaceEdit:
    return WorkspaceEdit(
        document_changes=[
            TextDocumentEdit(
                text_document=OptionalVersionedTextDocumentIdentifier(
                    uri=document.uri,
                    version=0 if document.version is None else document.version,
                ),
                edits=[
                    TextEdit(
                        range=Range(
                            start=Position(
                                line=fix["location"]["row"] - 1,
                                character=fix["location"]["column"],
                            ),
                            end=Position(
                                line=fix["end_location"]["row"] - 1,
                                character=fix["end_location"]["column"],
                            ),
                        ),
                        new_text=fix["content"],
                    )
                ],
            )
        ],
    )


def _get_line_endings(lines: list[str]) -> str | None:
    """Returns line endings used in the text."""
    try:
        if lines[0][-2:] == "\r\n":
            return "\r\n"
        return "\n"
    except Exception:
        return None


def _match_line_endings(document: workspace.Document, text: str) -> str:
    """Ensures that the edited text line endings matches the document line endings."""
    expected = _get_line_endings(document.source.splitlines(keepends=True))
    actual = _get_line_endings(text.splitlines(keepends=True))
    if actual == expected or actual is None or expected is None:
        return text
    return text.replace(actual, expected)


# **********************************************************
# Code Action features ends here
# **********************************************************


# **********************************************************
# Required Language Server Initialization and Exit handlers.
# **********************************************************
@LSP_SERVER.feature(INITIALIZE)
def initialize(params: InitializeParams) -> None:
    """LSP handler for initialize request."""
    settings = params.initialization_options["settings"]  # type: ignore
    _update_workspace_settings(settings)

    if isinstance(LSP_SERVER.lsp, protocol.LanguageServerProtocol):
        if any(setting["logLevel"] == "debug" for setting in settings):
            LSP_SERVER.lsp.trace = TraceValues.Verbose
        elif any(
            setting["logLevel"] in ["error", "warn", "info"] for setting in settings
        ):
            LSP_SERVER.lsp.trace = TraceValues.Messages
        else:
            LSP_SERVER.lsp.trace = TraceValues.Off


# *****************************************************
# Internal functional and settings management APIs.
# *****************************************************
def _get_default_settings(workspace_path: str) -> dict[str, Any]:
    return {
        "check": False,
        "workspaceFS": workspace_path,
        "workspace": uris.from_fs_path(workspace_path),
        "logLevel": "error",
        "args": [],
        "path": [],
        "interpreter": [sys.executable],
        "importStrategy": "useBundled",
        "showNotifications": "off",
    }


def _update_workspace_settings(settings) -> None:
    if not settings:
        key = os.getcwd()
        WORKSPACE_SETTINGS[key] = _get_default_settings(key)
        return

    for setting in settings:
        key = uris.to_fs_path(setting["workspace"])
        WORKSPACE_SETTINGS[key] = {
            **setting,
            "workspaceFS": key,
        }


def _get_document_key(document: workspace.Document) -> str | None:
    document_workspace = pathlib.Path(document.path)
    workspaces = {s["workspaceFS"] for s in WORKSPACE_SETTINGS.values()}

    while document_workspace != document_workspace.parent:
        if str(document_workspace) in workspaces:
            return str(document_workspace)
        document_workspace = document_workspace.parent
    return None


def _get_settings_by_document(document: workspace.Document | None) -> dict[str, Any]:
    if document is None or document.path is None:
        return list(WORKSPACE_SETTINGS.values())[0]

    key = _get_document_key(document)
    if key is None:
        key = os.fspath(pathlib.Path(document.path).parent)
        return _get_default_settings(key)

    return WORKSPACE_SETTINGS[str(key)]


# *****************************************************
# Internal execution APIs.
# *****************************************************
def _run_tool_on_document(
    document: workspace.Document,
    args: Sequence[str] = [],
    use_stdin: bool = False,
) -> utils.RunResult | None:
    """Runs tool on the given document.

    If `use_stdin` is `True` then contents of the document is passed to the tool via
    stdin.
    """
    if str(document.uri).startswith("vscode-notebook-cell"):
        # Skip notebook cells
        return None

    if utils.is_stdlib_file(document.path):
        log_warning(f"Skipping standard library file: {document.path}")
        return None

    # Deep copy, to prevent accidentally updating global settings.
    settings = copy.deepcopy(_get_settings_by_document(document))

    cwd = settings["workspaceFS"]

    bundled_path = os.fspath(
        pathlib.Path(__file__).parent.parent / "libs" / "bin" / TOOL_MODULE
    )
    if settings["path"]:
        # 'path' setting takes priority over everything.
        argv = settings["path"]
    elif settings["importStrategy"] == "useBundled":
        # If we're loading from the bundle, use the absolute path.
        argv = [bundled_path]
    elif settings["interpreter"] and not utils.is_current_interpreter(
        settings["interpreter"][0]
    ):
        # If there is a different interpreter set, find its scripts path.
        if settings["interpreter"][0] not in INTERPRETER_PATHS:
            INTERPRETER_PATHS[settings["interpreter"][0]] = utils.scripts(
                settings["interpreter"][0]
            )

        path: str = os.path.join(
            INTERPRETER_PATHS[settings["interpreter"][0]], TOOL_MODULE
        )
        if os.path.isfile(path):
            argv = [path]
        else:
            argv = [bundled_path]
    else:
        # If the interpreter is same as the interpreter running this process, get the
        # scripts path directly.
        path = os.path.join(sysconfig.get_path("scripts"), TOOL_MODULE)
        if os.path.isfile(path):
            argv = [path]
        else:
            argv = [bundled_path]

    argv += args

    log_to_output(f"Running Ruff with: {argv}")
    result: utils.RunResult = utils.run_path(
        argv=argv,
        use_stdin=use_stdin,
        cwd=cwd,
        source=document.source.replace("\r\n", "\n"),
    )
    if result.stderr:
        log_to_output(result.stderr)

    return result


# *****************************************************
# Logging and notification.
# *****************************************************
def log_to_output(message: str, msg_type: MessageType = MessageType.Log) -> None:
    LSP_SERVER.show_message_log(message, msg_type)


def log_error(message: str) -> None:
    LSP_SERVER.show_message_log(message, MessageType.Error)
    if os.getenv("LS_SHOW_NOTIFICATION", "off") in [
        "onError",
        "onWarning",
        "always",
    ]:
        LSP_SERVER.show_message(message, MessageType.Error)


def log_warning(message: str) -> None:
    LSP_SERVER.show_message_log(message, MessageType.Warning)
    if os.getenv("LS_SHOW_NOTIFICATION", "off") in ["onWarning", "always"]:
        LSP_SERVER.show_message(message, MessageType.Warning)


def log_always(message: str) -> None:
    LSP_SERVER.show_message_log(message, MessageType.Info)
    if os.getenv("LS_SHOW_NOTIFICATION", "off") in ["always"]:
        LSP_SERVER.show_message(message, MessageType.Info)


# *****************************************************
# Start the server.
# *****************************************************
if __name__ == "__main__":
    LSP_SERVER.start_io()
