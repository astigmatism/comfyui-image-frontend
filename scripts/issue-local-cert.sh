#!/bin/sh
#
# Issue (or reuse) the appliance-local TLS certificate material used by the
# Caddy edge in compose.example.yml.
#
# The appliance has no public DNS name, so browsers are told to trust a
# private root CA that lives only in the mounted data directory
# (CIF_TLS_CERT_DIR, default <repo>/data/certificates; never committed):
#
#   ca.crt / ca.key   private root CA (created once, reused forever)
#   tls.crt / tls.key leaf certificate for CIF_TLS_HOSTNAME (the SAN)
#   leaf.meta         bookkeeping: hostname and notAfter of the leaf
#
# The script is idempotent and safe to run on every update:
#   * the root is created only when missing or unreadable;
#   * the leaf is reissued only when the hostname changed, the leaf is
#     expired or expires within CIF_TLS_RENEW_DAYS (default 30), the key no
#     longer matches the certificate, or the chain fails verification;
#   * writes are atomic: material is generated in a temporary directory and
#     moved into place only after verification passes.
#
# Environment (all optional):
#   CIF_TLS_HOSTNAME  leaf SAN; default image-studio.lan. A stable,
#                     resolvable, non-public name (.lan works; .local is
#                     rejected because mDNS resolution varies per OS).
#   CIF_TLS_CERT_DIR  output directory; default ./data/certificates
#                     (relative paths resolve against the repository root).
#   CIF_TLS_ROOT_DAYS root validity; default 3650.
#   CIF_TLS_LEAF_DAYS leaf validity; default 825 (browsers accept up to 825).
#   CIF_TLS_RENEW_DAYS reissue the leaf this far before notAfter; default 30.

set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

HOSTNAME_VAL=${CIF_TLS_HOSTNAME:-image-studio.lan}
CERT_DIR=${CIF_TLS_CERT_DIR:-./data/certificates}
ROOT_DAYS=${CIF_TLS_ROOT_DAYS:-3650}
LEAF_DAYS=${CIF_TLS_LEAF_DAYS:-825}
RENEW_DAYS=${CIF_TLS_RENEW_DAYS:-30}

