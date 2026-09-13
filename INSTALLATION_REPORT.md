# Installation Report — ComfyUI Image Front-End

Deployment of the home-network image appliance, its local-CA TLS edge, and the per-client
trust setup that makes the browser's clipboard-paste and microphone features work.

> This report describes the **recommended Compose deployment** from a clean checkout. The
> appliance is a private appliance for a trusted LAN, not a public service.

---

## 1. What is deployed

`compose.example.yml` defines two containers and one named volume:

| Service | Image | Role |
| --- | --- | --- |
| `comfyui-image-frontend` | the application image | FastAPI app + queue worker. Listens on **8000** inside the container (plain HTTP, internal only). Serves `/api` and the built frontend. |
| `cif-tls-edge` | `caddy/caddy:2.11.4-alpine` | Unprivileged reverse proxy that **terminates TLS on 8443** and forwards to the app over plaintext on the internal network. This is the service **browsers** talk to. |
| `application-data` | (named volume) | Durable app data (`/data`): SQLite DB, app-owned image files, and the locally issued certificates. |

The app container's published port 8000 remains for **health checks and tooling**. Browsers
should always use the TLS edge on **8443**.

Both containers run as unprivileged user `1000:1000`, `read_only` root filesystem,
`cap_drop: ALL`, `no-new-privileges`, and a `pids_limit`. The edge additionally mounts the
Caddyfile and the certificate directory read-only and keeps its runtime directories on
ephemeral `tmpfs`.

---

## 2. Prerequisites

- A Git checkout on the expected branch with a clean working tree.
- Docker + Compose v2, with daemon access.
- `openssl` on the host (used by `scripts/issue-local-cert.sh` to issue the local certificate).
- A stable LAN address for the appliance (this report uses `192.168.1.5`).
- The ComfyUI runtime(s) the appliance will call, reachable from the appliance.

Generate the configuration:

```sh
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'   # -> CIF_SESSION_SECRET
```

Edit `.env`: set `CIF_SESSION_SECRET`, the bootstrap administrator's temporary password
(≥ 12 characters), and the ComfyUI instances. The TLS-edge keys relevant to browsers are:

| Key | Default | Meaning |
| --- | --- | --- |
| `CIF_TLS_HOSTNAME` | `image-studio.lan` | The browser-facing origin. Browsers use `https://<hostname>:<port>`. Must resolve to the appliance IP on each client. A `.lan` name is used deliberately (`.local`/mDNS resolves differently across OSes). |
| `CIF_TLS_BIND_IP` | `192.168.1.5` (defaults to `CIF_BIND_IP`) | LAN address the edge binds to. |
| `CIF_TLS_HOST_PORT` | `8443` | The TLS edge port. |
| `CIF_TLS_CERT_DIR` | `./data/certificates` | Host directory for the issued root/leaf/key. Outside git; survives rebuilds. |
| `CIF_COOKIE_SECURE` | `true` | The session cookie is `Secure`. Consequence: a plain-HTTP origin can no longer hold a login session (intended). |

> **Do not** use a public/DNS-controllable hostname. The certificate is issued by a
> local CA, so the hostname must be one you control on the LAN.

---

## 3. Build and start

```sh
docker compose -f compose.example.yml up -d --build
docker compose -f compose.example.yml logs -f comfyui-image-frontend cif-tls-edge
```

On first start (and on every update) the TLS certificate is ensured by
`scripts/issue-local-cert.sh`:

- It creates a **stable root CA** once (`data/certificates/ca.crt` / `ca.key`, CN
  `Home Image Studio Local Root CA`, P-256) and reuses it forever.
- It issues a **leaf** (`tls.crt` / `tls.key`) for `CIF_TLS_HOSTNAME` (the SAN) when the
  leaf is missing, its hostname no longer matches, or it nears expiry. Changing
  `CIF_TLS_HOSTNAME` reissues the leaf; the root is unchanged.
- The Caddy edge is configured with the leaf/key and listens on 8443.

