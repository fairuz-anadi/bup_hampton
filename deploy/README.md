# Live deployment

The judged demo runs on a laptop with `docker compose up` (the simulator is single-tenant and local by design).
A live link is a convenience for showing the team, mentors or judges from their own devices.

## Option 1: Cloudflare Tunnel from the demo laptop (quick, free)

```bash
winget install Cloudflare.cloudflared
cloudflared tunnel --url http://localhost:3000     # once the frontend exists: UI + /api through nginx
cloudflared tunnel --url http://localhost:8080     # until then: backend only (Swagger at /docs)
```

It prints an `https://<random>.trycloudflare.com` link that works while the laptop and the command are running.
**Never tunnel port 8000.** That would publish the simulator's `/admin` reset and fault endpoints.

## Option 2: a VM with a stable HTTPS link

Any Ubuntu VM with 2 vCPU / 4 GB (GitHub Student Pack: DigitalOcean credit; Azure for Students).

```bash
curl -fsSL https://raw.githubusercontent.com/fairuz-anadi/bup_hampton/main/deploy/setup-vm.sh | bash
# log out and back in, then:
cd ~/bup_hampton && ./deploy/update.sh
```

`setup-vm.sh` installs Docker, opens only ports 22/80/443, clones the repo and writes a `.env` with random secrets and
`PUBLIC_HOST=<ip>.sslip.io` (a free hostname that resolves to the VM, so Caddy can get a real certificate).
`update.sh` pulls `main` and redeploys; run it again after every push.

What is public on `https://PUBLIC_HOST`:

| Path | Service |
|---|---|
| `/` | frontend (Mission Control), once it exists |
| `/api/*`, `/docs` | backend |
| `/grafana/` | Grafana, read-only for anonymous viewers |

What is **not** public: the simulator, Postgres, Prometheus. `deploy/docker-compose.public.yml` removes their
published ports. Writes still need `X-Operator-Key`, which is only in the VM's `.env`.
