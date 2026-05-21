#!/bin/sh
# Managed by Ansible — runs after a successful certbot renewal that produced
# a new certificate. RENEWED_LINEAGE points to /etc/letsencrypt/live/<name>.
# We reload every service that may serve the new cert. Failures here MUST
# bubble up so certbot reports them.
set -eu

systemctl reload nginx    || true   # nginx may not be installed yet on first run
systemctl reload postfix
systemctl reload dovecot

# Rebuild postfix SNI map: postmap -F embeds the cert/key contents in the
# .db, so the .db must be regenerated when the underlying files change.
if [ -f /etc/postfix/sni_map ]; then
    postmap -F hash:/etc/postfix/sni_map
    systemctl reload postfix
fi
