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
import time

try:
    import ovh
except ImportError:
    print("ERROR: python3-ovh not installed. Run: pip install ovh", file=sys.stderr)
    sys.exit(1)

changed = False

MAX_RETRIES = 3
RETRY_BACKOFF = 2  # seconds, doubled each retry


def api_call_with_retry(func, *args, **kwargs):
    """Execute an OVH API call with exponential backoff retry."""
    last_exception = None
    for attempt in range(MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except ovh.exceptions.NetworkError as e:
            last_exception = e
            wait = RETRY_BACKOFF * (2 ** attempt)
            print(f"  RETRY ({attempt + 1}/{MAX_RETRIES}): network error, waiting {wait}s...",
                  file=sys.stderr)
            time.sleep(wait)
        except ovh.exceptions.APIError as e:
            if "Too many requests" in str(e) or getattr(e, 'status', 0) == 429:
                last_exception = e
                wait = RETRY_BACKOFF * (2 ** attempt)
                print(f"  RETRY ({attempt + 1}/{MAX_RETRIES}): rate limited, waiting {wait}s...",
                      file=sys.stderr)
                time.sleep(wait)
            else:
                raise
    raise last_exception


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
    record_ids = api_call_with_retry(client.get, f"/domain/zone/{zone}/record", **params)
    records = []
    for rid in record_ids:
        record = api_call_with_retry(client.get, f"/domain/zone/{zone}/record/{rid}")
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
    api_call_with_retry(client.post, f"/domain/zone/{zone}/record", **params)
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
    api_call_with_retry(
        client.post,
        f"/domain/zone/{zone}/record",
        fieldType="MX",
        subDomain=subdomain,
        target=mx_target,
        ttl=3600,
    )
    print(f"  CREATED: MX {subdomain or '@'}.{zone} -> {mx_target}")
    changed = True


def ensure_srv_record(client, zone, subdomain, priority, weight, port, target, ttl=3600):
    """Ensure an SRV record exists. RFC 2782 format: 'priority weight port target.'.
    Negative record per RFC 6186: priority=0, weight=0, port=0, target='.' (single dot)."""
    global changed
    if target == ".":
        srv_target = "0 0 0 ."
    else:
        srv_target = f"{priority} {weight} {port} {target}."
    existing = find_existing_record(client, zone, subdomain, "SRV")
    for rec in existing:
        if rec["target"] == srv_target:
            print(f"  OK: SRV {subdomain}.{zone} -> {srv_target}")
            return
        # An SRV with the same name but different value: leave it alone and
        # log; if the user has an override they keep it. Idempotency favors
        # not deleting records we did not create.
    api_call_with_retry(
        client.post,
        f"/domain/zone/{zone}/record",
        fieldType="SRV",
        subDomain=subdomain,
        target=srv_target,
        ttl=ttl,
    )
    print(f"  CREATED: SRV {subdomain}.{zone} -> {srv_target}")
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
    # MX target. RFC 2181 forbids MX -> CNAME, so this name must end up as an A.
    mx_host = os.environ.get("MAIL_MX_HOST") or hostname
    public_ip = os.environ["MAIL_PUBLIC_IP"]
    domains = os.environ["MAIL_DOMAINS"].split(",")
    selector = os.environ["DKIM_SELECTOR"]
    key_dir = os.environ["DKIM_KEY_DIR"]
    spf = os.environ["DNS_SPF"]
    dmarc = os.environ["DNS_DMARC"]
    mx_priority = int(os.environ["DNS_MX_PRIORITY"])
    tlsa_hash = os.environ.get("TLSA_HASH", "")

    errors = []

    for domain in domains:
        zone = get_zone_name(domain)
        subdomain = get_subdomain(domain, zone)
        print(f"\n=== Configuring DNS for {domain} (zone: {zone}) ===")

        try:
            # A records, in whichever zone owns each name. We may emit:
            #   - one A for `hostname` (PTR/HELO/cert primary). May be apex.
            #   - one A for `mx_host`  (MX target). Must be A, never CNAME.
            # When both names live in the same zone and are identical, we emit
            # one record; when they differ but share the IP we emit both.
            emitted_in_zone = set()
            for name in (hostname, mx_host):
                if name in emitted_in_zone:
                    continue
                if name == zone:
                    ensure_record(client, zone, "", "A", public_ip)
                    emitted_in_zone.add(name)
                elif name.endswith("." + zone):
                    sub = name[: -(len(zone) + 1)]
                    ensure_record(client, zone, sub, "A", public_ip)
                    emitted_in_zone.add(name)
                # else: name lives in a different zone; skip silently.

            # MX record -> mx_host (never CNAME). Cross-zone targets are fine.
            ensure_mx_record(client, zone, subdomain, mx_host, mx_priority)

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

            # Autoconfig / Autodiscover CNAMEs — both point at the mail host so
            # nginx can serve per-Host XML files for Thunderbird and Outlook.
            for alias in ("autoconfig", "autodiscover"):
                alias_subdomain = alias
                if subdomain:
                    alias_subdomain = f"{alias}.{subdomain}"
                ensure_record(client, zone, alias_subdomain, "CNAME", f"{hostname}.")

            # RFC 6186 SRV records — modern clients (Thunderbird, Apple Mail,
            # K-9) use these instead of the HTTP discovery URLs.
            srv_target_host = os.environ.get("MAIL_SRV_TARGET", hostname)

            def srv_sub(name):
                return f"{name}.{subdomain}" if subdomain else name

            # Positive: IMAPS:993, Submission:587 STARTTLS.
            ensure_srv_record(client, zone, srv_sub("_imaps._tcp"),
                              0, 1, 993, srv_target_host)
            ensure_srv_record(client, zone, srv_sub("_submission._tcp"),
                              0, 1, 587, srv_target_host)
            # Negative: explicitly tell clients NOT to use these protocols.
            for negative in ("_imap._tcp", "_pop3._tcp", "_pop3s._tcp"):
                ensure_srv_record(client, zone, srv_sub(negative),
                                  0, 0, 0, ".")

            # TLSA records for DANE (if hash provided)
            if tlsa_hash:
                mail_sub = get_subdomain(hostname, zone)
                for port in (25, 465, 587, 993):
                    tlsa_subdomain = f"_{port}._tcp.{mail_sub}" if mail_sub else f"_{port}._tcp"
                    tlsa_value = f"3 1 1 {tlsa_hash}"
                    ensure_record(client, zone, tlsa_subdomain, "TLSA", tlsa_value)

        except Exception as e:
            msg = f"ERROR processing {domain}: {e}"
            print(f"  {msg}", file=sys.stderr)
            errors.append(msg)
            continue

    # Refresh the DNS zones
    refreshed_zones = set()
    for domain in domains:
        zone = get_zone_name(domain)
        if zone in refreshed_zones:
            continue
        try:
            api_call_with_retry(client.post, f"/domain/zone/{zone}/refresh")
            print(f"\nRefreshed DNS zone: {zone}")
            refreshed_zones.add(zone)
        except Exception as e:
            print(f"  WARN: Could not refresh zone {zone}: {e}", file=sys.stderr)

    if errors:
        print(f"\nFAILED: {len(errors)} domain(s) had errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    if changed:
        print("\nCHANGED")
    else:
        print("\nOK - No changes needed")


if __name__ == "__main__":
    main()
