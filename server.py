#!/usr/bin/env python3
"""Small HTTP wrapper for the Strava weekly poster generator."""

from __future__ import annotations

import json
import os
import secrets
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
POSTER = ROOT / "poster.py"
PORT = int(os.environ.get("PORT", "8080"))
DATA = Path(os.environ.get("DATA_DIR", str(ROOT / "data")))
TOKENS = DATA / "tokens.json"
OAUTH_STATES = DATA / "oauth_states.json"


def issue_state(provider: str) -> str:
    state = secrets.token_urlsafe(24)
    try:
        states = json.loads(OAUTH_STATES.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        states = {}
    states[state] = {"provider": provider, "created_at": int(time.time())}
    DATA.mkdir(parents=True, exist_ok=True)
    OAUTH_STATES.write_text(json.dumps(states), encoding="utf-8")
    OAUTH_STATES.chmod(0o600)
    return state


def consume_state(state: str, provider: str) -> bool:
    try:
        states = json.loads(OAUTH_STATES.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    record = states.pop(state, None)
    OAUTH_STATES.write_text(json.dumps(states), encoding="utf-8")
    if not isinstance(record, dict) or record.get("provider") != provider:
        return False
    return int(record.get("created_at", 0)) >= int(time.time()) - 600


def config(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing configuration: {name}")
    return value


def read_tokens() -> dict[str, object]:
    try:
        return json.loads(TOKENS.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_tokens(tokens: dict[str, object]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    TOKENS.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    TOKENS.chmod(0o600)


def open_url(request: urllib.request.Request, timeout: int):
    ca_file = os.environ.get("SSL_CERT_FILE", "").strip()
    if ca_file:
        context = ssl.create_default_context(cafile=ca_file)
        return urllib.request.urlopen(request, timeout=timeout, context=context)
    return urllib.request.urlopen(request, timeout=timeout)


def json_request(url: str, *, method: str = "GET", fields: dict[str, object] | None = None, token: str | None = None) -> dict[str, object]:
    encoded = urllib.parse.urlencode({k: str(v) for k, v in (fields or {}).items()}).encode()
    if method == "GET" and encoded:
        url += ("&" if "?" in url else "?") + encoded.decode()
        encoded = None
    request = urllib.request.Request(url, data=encoded, method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with open_url(request, 30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"upstream API returned {exc.code}: {detail[:500]}") from exc


def strava_access_token() -> str:
    tokens = read_tokens()
    strava = tokens.get("strava", {})
    if not isinstance(strava, dict) or not strava.get("refresh_token"):
        raise RuntimeError("Strava is not connected; open /auth/strava first")
    if int(strava.get("expires_at", 0)) <= int(time.time()) + 60:
        refreshed = json_request("https://www.strava.com/oauth/token", method="POST", fields={
            "client_id": config("STRAVA_CLIENT_ID"),
            "client_secret": config("STRAVA_CLIENT_SECRET"),
            "grant_type": "refresh_token",
            "refresh_token": strava["refresh_token"],
        })
        strava.update(refreshed)
        tokens["strava"] = strava
        write_tokens(tokens)
    return strava["access_token"]


def fetch_strava_csv(week_ending: str | None = None) -> bytes:
    import datetime as dt
    end = dt.date.fromisoformat(week_ending) if week_ending else dt.date.today()
    start = end - dt.timedelta(days=6)
    after = int(dt.datetime.combine(start, dt.time.min, tzinfo=dt.timezone.utc).timestamp())
    activities = json_request("https://www.strava.com/api/v3/athlete/activities", fields={"after": after, "per_page": 100}, token=strava_access_token())
    if not isinstance(activities, list):
        raise RuntimeError("Strava returned an unexpected activity response")
    lines = ["Activity Date,Activity Type,Distance,Moving Time,Elevation Gain"]
    for activity in activities:
        if not isinstance(activity, dict):
            continue
        date = str(activity.get("start_date_local") or activity.get("start_date") or "")
        kind = str(activity.get("sport_type") or activity.get("type") or "Activity")
        distance_km = float(activity.get("distance") or 0) / 1000
        moving_time = int(activity.get("moving_time") or 0)
        elevation = float(activity.get("total_elevation_gain") or 0)
        lines.append(",".join([f'"{date}"', csv_quote(kind), f"{distance_km:.3f}", str(moving_time), f"{elevation:.1f}"]))
    return ("\n".join(lines) + "\n").encode()


def csv_quote(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def facebook_graph(path: str) -> str:
    return f"https://graph.facebook.com/{config('FACEBOOK_GRAPH_VERSION')}/{path.lstrip('/')}"


def upload_facebook_photo(page_id: str, page_token: str, image_path: Path, caption: str) -> dict[str, object]:
    boundary = "----StravaPoster" + secrets.token_hex(12)
    chunks: list[bytes] = []
    def field(name: str, value: str) -> None:
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), value.encode(), b"\r\n"])
    field("access_token", page_token)
    field("caption", caption)
    chunks.extend([f"--{boundary}\r\n".encode(), b'Content-Disposition: form-data; name="source"; filename="strava-week.png"\r\n', b"Content-Type: image/png\r\n\r\n", image_path.read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode()])
    request = urllib.request.Request(facebook_graph(f"{page_id}/photos"), data=b"".join(chunks), method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with open_url(request, 60) as response:
        return json.loads(response.read())


class Handler(BaseHTTPRequestHandler):
    server_version = "StravaPoster/1.0"

    def send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/auth/strava":
            try:
                client_id = config("STRAVA_CLIENT_ID")
                redirect_uri = config("STRAVA_REDIRECT_URI")
            except RuntimeError as exc:
                self.send_json(503, {"error": str(exc)})
                return
            state = issue_state("strava")
            query = urllib.parse.urlencode({"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "approval_prompt": "auto", "scope": "read,activity:read_all", "state": state})
            self.redirect(f"https://www.strava.com/oauth/authorize?{query}")
            return
        if self.path.startswith("/auth/strava/callback"):
            self.handle_strava_callback(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            return
        if self.path == "/auth/facebook":
            try:
                app_id = config("FACEBOOK_APP_ID")
                redirect_uri = config("FACEBOOK_REDIRECT_URI")
            except RuntimeError as exc:
                self.send_json(503, {"error": str(exc)})
                return
            state = issue_state("facebook")
            query = urllib.parse.urlencode({"client_id": app_id, "redirect_uri": redirect_uri, "state": state, "response_type": "code", "scope": "pages_show_list,pages_read_engagement,pages_manage_posts"})
            self.redirect(f"https://www.facebook.com/dialog/oauth?{query}")
            return
        if self.path.startswith("/auth/facebook/callback"):
            self.handle_facebook_callback(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
            return
        if self.path == "/healthz":
            self.send_json(200, {"status": "ok"})
            return
        if self.path == "/":
            self.send_json(200, {"service": "strava-weekly-poster", "usage": "POST CSV bytes to /generate"})
            return
        if self.path.startswith("/output/"):
            requested = (OUTPUT / unquote(self.path.removeprefix("/output/"))).resolve()
            if OUTPUT in requested.parents and requested.is_file():
                content_type = "image/png" if requested.suffix == ".png" else "image/svg+xml" if requested.suffix == ".svg" else "text/plain; charset=utf-8"
                body = requested.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        self.send_json(404, {"error": "not found"})

    def redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def handle_strava_callback(self, query: dict[str, list[str]]) -> None:
        state, code = query.get("state", [""])[0], query.get("code", [""])[0]
        if query.get("error"):
            self.send_json(400, {"error": "Strava authorization was denied", "reason": query.get("error_description", query.get("error"))[0]})
            return
        if not code or not consume_state(state, "strava"):
            self.send_json(400, {"error": "invalid Strava OAuth callback", "detail": "The authorization link may have expired or the service may have restarted. Start again at /auth/strava."})
            return
        try:
            exchanged = json_request("https://www.strava.com/oauth/token", method="POST", fields={"client_id": config("STRAVA_CLIENT_ID"), "client_secret": config("STRAVA_CLIENT_SECRET"), "code": code, "grant_type": "authorization_code"})
            tokens = read_tokens(); tokens["strava"] = exchanged; write_tokens(tokens)
            self.send_json(200, {"connected": "strava", "athlete": exchanged.get("athlete"), "scope": exchanged.get("scope")})
        except (RuntimeError, urllib.error.URLError) as exc:
            self.send_json(502, {"error": str(exc)})

    def handle_facebook_callback(self, query: dict[str, list[str]]) -> None:
        state, code = query.get("state", [""])[0], query.get("code", [""])[0]
        if query.get("error"):
            self.send_json(400, {"error": "Facebook authorization was denied", "reason": query.get("error_description", query.get("error"))[0]})
            return
        if not code or not consume_state(state, "facebook"):
            self.send_json(400, {"error": "invalid Facebook OAuth callback", "detail": "The authorization link may have expired or the service may have restarted. Start again at /auth/facebook."})
            return
        try:
            exchanged = json_request(facebook_graph("oauth/access_token"), fields={"client_id": config("FACEBOOK_APP_ID"), "client_secret": config("FACEBOOK_APP_SECRET"), "redirect_uri": config("FACEBOOK_REDIRECT_URI"), "code": code})
            accounts = json_request(facebook_graph("me/accounts"), fields={"fields": "id,name,access_token"}, token=exchanged["access_token"])
            pages = accounts.get("data", []) if isinstance(accounts, dict) else []
            wanted = os.environ.get("FACEBOOK_PAGE_ID", "").strip()
            page = next((item for item in pages if isinstance(item, dict) and (not wanted or item.get("id") == wanted)), None)
            if not page:
                raise RuntimeError("No matching Facebook Page was returned; set FACEBOOK_PAGE_ID if you manage more than one Page")
            tokens = read_tokens(); tokens["facebook"] = {"page_id": page["id"], "page_name": page.get("name"), "page_access_token": page["access_token"]}; write_tokens(tokens)
            self.send_json(200, {"connected": "facebook", "page_id": page["id"], "page_name": page.get("name")})
        except (RuntimeError, urllib.error.URLError) as exc:
            self.send_json(502, {"error": str(exc)})

    def do_HEAD(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path.startswith("/output/"):
            requested = (OUTPUT / unquote(self.path.removeprefix("/output/"))).resolve()
            if OUTPUT in requested.parents and requested.is_file():
                self.send_response(200)
                self.send_header("Content-Length", str(requested.stat().st_size))
                self.end_headers()
                return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/generate", "/generate-and-post"):
            self.send_json(404, {"error": "not found"})
            return
        generated_from_strava = self.path == "/generate-and-post"
        length = int(self.headers.get("Content-Length", "0"))
        if generated_from_strava:
            try:
                csv_bytes = fetch_strava_csv(self.headers.get("X-Week-Ending"))
            except (RuntimeError, ValueError) as exc:
                self.send_json(502, {"error": str(exc)})
                return
        else:
            csv_bytes = b""
        if not generated_from_strava and (length <= 0 or length > 10_000_000):
            self.send_json(400, {"error": "send a CSV body up to 10 MB"})
            return
        if not generated_from_strava:
            csv_bytes = self.rfile.read(length)
        week_ending = self.headers.get("X-Week-Ending")
        with tempfile.NamedTemporaryFile(suffix=".csv", dir=ROOT, delete=False) as handle:
            handle.write(csv_bytes)
            csv_path = Path(handle.name)
        try:
            result = subprocess.run(["python3", str(POSTER), str(csv_path), *(["--week-ending", week_ending] if week_ending else [])], cwd=ROOT, capture_output=True, text=True)
        finally:
            csv_path.unlink(missing_ok=True)
        if result.returncode:
            self.send_json(422, {"error": result.stderr.strip() or result.stdout.strip()})
            return
        created = [line.removeprefix("Created ") for line in result.stdout.splitlines() if line.startswith("Created ")]
        links = {Path(path).suffix.lstrip("."): "/output/" + Path(path).name for path in created}
        if generated_from_strava:
            try:
                tokens = read_tokens(); facebook = tokens.get("facebook", {})
                if not isinstance(facebook, dict) or not facebook.get("page_id") or not facebook.get("page_access_token"):
                    raise RuntimeError("Facebook is not connected; open /auth/facebook first")
                caption = (OUTPUT / Path(next(path for path in created if path.endswith("-caption.txt"))).name).read_text(encoding="utf-8")
                posted = upload_facebook_photo(str(facebook["page_id"]), str(facebook["page_access_token"]), OUTPUT / Path(next(path for path in created if path.endswith(".png"))).name, caption)
            except (RuntimeError, StopIteration, urllib.error.URLError) as exc:
                self.send_json(502, {"error": str(exc), "artifacts": links})
                return
            return self.send_json(201, {"artifacts": links, "facebook": posted, "message": "poster generated and posted"})
        self.send_json(201, {"artifacts": links, "message": "poster generated"})


if __name__ == "__main__":
    OUTPUT.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
