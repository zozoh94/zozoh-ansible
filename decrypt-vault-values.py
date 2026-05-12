#!/usr/bin/env python3
"""Decrypt inline vault values from a YAML file and print to stdout.

Usage:
  ./decrypt-vault-values.py <file.yaml>           # all keys
  ./decrypt-vault-values.py <file.yaml> <key>      # single key
"""
import sys
import subprocess
import re
import tempfile
import os


def decrypt_blob(blob):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".vault", delete=False) as f:
        f.write(blob)
        tmp = f.name
    try:
        result = subprocess.run(
            ["ansible-vault", "decrypt", "--output", "-", tmp],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return f"<error: {result.stderr.strip()}>"
        return result.stdout.rstrip("\n")
    finally:
        os.unlink(tmp)


def parse_vault_yaml(filepath):
    """Parse a YAML with !vault inline values, return ordered list of (key, value, encrypted)."""
    with open(filepath, "r") as f:
        lines = f.readlines()

    entries = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if not stripped or stripped.startswith("#") or stripped == "---":
            i += 1
            continue

        match = re.match(r'^(\S+):\s+!vault\s+\|', line)
        if match:
            key = match.group(1)
            # Collect the indented encrypted block
            blob_lines = []
            i += 1
            while i < len(lines) and (lines[i].startswith(" ") or lines[i].strip() == ""):
                if lines[i].strip():
                    blob_lines.append(lines[i].strip())
                i += 1
            entries.append((key, "\n".join(blob_lines), True))
            continue

        match = re.match(r'^(\S+):\s+(.+)$', line)
        if match:
            entries.append((match.group(1), match.group(2), False))

        i += 1

    return entries


def main():
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print(f"Usage: {sys.argv[0]} <file.yaml> [key]", file=sys.stderr)
        sys.exit(1)

    filepath = sys.argv[1]
    filter_key = sys.argv[2] if len(sys.argv) == 3 else None

    entries = parse_vault_yaml(filepath)

    if filter_key:
        entries = [(k, v, e) for k, v, e in entries if k == filter_key]
        if not entries:
            print(f"Key '{filter_key}' not found", file=sys.stderr)
            sys.exit(1)

    for key, value, encrypted in entries:
        if encrypted:
            value = decrypt_blob(value)
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
