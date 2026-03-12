#!/usr/bin/env python3
"""
OVH DNS record management for mail server.
Creates MX, SPF, DKIM, DMARC, and autoconfig records.

Environment variables:
  OVH_ENDPOINT, OVH_APPLICATION_KEY, OVH_APPLICATION_SECRET, OVH_CONSUMER_KEY
  MAIL_HOSTNAME, MAIL_PUBLIC_IP, MAIL_DOMAINS (comma-separated)
  DKIM_SELECTOR, DKIM_KEY_DIR, DNS_SPF, DNS_DMARC, DNS_MX_PRIORITY
"""

import os
import sys
import re

try:
    import ovh
except ImportError:
    print("ERROR: python3-ovh not installed. Run: pip install ovh", file=sys.stderr)
    sys.exit(1)

changed = False


def get_client():
    return ovh.Client(
        endpoint=os.environ["OVH_ENDPOINT"],
        application_key=os.environ["OVH_APPLICATION_KEY"],
        application_secret=os.environ["OVH_APPLICATION_SECRET"],
        consumer_key=os.environ["OVH_CONSUMER_KEY"],
    )


def get_zone_name(domain):
    """Extract the DNS zone from a domain (handles subdomains)."""
    parts = domain.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return domain


def get_subdomain(domain, zone):
    """Get subdomain part relative to zone."""
    if domain == zone:
        return ""
    return domain[: -(len(zone) + 1)]


def find_existing_record(client, zone, subdomain, field_type, target=None):
    """Find existing DNS records matching criteria."""
    params = {"fieldType": field_type}
    if subdomain is not None:
        params["subDomain"] = subdomain
    record_ids = client.get(f"/domain/zone/{zone}/record", **params)
    records = []
    for rid in record_ids:
        record = client.get(f"/domain/zone/{zone}/record/{rid}")
        if target is None or record["target"] == target:
            records.append(record)
    return records


def ensure_record(client, zone, subdomain, field_type, target, ttl=3600):
    """Ensure a DNS record exists, create if missing."""
    global changed
    existing = find_existing_record(client, zone, subdomain, field_type)
    for rec in existing:
        if rec["target"] == target:
            print(f"  OK: {field_type} {subdomain}.{zone} -> {target[:60]}...")
            return
    # Create new record
    params = {
        "fieldType": field_type,
        "subDomain": subdomain,
        "target": target,
        "ttl": ttl,
    }
    client.post(f"/domain/zone/{zone}/record", **params)
    print(f"  CREATED: {field_type} {subdomain}.{zone} -> {target[:60]}...")
    changed = True


def ensure_mx_record(client, zone, subdomain, target, priority):
    """Ensure MX record exists."""
    global changed
    mx_target = f"{priority} {target}."
    existing = find_existing_record(client, zone, subdomain, "MX")
    for rec in existing:
        if target in rec["target"]:
            print(f"  OK: MX {subdomain or '@'}.{zone} -> {rec['target']}")
            return
    client.post(
        f"/domain/zone/{zone}/record",
        fieldType="MX",
        subDomain=subdomain,
        target=mx_target,
        ttl=3600,
    )
    print(f"  CREATED: MX {subdomain or '@'}.{zone} -> {mx_target}")
    changed = True


def read_dkim_public_key(domain, selector, key_dir):
    """Read DKIM public key from the generated .txt file and return DNS value."""
    key_file = os.path.join(key_dir, domain, f"{selector}.txt")
    if not os.path.exists(key_file):
        print(f"  WARN: DKIM key file not found: {key_file}", file=sys.stderr)
        return None
    with open(key_file) as f:
        content = f.read()
    # Extract the TXT record value - combine multi-line quoted strings
    parts = re.findall(r'"([^"]*)"', content)
    return "".join(parts)


def main():
    client = get_client()
    hostname = os.environ["MAIL_HOSTNAME"]
    public_ip = os.environ["MAIL_PUBLIC_IP"]
    domains = os.environ["MAIL_DOMAINS"].split(",")
    selector = os.environ["DKIM_SELECTOR"]
    key_dir = os.environ["DKIM_KEY_DIR"]
    spf = os.environ["DNS_SPF"]
    dmarc = os.environ["DNS_DMARC"]
    mx_priority = int(os.environ["DNS_MX_PRIORITY"])

    for domain in domains:
        zone = get_zone_name(domain)
        subdomain = get_subdomain(domain, zone)
        print(f"\n=== Configuring DNS for {domain} (zone: {zone}) ===")

        # A record for mail hostname (only for the primary domain)
        mail_sub = get_subdomain(hostname, zone)
        if mail_sub:
            ensure_record(client, zone, mail_sub, "A", public_ip)

        # MX record
        ensure_mx_record(client, zone, subdomain, hostname, mx_priority)

        # SPF record
        ensure_record(client, zone, subdomain, "TXT", f'"{spf}"')

        # DKIM record
        dkim_value = read_dkim_public_key(domain, selector, key_dir)
        if dkim_value:
            dkim_subdomain = f"{selector}._domainkey"
            if subdomain:
                dkim_subdomain = f"{dkim_subdomain}.{subdomain}"
            ensure_record(client, zone, dkim_subdomain, "TXT", f'"{dkim_value}"')

        # DMARC record
        dmarc_subdomain = "_dmarc"
        if subdomain:
            dmarc_subdomain = f"_dmarc.{subdomain}"
        ensure_record(client, zone, dmarc_subdomain, "TXT", f'"{dmarc}"')

        # _domainkey base TXT record
        domainkey_subdomain = "_domainkey"
        if subdomain:
            domainkey_subdomain = f"_domainkey.{subdomain}"
        admin_email = os.environ.get("ADMIN_EMAIL", f"postmaster@{domain}")
        ensure_record(
            client, zone, domainkey_subdomain, "TXT",
            f'"o=-; r={admin_email}"',
        )

        # smtp/imap CNAME aliases
        for alias in ("smtp", "imap"):
            alias_subdomain = alias
            if subdomain:
                alias_subdomain = f"{alias}.{subdomain}"
            ensure_record(client, zone, alias_subdomain, "CNAME", f"{hostname}.")

        # Autoconfig record (for Thunderbird and other clients)
        autoconfig_subdomain = "autoconfig"
        if subdomain:
            autoconfig_subdomain = f"autoconfig.{subdomain}"
        ensure_record(client, zone, autoconfig_subdomain, "CNAME", f"{hostname}.")

    # Refresh the DNS zone
    for domain in domains:
        zone = get_zone_name(domain)
        try:
            client.post(f"/domain/zone/{zone}/refresh")
            print(f"\nRefreshed DNS zone: {zone}")
        except Exception as e:
            print(f"  WARN: Could not refresh zone {zone}: {e}", file=sys.stderr)

    if changed:
        print("\nCHANGED")
    else:
        print("\nOK - No changes needed")


if __name__ == "__main__":
    main()
