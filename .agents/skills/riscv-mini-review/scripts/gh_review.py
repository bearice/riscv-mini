#!/usr/bin/env python3
"""GitHub PR Review helper script for riscv-mini.

Inspects PR reviews, lists active review comments, replies, and reacts.
"""
import argparse
import json
import subprocess
import sys
from typing import Any, Dict, List, Optional, Set


def run_gh_api(endpoint: str, method: str = "GET", data: Optional[Dict[str, Any]] = None) -> Any:
    cmd = ["gh", "api", endpoint]
    if method != "GET":
        cmd.extend(["-X", method])
    if data:
        for k, v in data.items():
            cmd.extend(["-f", f"{k}={v}"])
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"gh api error ({res.returncode}): {res.stderr.strip()}")
    if not res.stdout.strip():
        return None
    return json.loads(res.stdout)


def get_current_repo() -> str:
    res = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError("Failed to detect repo with gh CLI")
    return res.stdout.strip()


def get_current_pr() -> int:
    res = subprocess.run(
        ["gh", "pr", "view", "--json", "number", "--jq", ".number"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError("Failed to detect current PR with gh CLI")
    return int(res.stdout.strip())


def cmd_list(args: argparse.Namespace) -> int:
    repo = args.repo or get_current_repo()
    pr = args.pr or get_current_pr()
    print(f"Fetching review comments for {repo} PR #{pr}...")
    comments = run_gh_api(f"repos/{repo}/pulls/{pr}/comments?per_page=100")

    replies: Dict[int, List[Dict[str, Any]]] = {}
    root_comments: List[Dict[str, Any]] = []

    for c in comments:
        reply_to = c.get("in_reply_to_id")
        if reply_to:
            replies.setdefault(reply_to, []).append(c)
        else:
            root_comments.append(c)

    active_pending = []
    active_resolved = []
    outdated = []

    for c in root_comments:
        cid = c["id"]
        path = c.get("path")
        line = c.get("line")
        user = c.get("user", {}).get("login", "")
        body_preview = c.get("body", "").split("\n")[0][:80]
        has_reply = cid in replies
        is_active = line is not None

        item = {
            "id": cid,
            "path": path,
            "line": line,
            "user": user,
            "preview": body_preview,
            "replies": len(replies.get(cid, [])),
            "is_active": is_active,
        }

        if not is_active:
            outdated.append(item)
        elif not has_reply:
            active_pending.append(item)
        else:
            active_resolved.append(item)

    print(f"\n=== Active Pending Review Comments ({len(active_pending)}) ===")
    if not active_pending:
        print("  (None! All active review comments have been replied to)")
    for it in active_pending:
        print(f"  • ID {it['id']}: {it['path']}:{it['line']} by @{it['user']}")
        print(f"    Preview: {it['preview']}")

    print(f"\n=== Active Replied Review Comments ({len(active_resolved)}) ===")
    for it in active_resolved:
        print(f"  • ID {it['id']}: {it['path']}:{it['line']} by @{it['user']} ({it['replies']} replies)")
        print(f"    Preview: {it['preview']}")

    if args.show_outdated:
        print(f"\n=== Outdated Review Comments ({len(outdated)}) ===")
        for it in outdated:
            print(f"  • ID {it['id']}: {it['path']} (Outdated)")

    return 0


def cmd_reply(args: argparse.Namespace) -> int:
    repo = args.repo or get_current_repo()
    pr = args.pr or get_current_pr()
    print(f"Replying to comment {args.comment_id} on {repo} PR #{pr}...")
    res = run_gh_api(
        f"repos/{repo}/pulls/{pr}/comments/{args.comment_id}/replies",
        method="POST",
        data={"body": args.body},
    )
    print(f"Reply posted successfully: {res.get('html_url')}")
    return 0


def cmd_react(args: argparse.Namespace) -> int:
    repo = args.repo or get_current_repo()
    reaction = args.reaction
    print(f"Adding '{reaction}' reaction to comment {args.comment_id} on {repo}...")
    run_gh_api(
        f"repos/{repo}/pulls/comments/{args.comment_id}/reactions",
        method="POST",
        data={"content": reaction},
    )
    print("Reaction added successfully.")
    return 0


def cmd_resolve(args: argparse.Namespace) -> int:
    repo = args.repo or get_current_repo()
    pr = args.pr or get_current_pr()
    reaction = args.reaction
    print(f"Resolving review comment {args.comment_id} on {repo} PR #{pr}...")
    # 1. Reply
    res = run_gh_api(
        f"repos/{repo}/pulls/{pr}/comments/{args.comment_id}/replies",
        method="POST",
        data={"body": args.body},
    )
    print(f"  Reply posted: {res.get('html_url')}")
    # 2. React
    run_gh_api(
        f"repos/{repo}/pulls/comments/{args.comment_id}/reactions",
        method="POST",
        data={"content": reaction},
    )
    print(f"  Added '{reaction}' reaction.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PR Review Helper")
    parser.add_argument("--repo", help="Target repo (owner/repo)")
    parser.add_argument("--pr", type=int, help="Target PR number")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = subparsers.add_parser("list", help="List review comments")
    p_list.add_argument("--show-outdated", action="store_true", help="Show outdated comments")
    p_list.set_defaults(func=cmd_list)

    # reply
    p_reply = subparsers.add_parser("reply", help="Reply to a review comment")
    p_reply.add_argument("comment_id", type=int, help="Comment ID to reply to")
    p_reply.add_argument("--body", required=True, help="Reply body content")
    p_reply.set_defaults(func=cmd_reply)

    # react
    p_react = subparsers.add_parser("react", help="Add reaction to a review comment")
    p_react.add_argument("comment_id", type=int, help="Comment ID")
    p_react.add_argument("--reaction", default="+1", help="Reaction content (+1, heart, etc.)")
    p_react.set_defaults(func=cmd_react)

    # resolve (reply + react)
    p_res = subparsers.add_parser("resolve", help="Reply and react to resolve a review comment")
    p_res.add_argument("comment_id", type=int, help="Comment ID")
    p_res.add_argument("--body", required=True, help="Reply body content")
    p_res.add_argument("--reaction", default="+1", help="Reaction content (+1, etc.)")
    p_res.set_defaults(func=cmd_resolve)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
