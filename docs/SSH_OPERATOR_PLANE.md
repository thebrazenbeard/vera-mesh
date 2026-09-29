# SSH Operator Plane

Status: source-ready; no host effect from repository presence.

SSH is the independent operator/recovery plane for Vera infrastructure. VeraMesh and WorkBridge remain the governed MCP plane.

## Lappy

Use Windows OpenSSH Server with an existing local Administrator account and a dedicated public key. The provided `tools/windows_configure_ssh_operator.ps1` is plan-only unless `-Apply` is supplied.

The apply path installs OpenSSH.Server if needed, installs the administrator public key with restrictive ACLs, adds a marked PubkeyAuthentication/AllowUsers block, optionally disables password authentication, validates sshd configuration, restricts the firewall rule to configured sources, starts sshd, and verifies TCP/22. Config/key/firewall state is restored if later configuration fails.

It does not create an Administrator account or add a non-admin user to Administrators.

## DS216

DSM owns its SSH daemon. `tools/synology_install_ssh_authorized_key.sh` only installs a supplied public key for an existing DSM administrator account. It does not edit DSM sshd configuration, enable root SSH login, or create another daemon.

Run it through sudo/root after enabling SSH in DSM Control Panel if SSH is not already enabled. Root administration remains `sudo -i` after logging in as the admin account.

## Keys and network

Private keys are never committed. Hold the private key on the trusted operator/client side and supply only its public key to these installers.

Ordinary OpenSSH can be reached over LAN or Tailscale networking when host policy permits. This design does not depend on Tailscale SSH.

SSH and VeraMesh/WorkBridge are intentionally independent so either plane can repair the other. Source, build, install, runtime, and effect remain separate.
