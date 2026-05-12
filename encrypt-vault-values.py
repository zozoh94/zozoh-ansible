#!/usr/bin/env python3
"""Encrypt only plain-text values in a YAML vault file.

Usage: ./encrypt-vault-values.py <file.yaml>

Skips values already encrypted (!vault) and only encrypts new plain-text ones.
"""
import sys
import subprocess
import re


def encrypt_value(name, value):
    result = subprocess.run(
        ["ansible-vault", "encrypt_string", "--name", name, value],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"Error encrypting '{name}': {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.rstrip("\n")


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <file.yaml>", file=sys.stderr)
        sys.exit(1)

    filepath = sys.argv[1]

    with open(filepath, "r") as f:
        raw = f.read()

    output_lines = []
    changed = 0
    skipped = 0
    skip_block = False

    for line in raw.split("\n"):
        # Skip continuation lines of an encrypted block
        if skip_block:
            if line.startswith(" ") or line.strip() == "":
                output_lines.append(line)
                continue
            else:
                skip_block = False

        stripped = line.strip()

        # Preserve comments, blank lines, document markers
        if not stripped or stripped.startswith("#") or stripped == "---":
            output_lines.append(line)
            continue

        # Already encrypted value — keep as-is
        if "!vault" in line:
            output_lines.append(line)
            skip_block = True
            skipped += 1
            continue

        # Match a plain key: value line
        match = re.match(r'^(\s*)(\S+):\s+(.+)$', line)
        if not match:
            output_lines.append(line)
            continue

        indent, key, value = match.group(1), match.group(2), match.group(3)

        # Strip surrounding quotes
        if (value.startswith("'") and value.endswith("'")) or \
           (value.startswith('"') and value.endswith('"')):
            value = value[1:-1]

        encrypted = encrypt_value(key, value)
        for enc_line in encrypted.split("\n"):
            output_lines.append(indent + enc_line)
        changed += 1

    with open(filepath, "w") as f:
        f.write("\n".join(output_lines) + "\n")

    print(f"✓ {filepath}: {changed} encrypted, {skipped} already encrypted (skipped)")


if __name__ == "__main__":
    main()
