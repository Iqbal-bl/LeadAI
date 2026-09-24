# scripts/ â€” XML agent scripts

Runtime data, not code. Each `.xml` file defines an AI voice agent (persona, flow,
rules) in the format read by `outbound/xml_parser.py`.

- The voice app lists and edits these through `/api/scripts`.
- LeadAI can import them into a company's script library (`/api/leadai/scripts/import`).
- `docker-compose.yml` mounts this folder as a volume, so scripts uploaded in
  production survive redeploys. Do not move or rename it without updating the volume.

Utility scripts (backfills, diagnostics) live in `../tools/`.
