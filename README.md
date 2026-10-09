# Strava weekly poster

Create a Facebook-ready weekly activity poster from a Strava CSV export.

## Run it

```bash
python3 poster.py sample_activities.csv --week-ending 2026-10-06
```

The command writes these files to `output/`:

- `strava-week-YYYY-MM-DD.png` — poster image for Facebook
- `strava-week-YYYY-MM-DD.svg` — editable vector source
- `strava-week-YYYY-MM-DD-caption.txt` — suggested post copy

Use a Strava activities export in place of `sample_activities.csv`. The parser
accepts common Strava column names including Activity Date, Activity Type,
Distance, Moving Time, Elapsed Time, and Elevation Gain.

## Run with Docker

```bash
docker compose up --build -d
curl -X POST --data-binary @sample_activities.csv \
  -H 'X-Week-Ending: 2026-10-06' \
  http://localhost:8080/generate
```

The response returns links for the generated PNG, SVG, and caption under
`/output/`. The service also exposes `GET /healthz` for container health.

## Run with Podman

```bash
podman build --format docker -t strava-weekly-poster:local .
podman run -d --name strava-poster -p 8080:8080 \
  -v strava-poster-output:/app/output \
  -v strava-poster-data:/app/data \
  strava-weekly-poster:local

curl -X POST --data-binary @sample_activities.csv \
  -H 'X-Week-Ending: 2026-10-06' \
  http://localhost:8080/generate
```

Stop it with `podman rm -f strava-poster`.

## Connect Strava and Facebook

Copy `.env.example` to `.env`, fill in the app credentials, and recreate the
container with those variables:

```bash
cp .env.example .env
podman rm -f strava-poster
podman run -d --name strava-poster --env-file .env -p 8080:8080 \
  -v strava-poster-output:/app/output \
  -v strava-poster-data:/app/data \
  strava-weekly-poster:local
```

Register these exact callback URLs in each
provider:

- Strava: `http://localhost:8080/auth/strava/callback`
- Facebook: `http://localhost:8080/auth/facebook/callback`

Then open `/auth/strava` and `/auth/facebook` in a browser and approve access.
After both connections succeed, generate and publish in one step:

```bash
curl -X POST -H 'X-Week-Ending: 2026-10-06' \
  http://localhost:8080/generate-and-post
```

`/generate-and-post` fetches the last seven days from Strava, creates the
poster, and uploads the PNG to the connected Facebook Page. It does not post
to a personal Facebook profile.

If the callback reports `CERTIFICATE_VERIFY_FAILED`, mount the corporate CA
bundle and set `SSL_CERT_FILE` to its path inside the container. Keep TLS
verification enabled.

## Facebook publishing

This workspace has no Facebook connector or page token, so the generated PNG
and caption are ready for manual upload. Automated publishing can be added
once a Facebook Page is connected with permission to publish posts; personal
profile posting is generally not available through the standard Page API.
