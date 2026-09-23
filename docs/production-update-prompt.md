# Prompt for the next update

> Run `/host/home/astigmatism/deployments/comfyui-image-frontend/update_production --wait`
> to deploy the latest `main` (omit `/host` on the native host). Follow
> `docs/production-deployment-agent.md`. If tooling has not been installed since
> the restored-layout change, first follow `docs/production-service-portal.md`
> using a reviewed tooling revision. Wait for the same durable job to finish and
> report its exit code, deployed SHA, recovery outcome, and verified HTTPS/assets.
> Do not write replacement deployment scripts, configure SSH, or create a
> permanent checkout. The command owns source fetch, backup, build, cutover,
> rollback, and verification.
