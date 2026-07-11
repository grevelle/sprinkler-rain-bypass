from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, override

import typer

type RunCommand = Callable[..., subprocess.CompletedProcess[bytes]]


class Prompter(Protocol):
    def text(self, label: str, *, default: str = "") -> str: ...

    def confirm(self, label: str, *, default: bool = False) -> bool: ...

    def secret(self, label: str) -> str: ...


@dataclass(frozen=True, slots=True)
class TyperPrompter(Prompter):
    @override
    def text(self, label: str, *, default: str = "") -> str:
        return typer.prompt(label, default=default)

    @override
    def confirm(self, label: str, *, default: bool = False) -> bool:
        return typer.confirm(label, default=default)

    @override
    def secret(self, label: str) -> str:
        return typer.prompt(label, hide_input=True)


def detect_service_user(
    *,
    run_command: RunCommand | None = None,
    prompter: Prompter | None = None,
) -> str:
    runner = run_command or subprocess.run
    if shutil.which("id") and runner(["id", "-u", "pi"], check=False).returncode == 0:
        if prompter is not None:
            return prompter.text(
                "Service user (needs GPIO access on Pi; root is safest)",
                default="root",
            )
        return "pi"
    return "root"
