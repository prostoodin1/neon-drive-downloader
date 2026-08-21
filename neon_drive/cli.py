from __future__ import annotations

import argparse
import json
import sys

from PySide6.QtCore import QCoreApplication

from .single_instance import send_request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="NeonDriveCLI",
        description="Скрытый локальный интерфейс Neon Drive для AI-агентов.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Получить состояние приложения и очереди")
    commands.add_parser("activate", help="Показать уже запущенное окно Neon Drive")
    commands.add_parser("pause", help="Поставить активную передачу на паузу")
    commands.add_parser("resume", help="Продолжить активную передачу")
    commands.add_parser("stop", help="Безопасно остановить активную очередь")

    add = commands.add_parser("add", help="Добавить загрузку или выгрузку")
    add.add_argument("--direction", choices=("download", "upload"), default="download")
    add.add_argument("--source", action="append", required=True, dest="sources")
    add.add_argument("--destination", required=True)
    add.add_argument("--profile", choices=("slow", "optimal", "maximum"), default="optimal")
    add.add_argument("--start", action="store_true", help="Сразу запустить созданную очередь")
    return parser


def request_from_args(args: argparse.Namespace) -> dict:
    payload = {"command": args.command}
    if args.command == "add":
        payload.update(
            direction=args.direction,
            sources=args.sources,
            destination=args.destination,
            profile=args.profile,
            start=bool(args.start),
        )
    return payload


def main(argv: list[str] | None = None) -> int:
    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    _ = app
    args = build_parser().parse_args(argv)
    response = send_request(request_from_args(args))
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0 if response.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

