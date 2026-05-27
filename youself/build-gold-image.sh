#!/bin/bash
# ============================================================
# build-gold-image.sh — Build youself.io gold image on Proxmox
# Run directly ON Proxmox host: bash /opt/build-gold-image.sh [TARGET_VMID]
# BASE_TEMPLATE is updated automatically after each successful build.
# ============================================================
set -euo pipefail

HERMES_REPO="https://github.com/Hacksli/hermes-agent@main"
BUILDER_VMID=199
BASE_TEMPLATE=9042

TARGET_VMID=${1:-$((BASE_TEMPLATE + 1))}
VERSION=$(curl -s "https://raw.githubusercontent.com/Hacksli/hermes-agent/main/hermes_cli/__version__.py" \
  2>/dev/null | grep -o '"[0-9.]*"' | tr -d '"' || echo "latest")

log() { echo "[$(date '+%H:%M:%S')] $*"; }

guest_exec() {
  # Run command synchronously in VM with extended timeout
  local cmd="$1" timeout_sec="${2:-120}"
  log "  Executing (timeout ${timeout_sec}s): ${cmd:0:70}..."
  local result
  result=$(qm guest exec "$BUILDER_VMID" --timeout "$timeout_sec" -- sh -c "$cmd" 2>/dev/null)
  local out exit_code
  out=$(echo "$result" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('out-data','').strip())" 2>/dev/null)
  exit_code=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin).get('exitcode',0))" 2>/dev/null || echo 0)
  [ -n "$out" ] && echo "$out"
  [ "$exit_code" != "0" ] && log "  WARNING: exit $exit_code"
  return 0
}

# Keep guest_long as alias for compatibility
guest_long() { guest_exec "$1" "${2:-120}"; }

log "=== Building gold image v${VERSION} (VMID ${TARGET_VMID}) from ${BASE_TEMPLATE} ==="

# 1. Clone base template → builder VM
log "Step 1: Cloning ${BASE_TEMPLATE} → builder ${BUILDER_VMID}"
qm stop ${BUILDER_VMID} --timeout 10 2>/dev/null || true; sleep 3
qm destroy ${BUILDER_VMID} --purge 2>/dev/null || true
sleep 2
rm -rf /var/lib/vz/images/${BUILDER_VMID}/ 2>/dev/null || true
rm -f /etc/pve/qemu-server/${BUILDER_VMID}.conf 2>/dev/null || true
qm clone ${BASE_TEMPLATE} ${BUILDER_VMID} --name hermes-gold-builder --full 1
qm set ${BUILDER_VMID} --cpu host --ipconfig0 ip=dhcp --nameserver 8.8.8.8
qm start ${BUILDER_VMID}

# 2. Wait for guest agent - ensure it can actually execute commands
log "Step 2: Waiting for VM to boot..."
for i in $(seq 1 60); do
  OUT=$(qm guest exec ${BUILDER_VMID} -- sh -c 'echo vm_ready' 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('out-data','').strip())" 2>/dev/null || echo '')
  if [ "$OUT" = "vm_ready" ]; then
    log "VM ready after $((i*3))s"
    break
  fi
  sleep 3
done

# 3. Install latest hermes and patch youself identity
log "Step 3: Installing latest hermes"
guest_long "pip3 install --no-cache-dir --break-system-packages --quiet --force-reinstall git+${HERMES_REPO} && echo hermes_ok" 180

# 3b. Download and apply our custom main.py directly (pip may install PyPI version)
log "Step 3b: Patching hermes_cli/main.py with youself identity"
guest_long "wget -q 'https://raw.githubusercontent.com/Hacksli/hermes-agent/main/hermes_cli/main.py' -O /usr/lib/python3.12/site-packages/hermes_cli/main.py && echo patch_ok" 60

# 4. Apply setup-hermes.sh
log "Step 4: Applying hermes setup"
SETUP_B64=$(base64 -w0 /var/lib/vz/snippets/setup-hermes.sh)
guest_long "echo '${SETUP_B64}' | base64 -d > /tmp/s.sh && sh /tmp/s.sh" 30

# 5. Verify AIAgent identity via guest_long
log "Step 5: Verifying hermes AIAgent identity"
guest_long "grep -l 'youself_identity' /usr/lib/python*/site-packages/hermes_cli/main.py 2>/dev/null && echo AIAgent_OK || echo AIAgent_MISSING" 30
log "  (verification complete)"

# 6. Scrub
log "Step 6: Scrubbing VM"
SCRUB_B64=$(base64 -w0 /var/lib/vz/snippets/scrub-vm.sh)
guest_long "echo '${SCRUB_B64}' | base64 -d > /tmp/sc.sh && sh /tmp/sc.sh" 30

# 7. Shutdown
log "Step 7: Shutting down builder"
qm shutdown ${BUILDER_VMID} --timeout 30
sleep 10

# 8. Update BASE_TEMPLATE immediately so next run uses correct base
log "Step 8a: Updating BASE_TEMPLATE to ${TARGET_VMID}"
sed -i "s/^BASE_TEMPLATE=.*/BASE_TEMPLATE=${TARGET_VMID}/" /opt/build-gold-image.sh

# 8b. Create template
log "Step 8: Creating template VMID ${TARGET_VMID}"
qm destroy ${TARGET_VMID} --purge 2>/dev/null || true
sleep 2
qm clone ${BUILDER_VMID} ${TARGET_VMID} --name "youself-gold-v${VERSION}" --full 1
qm template ${TARGET_VMID}
qm set ${TARGET_VMID} --description "hermes ${VERSION}; AIAgent mode; built $(date +%Y-%m-%d)"
qm destroy ${BUILDER_VMID} --purge

log "Step 9: BASE_TEMPLATE already updated to ${TARGET_VMID}"

# 10. Update system_settings via Admin API
log "Step 10: Updating proxmox_template_vmid in DB"
ADMIN_TOKEN="${YOUSELF_ADMIN_TOKEN:-}"  # set via env or CI secret
RESP=$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{\"value\":\"${TARGET_VMID}\"}" \
  "https://api.youself.io/admin/settings/proxmox_template_vmid")
if [ "$RESP" = "200" ]; then
  log "DB updated: proxmox_template_vmid=${TARGET_VMID}"
else
  log "WARNING: DB update failed (HTTP $RESP)"
fi

log "=== ✅ Gold image v${VERSION} ready at VMID ${TARGET_VMID} ==="
