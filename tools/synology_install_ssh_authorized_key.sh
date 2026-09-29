#!/bin/sh
set -eu
usage() { echo "usage: $0 --user USER --public-key KEY [--apply]" >&2; exit 64; }
USER_NAME=""
PUBLIC_KEY=""
APPLY=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --user) [ "$#" -ge 2 ] || usage; USER_NAME=$2; shift 2 ;;
    --public-key) [ "$#" -ge 2 ] || usage; PUBLIC_KEY=$2; shift 2 ;;
    --apply) APPLY=1; shift ;;
    *) usage ;;
  esac
done
[ -n "$USER_NAME" ] || usage
[ -n "$PUBLIC_KEY" ] || usage
case "$PUBLIC_KEY" in
  ssh-ed25519\ *|ecdsa-sha2-nistp256\ *|ecdsa-sha2-nistp384\ *|ecdsa-sha2-nistp521\ *|sk-ssh-ed25519@openssh.com\ *|sk-ecdsa-sha2-nistp256@openssh.com\ *|ssh-rsa\ *) ;;
  *) echo "unsupported OpenSSH public key format" >&2; exit 65 ;;
esac
ENTRY=$(getent passwd "$USER_NAME" 2>/dev/null || true)
[ -n "$ENTRY" ] || { echo "local user not found: $USER_NAME" >&2; exit 66; }
HOME_DIR=$(printf "%s\n" "$ENTRY" | awk -F: '{print $6}')
[ -n "$HOME_DIR" ] || { echo "user home is empty: $USER_NAME" >&2; exit 66; }
if ! id -Gn "$USER_NAME" 2>/dev/null | tr " " "\n" | grep -Fxq administrators; then echo "operator user must already belong to DSM administrators group" >&2; exit 67; fi
TAILSCALE_IP=""
if command -v tailscale >/dev/null 2>&1; then TAILSCALE_IP=$(tailscale ip -4 2>/dev/null | head -n 1 || true); fi
printf '{"schema":"VERAMESH_DSM_SSH_OPERATOR_PLAN_V1","user":"%s","home":"%s","tailscale_ip":"%s","apply":%s}\n' "$USER_NAME" "$HOME_DIR" "$TAILSCALE_IP" "$([ "$APPLY" -eq 1 ] && printf true || printf false)"
[ "$APPLY" -eq 1 ] || exit 0
[ "$(id -u)" -eq 0 ] || { echo "--apply must run through sudo/root" >&2; exit 77; }
SSH_DIR="$HOME_DIR/.ssh"
AUTH_KEYS="$SSH_DIR/authorized_keys"
mkdir -p "$SSH_DIR"
chmod 700 "$SSH_DIR"
touch "$AUTH_KEYS"
chmod 600 "$AUTH_KEYS"
if ! grep -Fqx -- "$PUBLIC_KEY" "$AUTH_KEYS"; then printf "%s\n" "$PUBLIC_KEY" >> "$AUTH_KEYS"; fi
chown -R "$USER_NAME" "$SSH_DIR"
chmod 700 "$SSH_DIR"
chmod 600 "$AUTH_KEYS"
if command -v sshd >/dev/null 2>&1; then sshd -t; fi
printf '{"schema":"VERAMESH_DSM_SSH_OPERATOR_RESULT_V1","status":"PASS","user":"%s","authorized_keys":"%s","tailscale_ip":"%s","note":"DSM SSH service state was not changed; enable SSH in DSM Control Panel if required."}\n' "$USER_NAME" "$AUTH_KEYS" "$TAILSCALE_IP"
