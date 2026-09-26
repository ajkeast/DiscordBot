# GitHub Actions secrets after VPS + Postgres cutover

CI uses a workflow `services:` Postgres container. Deploy uses SSH like Dinkboard.

## Delete (obsolete)

- `SQL_HOST`, `SQL_USER`, `SQL_PASSWORD`, `SQL_DATABASE` (remote MySQL)
- `PEBBLEHOST_SERVER_ID`, `PEBBLEHOST_API_TOKEN`

## Add / keep

- `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY` (same values as the Dinkboard repo)
- `XAI_API_KEY` (tests / optional)

Production bot credentials live only on the VPS in `/opt/apps/discordbot/.env` (`DISCORD_TOKEN`, `XAI_API_KEY`, `SQL_*` → Postgres).
