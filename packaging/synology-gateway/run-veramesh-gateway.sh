#!/bin/sh
set -eu

ROOT=/var/packages/VeraMeshGateway
TARGET="$ROOT/target"
VAR="$ROOT/var/gateway"
BIN="$TARGET/bin/veramesh-gateway"
CONTROLLER_CONFIG="$VAR/controller.json"
OAUTH_CONFIG="$VAR/oauth.json"
OAUTH_SECRET="$VAR/oauth-client-secret"

fail() {
  printf '%s\n' "VeraMeshGateway: $*" >&2
  exit 78
}

[ -x "$BIN" ] || fail "gateway binary missing"
[ -r "$CONTROLLER_CONFIG" ] || fail "controller config missing"
[ -r "$OAUTH_CONFIG" ] || fail "OAuth config missing"
[ -r "$OAUTH_SECRET" ] || fail "OAuth client secret missing"

VERAMESH_OAUTH_CLIENT_SECRET=$(cat "$OAUTH_SECRET")
[ -n "$VERAMESH_OAUTH_CLIENT_SECRET" ] || fail "OAuth client secret empty"
export VERAMESH_OAUTH_CLIENT_SECRET

exec "$BIN" \
  -listen 127.0.0.1:17446 \
  -controller-config "$CONTROLLER_CONFIG" \
  -oauth-config "$OAUTH_CONFIG"