The edge's healthcheck probes the full path (TLS termination → proxy → app) with
`curl -fsk https://127.0.0.1:8443/api/health`; `-k` is required because the leaf is signed
by the appliance-local root, which is deliberately **not** in the image's system trust store
(each client imports that root instead).

---

## 4. Why HTTPS is required (the secure-context rationale)

The app's **clipboard-paste** and **voice (microphone)** controls depend on browser APIs that
are only defined in a *secure context*:

- `navigator.clipboard.readText` (paste)
- `navigator.mediaDevices.getUserMedia` (microphone)

A plain-HTTP page on a LAN address (`http://192.168.1.5:8000`) is **not** a secure context, so
those APIs do not exist there — the controls are simply unavailable. `https://` **is** a
secure context even with a self-issued certificate, so the browser exposes both APIs. The
only cost of a self-issued certificate is that each client must trust the local root **once**.

Because the appliance is on the home network (not the public internet), Let's Encrypt/ACME is
not usable. The committed local-CA TLS edge (`cif-tls-edge`) is the solution: real TLS, a real
secure context, and no public CA dependency.

---

## 5. One-time per-client setup

Do this **once per device** (each laptop, phone, tablet that will use clipboard paste or
voice input). Two things are needed: **(a)** trust the local root CA, and **(b)** resolve the
hostname to the appliance's LAN IP.

### (a) Import and trust the local root CA

Copy `data/certificates/ca.crt` from the appliance to the client (AirDrop, a shared folder,
USB, etc.) and add it to the client's trusted root store:

- **macOS**
  1. Double-click `ca.crt` → it opens in **Keychain Access** (added to the *login* keychain).
  2. Drag it into the **system** keychain (or, with the certificate selected, *Keychain
     Access → Certificate Assistant → Add to Keychain* while logged in to system).
  3. Double-click the certificate → expand **Trust** → set *When using this certificate* to
     **Always Trust** → close the window → enter your password.
  4. Verify in *Keychain Access → System → Home Image Studio Local Root CA* that it shows
     "Always Trusted" for TLS.
- **Windows**
  - Per user: `certutil -addstore -user Root ca.crt`
  - All users (elevation): right-click → **Install Certificate** → *Local Machine* →
    *Trusted Root Certification Authorities*, **or** `slmgr.vbs /addstore Root ca.crt`.
- **Linux**
  - System: `sudo cp ca.crt /usr/local/share/ca-certificates/home-image-studio.crt && sudo update-ca-certificates`
  - **Firefox** does not use the OS store: *Settings → Privacy & Security → Certificates →
    View Certificates → Authorities → Import* and tick **Trust this CA to identify websites**.
- **iOS / iPadOS**
  1. AirDrop/transfer `ca.crt` (as a profile) → Settings prompt → **Install** on the Home Screen
     → enter passcode → **Install** → **Done**.
  2. Then **Settings → General → About → Certificate Trust Settings** → enable
     **Full Trust** for `Home Image Studio Local Root CA`.
- **Android**
  - **Settings → Security → Install certificates → CA certificate** → select `ca.crt`.
  - Chrome uses the system store (this is enough). Firefox on Android also uses the Android
    CA store, so the same import covers it.

### (b) Resolve the hostname to the appliance IP

Make `CIF_TLS_HOSTNAME` (e.g. `image-studio.lan`) resolve to `192.168.1.5` on the client:

- **Internal DNS** (preferred): add an A record `image-studio.lan → 192.168.1.5` to your
  router / Pi-hole / Unbound / `dnsmasq`.
- **Hosts file** (no DNS server): add `192.168.1.5  image-studio.lan`
  - macOS/Windows/Linux: `/etc/hosts` (Windows: `C:\Windows\System32\drivers\etc\hosts`).
  - A `.lan` TLD avoids the `.local` mDNS ambiguity on Windows/iOS.

### Verify on the client

Open `https://image-studio.lan:8443` in the browser. You should see the app with **no
certificate warning**, a padlock/secure indicator, and working clipboard-paste + microphone
buttons.

