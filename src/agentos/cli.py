"""Process entry points and local operator provisioning for the M1 profile."""

import argparse
import json
import logging
import os
from pathlib import Path

from agentos.control.release import Release


def main():
    parser = argparse.ArgumentParser(prog="agentos")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("release-id", help="print the immutable configured release ID")
    score = commands.add_parser("score", help="score observations against the M1 fixture")
    score.add_argument("cases", type=Path)
    score.add_argument("observations", type=Path)
    api = commands.add_parser("api", help="start the HTTP API")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    commands.add_parser("worker", help="run a worker for the configured release")
    for action in ("grant", "revoke"):
        access = commands.add_parser(action, help=f"{action} a subject's project access")
        access.add_argument("tenant")
        access.add_argument("subject")
        access.add_argument("project")
    source = commands.add_parser("put-source", help="create or revise a UTF-8 evidence document")
    source.add_argument("tenant")
    source.add_argument("project")
    source.add_argument("source_id")
    source.add_argument("title")
    source.add_argument("location")
    source.add_argument("file", type=Path)
    delete = commands.add_parser("delete-source", help="withdraw a document")
    delete.add_argument("tenant")
    delete.add_argument("project")
    delete.add_argument("source_id")
    receipt = commands.add_parser("reconcile", help="settle an orphaned attempt from a verified provider receipt")
    receipt.add_argument("tenant")
    receipt.add_argument("dispatch_id")
    receipt.add_argument("receipt_id")
    receipt.add_argument("actual_micros", type=int)
    receipt.add_argument("--input-tokens", type=int)
    receipt.add_argument("--output-tokens", type=int)
    args = parser.parse_args()
    if args.command == "score":
        from agentos.evaluation.support import score_files
        print(json.dumps(score_files(args.cases, args.observations), ensure_ascii=False, indent=2))
        return
    release = None
    if args.command in {"release-id", "api", "worker"}:
        release = Release.load(os.environ["AGENTOS_RELEASE_FILE"])
    if args.command == "release-id":
        print(release.version_id)
        return

    from agentos.adapters.mysql.store import MySQLStore

    store = MySQLStore.from_env()
    if release is not None:
        store.register_release(release.version_id, release.manifest)
    if args.command == "api":
        import uvicorn
        from agentos.api.app import create_app
        from agentos.governance.auth import Authenticator

        auth = Authenticator.from_file(os.environ["AGENTOS_CREDENTIALS_FILE"])
        uvicorn.run(create_app(store, auth, release.version_id), host=args.host, port=args.port)
    elif args.command == "worker":
        from agentos.gateways.model.client import ChatModel
        from agentos.worker.service import serve

        logging.basicConfig(level=logging.INFO)
        model = ChatModel(release.endpoint, os.environ["AGENTOS_MODEL_API_KEY"], timeout_seconds=30)
        serve(store, model, release)
    elif args.command in {"grant", "revoke"}:
        store.set_access(args.tenant, args.subject, args.project, args.command == "grant")
    elif args.command == "put-source":
        store.put_source(args.tenant, args.project, args.source_id, args.title,
                         args.file.read_text(encoding="utf-8-sig"), args.location)
    elif args.command == "delete-source":
        store.delete_source(args.tenant, args.project, args.source_id)
    elif args.command == "reconcile":
        store.reconcile(args.tenant, args.dispatch_id, args.receipt_id, args.actual_micros,
                        input_tokens=args.input_tokens, output_tokens=args.output_tokens)
