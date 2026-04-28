"""``startserviceapp`` management command — service-oriented Django app scaffold.

Mirrors Django's built-in ``startapp`` but writes a layout where each
"slot" (services, selectors, validators, serializers, utils) is a package
of its own, and ``models`` / ``views`` are packages rather than single
files. Backed by Django's :class:`~django.core.management.templates.TemplateCommand`,
so behaviour around naming, target directories, and template rendering
matches ``startapp`` exactly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.core.management.templates import TemplateCommand


class Command(TemplateCommand):
    help = (
        "Creates a service-oriented Django app directory layout for the "
        "given app name in the current directory or optionally in the "
        "given directory."
    )
    missing_args_message = "You must provide an application name."

    def handle(self, *_args: Any, **options: Any) -> None:
        app_name: str = options.pop("name")
        target: str | None = options.pop("directory")
        if not options.get("template"):
            template_dir: Path = (
                Path(__file__).resolve().parent.parent / "templates" / "service_app"
            )
            options["template"] = str(template_dir)
        super().handle("app", app_name, target, **options)