---

## 6. Access

| Purpose | URL |
| --- | --- |
| **Browsers (primary)** | `https://image-studio.lan:8443` |
| Health check / tooling (plain) | `http://192.168.1.5:8000/api/health` |
| App health through the edge | `https://image-studio.lan:8443/api/health` |

Sign in with the bootstrap administrator; the first sign-in requires setting a permanent
password. Bootstrap variables are read only while the database has no users; replacing the
container does **not** reset an existing password.

### Remote access via SSH tunnel (no per-client CA import required to trust)

From a client, forward the edge port over SSH and use loopback (which is itself a secure
context):

```sh
ssh -N -L 8443:192.168.1.5:8443 user@192.168.1.5
# then open https://localhost:8443
```

The leaf is still signed by the local root, so you may still import `ca.crt` (step 5a) to
silence the certificate warning — or accept the browser's one-time exception for the loopback
origin.

---

## 7. Confirming the installation

- `docker compose -f compose.example.yml ps` — both services `healthy`.
- `curl -fsk https://image-studio.lan:8443/api/health` (from a client that trusts the root, or
  `-k`) — returns 200 with `database` and `worker.ready` true.
- `docker compose -f compose.example.yml logs cif-tls-edge` — Caddy bound to 8443 with the
  local leaf; no `leaf` errors.
- In a trusting browser, `https://image-studio.lan:8443` loads, clipboard paste and the
  microphone button are enabled, and a generation round-trips.

Automated coverage of the edge is the Playwright TLS project:

```sh
make e2e-tls          # or: cd frontend && npx playwright test -c playwright.tls.config.mjs
```

It runs against the real Compose stack when a Docker daemon is reachable, otherwise a local
Caddy in front of the in-process app, and asserts the secure context, the `Secure` cookie,
and the clipboard/microphone API surface.

---

## 8. Updating

From a clean checkout on the expected branch:

```sh
./update_and_restart
```

The wrapper around `scripts/update-and-restart.sh` verifies toolchain/daemon/branch, merges
fast-forward only, builds the replacement image **while the current app stays up**, **ensures
the local TLS certificate is current** (issuing via `scripts/issue-local-cert.sh` when the
leaf is missing or hostname-mismatched), then reconciles the whole project (including
`cif-tls-edge`) with a bounded `--wait`. It never runs `docker compose down` and never touches
named volumes or bind-mounted configuration, so user data and the local root CA survive.

---

## 9. Troubleshooting

- **Certificate warning in the browser** — the client does not trust the local root (or the
  hostname does not resolve). Re-do step 5(a) and/or 5(b). Confirm the certificate in the
  browser shows issuer `Home Image Studio Local Root CA`.
- **Page loads but clipboard/mic buttons are disabled** — the page is not being reached over
  `https://`. Confirm the URL is `https://image-studio.lan:8443` (not `http://…:8000`) and the
  browser shows a secure context.
- **Edge not `healthy`** — `docker compose logs cif-tls-edge`; usually a missing/mismatched
  leaf. Re-run `scripts/issue-local-cert.sh` (the update path does this automatically).
- **Login works on `https://` but a session opened on `http://…:8000` cannot log in** —
  expected: the session cookie is `Secure`, so a plain-HTTP origin cannot hold a session.
- **Changing `CIF_TLS_HOSTNAME`** — reissues the leaf (root is unchanged); clients must update
  their hostname→IP mapping to the new name.

---

## 10. Security notes

- The local root CA (`ca.key`) must be kept private and is created once; if it is lost, the
  leaf can still be regenerated from a new root, but every client must then re-import the new
  root.
- The edge and app run unprivileged with no dropped capabilities, read-only root filesystems,
  and ephemeral runtime directories; the certificate directory is mounted read-only into the
  edge.
- The certificate is self-issued for a LAN-only hostname. Do not point it at a public
  hostname, and do not reuse this material for any non-LAN origin.
