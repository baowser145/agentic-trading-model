#!/usr/bin/env python3
"""Post a message to the project's Discord webhook.

Usage:
  python3 send_discord.py "message text"
  echo "message text" | python3 send_discord.py

Webhook URL comes from the DISCORD_WEBHOOK_URL env var (local runs: config/.env; the scheduled
cloud routine embeds the URL directly since it can't read local files).
"""

import os
import sys

import requests


DISCORD_MAX_LEN = 2000


def split_message(message, max_len=DISCORD_MAX_LEN):
    """Split on blank-line boundaries so a message never gets cut mid-line. Discord hard-caps
    message content at 2000 characters -- silently truncating (the old behavior) can cut a message
    off mid-card, which is worse than sending it as multiple messages."""
    if len(message) <= max_len:
        return [message]

    paragraphs = message.split("\n\n")
    chunks = []
    current = ""
    for p in paragraphs:
        candidate = f"{current}\n\n{p}" if current else p
        if len(candidate) > max_len:
            if current:
                chunks.append(current)
            if len(p) > max_len:
                # a single paragraph is itself too long -- hard-split it as a last resort
                for i in range(0, len(p), max_len):
                    chunks.append(p[i:i + max_len])
                current = ""
            else:
                current = p
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def main():
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL is not set", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])
    else:
        message = sys.stdin.read()

    if not message.strip():
        print("No message provided", file=sys.stderr)
        sys.exit(1)

    chunks = split_message(message)
    for i, chunk in enumerate(chunks):
        resp = requests.post(webhook_url, json={"content": chunk}, timeout=15)
        resp.raise_for_status()
    print(f"Sent ({len(chunks)} message{'s' if len(chunks) != 1 else ''}).")


if __name__ == "__main__":
    main()