# Relative certificate directories resolve against the repository root so the
# script works from any working directory (including the portal runner).
case "$CERT_DIR" in
  /*) ;;
  *) CERT_DIR="$ROOT/$CERT_DIR" ;;
esac

fail() {
  echo "Error: $*" >&2
  exit 1
}

command -v openssl >/dev/null 2>&1 || fail "openssl is not installed"

# --- Hostname validation ------------------------------------------------------
[ -n "$HOSTNAME_VAL" ] || fail "CIF_TLS_HOSTNAME must not be empty"
# Reject .local: resolution depends on per-OS mDNS/Avahi behavior, which makes
# the user-facing URL unreliable across Windows, macOS, Linux, and phones.
case "$(printf '%s' "$HOSTNAME_VAL" | tr '[:upper:]' '[:lower:]')" in
  *.local)
    fail "CIF_TLS_HOSTNAME must not use the .local suffix (mDNS varies per OS); choose a .lan-style name instead"
    ;;
esac
# Standard DNS name: labels of alphanumerics and hyphens, no leading/trailing
# hyphen, no underscores.
printf '%s' "$HOSTNAME_VAL" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)*$' \
  || fail "CIF_TLS_HOSTNAME is not a valid DNS name: $HOSTNAME_VAL"

for value in "$ROOT_DAYS" "$LEAF_DAYS" "$RENEW_DAYS"; do
  printf '%s' "$value" | grep -Eq '^[1-9][0-9]*$' || fail "invalid day count: $value"
done
[ "$LEAF_DAYS" -le 825 ] || fail "CIF_TLS_LEAF_DAYS exceeds 825 (browser limit)"

mkdir -p "$CERT_DIR"

TMP_DIR=$(mktemp -d "${CERT_DIR}.new.XXXXXX")
trap 'rm -rf "$TMP_DIR"' EXIT
cd "$TMP_DIR"

# --- Root CA: create once, reuse forever --------------------------------------
if [ -f "$CERT_DIR/ca.crt" ] && [ -f "$CERT_DIR/ca.key" ] \
  && openssl x509 -in "$CERT_DIR/ca.crt" -noout -subject >/dev/null 2>&1 \
  && openssl verify -CAfile "$CERT_DIR/ca.crt" "$CERT_DIR/ca.crt" >/dev/null 2>&1; then
  cp "$CERT_DIR/ca.crt" ca.crt
  cp "$CERT_DIR/ca.key" ca.key
  echo "Reusing existing root CA: $CERT_DIR/ca.crt"
else
  echo "Creating new root CA for $HOSTNAME_VAL issuance"
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out ca.key
  openssl req -x509 -new -key ca.key -sha256 -days "$ROOT_DAYS" -out ca.crt \
    -subj "/CN=Home Image Studio Local Root CA" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -addext "subjectKeyIdentifier=hash"
  chmod 600 ca.key
fi

# --- Leaf: decide whether the existing one is still good -----------------------
need_new_leaf=1
reason="no existing leaf"
if [ -f "$CERT_DIR/tls.crt" ] && [ -f "$CERT_DIR/tls.key" ] && [ -f "$CERT_DIR/leaf.meta" ]; then
  meta_hostname=$(sed -n 's/^hostname=//p' "$CERT_DIR/leaf.meta" | head -n 1)
  cert_pubkey=$(openssl x509 -in "$CERT_DIR/tls.crt" -noout -pubkey 2>/dev/null || true)
  key_pubkey=$(openssl pkey -in "$CERT_DIR/tls.key" -pubout 2>/dev/null || true)
  # Verify the existing leaf against the root that will be installed (the
  # temp dir holds either the reused root or a freshly created one). Checking
  # $CERT_DIR/ca.crt instead would let a leaf issued by a superseded root pass
  # here and only fail the final verification.
  if [ "$meta_hostname" = "$HOSTNAME_VAL" ] \
    && openssl x509 -in "$CERT_DIR/tls.crt" -noout -checkend $((RENEW_DAYS * 86400)) >/dev/null 2>&1 \
    && [ -n "$cert_pubkey" ] && [ "$cert_pubkey" = "$key_pubkey" ] \
    && openssl verify -CAfile ca.crt "$CERT_DIR/tls.crt" >/dev/null 2>&1; then
    need_new_leaf=0
    reason="existing leaf for $HOSTNAME_VAL is valid"
  else
    reason="existing leaf needs reissue (hostname, expiry, key match, or chain)"
  fi
fi

if [ "$need_new_leaf" = "1" ]; then
  echo "Issuing new leaf for $HOSTNAME_VAL ($reason)"
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out tls.key
  openssl req -new -key tls.key -sha256 -out tls.csr -subj "/CN=$HOSTNAME_VAL"
  cat > leaf.ext <<EOF
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:$HOSTNAME_VAL
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
EOF
  serial=$(openssl rand -hex 16)
  openssl x509 -req -in tls.csr -CA ca.crt -CAkey ca.key \
    -set_serial "0x$serial" -days "$LEAF_DAYS" -sha256 \
    -extfile leaf.ext -out tls.crt
  rm -f tls.csr leaf.ext
  chmod 600 tls.key
else
  cp "$CERT_DIR/tls.crt" tls.crt
  cp "$CERT_DIR/tls.key" tls.key
fi

# Final verification before anything is installed.
openssl verify -CAfile ca.crt tls.crt >/dev/null || fail "issued chain failed verification"
openssl x509 -in tls.crt -noout -checkend $((RENEW_DAYS * 86400)) >/dev/null \
  || fail "issued leaf already inside the renewal window"
# The SAN must be exactly the hostname the Caddyfile serves and the clients
# will navigate to.
openssl x509 -in tls.crt -noout -ext subjectAltName | grep -q "DNS:$HOSTNAME_VAL" \
  || fail "issued leaf SAN does not include $HOSTNAME_VAL"

not_after=$(openssl x509 -in tls.crt -noout -enddate | sed 's/^notAfter=//')
{
  echo "hostname=$HOSTNAME_VAL"
  echo "not_after=$not_after"
  echo "root_days=$ROOT_DAYS"
  echo "leaf_days=$LEAF_DAYS"
} > leaf.meta

# --- Install atomically ---------------------------------------------------------
# The keys are the only secrets: owner-only. The certificates are public and
# may be imported by clients. The directory itself is left at its default
# permissions so the container (which reads the bind mount as its own uid)
# can always traverse it regardless of which host user ran this script.
chmod 600 ca.key tls.key
chmod 644 ca.crt tls.crt leaf.meta
mv ca.crt ca.key tls.crt tls.key leaf.meta "$CERT_DIR/"

echo "TLS material ready under $CERT_DIR"
echo "  subject:   $(openssl x509 -in "$CERT_DIR/tls.crt" -noout -subject | sed 's/^subject=//')"
echo "  SAN:       DNS:$HOSTNAME_VAL"
echo "  notAfter:  $not_after"
echo "  user URL:  https://$HOSTNAME_VAL:8443"
echo "Import $CERT_DIR/ca.crt into each client's OS/browser trust store once."
