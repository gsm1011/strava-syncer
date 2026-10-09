#!/usr/bin/env python3
"""Create a Strava weekly activity poster from a Strava CSV export.

The script intentionally uses only the Python standard library. Strava's
export format is not perfectly stable, so the parser accepts common column
names and treats distance as kilometres when the export is metric and miles
when the header says miles.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import re
from collections import Counter
from pathlib import Path


WIDTH, HEIGHT = 1200, 1500
INK = "#10231f"
MINT = "#dff4e9"
CREAM = "#fffaf0"
CORAL = "#f26b5e"
GOLD = "#f5c45b"
MUTED = "#58706a"


def pick(row: dict[str, str], *names: str) -> str:
    normalized = {re.sub(r"[^a-z0-9]", "", k.lower()): v for k, v in row.items()}
    for name in names:
        value = normalized.get(re.sub(r"[^a-z0-9]", "", name.lower()))
        if value not in (None, ""):
            return value.strip()
    return ""


def parse_date(value: str) -> dt.date:
    value = value.strip()
    for fmt in ("%b %d, %Y, %I:%M:%S %p", "%b %d, %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise ValueError(f"Could not parse activity date: {value!r}") from exc


def number(value: str) -> float:
    cleaned = re.sub(r"[^0-9.\-]", "", value or "")
    return float(cleaned) if cleaned else 0.0


def seconds(value: str) -> int:
    parts = [int(p) for p in re.findall(r"\d+", value or "")]
    if not parts:
        return 0
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[-3] * 3600 + parts[-2] * 60 + parts[-1]


def fmt_time(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def week_range(end: dt.date) -> tuple[dt.date, dt.date]:
    start = end - dt.timedelta(days=6)
    return start, end


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def text(x: int, y: int, value: object, size: int, fill: str = INK, weight: int = 400, anchor: str = "start") -> str:
    return f'<text x="{x}" y="{y}" fill="{fill}" font-family="Arial, Helvetica, sans-serif" font-size="{size}px" font-weight="{weight}" text-anchor="{anchor}">{esc(value)}</text>'


def make_svg(start: dt.date, end: dt.date, activities: list[dict[str, object]], distance_unit: str) -> str:
    total_distance = sum(float(a["distance"]) for a in activities)
    total_seconds = sum(int(a["seconds"]) for a in activities)
    elevation = sum(float(a["elevation"]) for a in activities)
    kinds = Counter(str(a["type"]) for a in activities)
    top_kinds = kinds.most_common(4)
    date_label = f"{start.strftime('%b %-d')} — {end.strftime('%b %-d, %Y')}"
    unit_label = "mi" if distance_unit == "mi" else "km"

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">']
    parts += [f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{CREAM}"/>', f'<rect x="0" y="0" width="1200" height="420" fill="{MINT}"/>']
    parts += [f'<circle cx="1070" cy="150" r="180" fill="{GOLD}" opacity=".72"/>', f'<circle cx="1110" cy="90" r="82" fill="{CORAL}" opacity=".9"/>']
    parts += [text(82, 105, "WEEKLY MOVEMENT", 25, MUTED, 700), text(82, 190, "STRAVA", 82, INK, 800), text(82, 260, "A little farther than last week.", 33, INK, 400), text(82, 320, date_label, 29, MUTED, 700)]

    cards = [(82, 500, "ACTIVITIES", str(len(activities))), (428, 500, "DISTANCE", f"{total_distance:.1f} {unit_label}"), (774, 500, "MOVING TIME", fmt_time(total_seconds))]
    for x, y, label, value in cards:
        parts += [f'<rect x="{x}" y="{y}" width="300" height="215" rx="28" fill="{MINT}"/>', text(x + 28, y + 52, label, 19, MUTED, 700), text(x + 28, y + 133, value, 44, INK, 800)]

    parts += [f'<rect x="82" y="790" width="1036" height="285" rx="28" fill="{INK}"/>', text(120, 850, "THIS WEEK IN MOTION", 22, "#b9dfcd", 700)]
    if elevation:
        parts += [text(120, 955, f"{elevation:,.0f}", 74, CREAM, 800), text(120, 1008, "elevation gain", 24, "#b9dfcd", 400)]
    else:
        parts += [text(120, 955, "Keep going", 54, CREAM, 800), text(120, 1008, "every finish line counts", 24, "#b9dfcd", 400)]
    parts += [text(640, 850, "ACTIVITY MIX", 22, "#b9dfcd", 700)]
    for idx, (kind, count) in enumerate(top_kinds):
        y = 915 + idx * 40
        parts += [f'<circle cx="650" cy="{y-7}" r="7" fill="{CORAL if idx % 2 == 0 else GOLD}"/>', text(675, y, kind, 25, CREAM, 700), text(1035, y, count, 25, CREAM, 800, "end")]
    if not top_kinds:
        parts += [text(640, 930, "No activities found", 26, CREAM, 400)]

    parts += [text(82, 1190, "SHOW UP. MOVE FORWARD.", 32, CORAL, 800), text(82, 1250, "A weekly snapshot of the miles, minutes, and moments that add up.", 24, MUTED, 400), text(82, 1392, "strava weekly recap", 21, MUTED, 700), text(1118, 1392, "#strava #weeklymileage", 21, MUTED, 400, "end")]
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="Strava activities CSV export")
    parser.add_argument("--week-ending", type=dt.date.fromisoformat, help="Sunday ending the week, YYYY-MM-DD; defaults to latest activity")
    parser.add_argument("--out-dir", type=Path, default=Path("output"))
    args = parser.parse_args()

    rows: list[dict[str, object]] = []
    with args.csv.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        distance_unit = "mi" if any("mile" in h.lower() for h in headers) else "km"
        for row in reader:
            activity_date = parse_date(pick(row, "Activity Date", "Date", "start_date"))
            rows.append({
                "date": activity_date,
                "type": pick(row, "Activity Type", "Type", "sport_type") or "Activity",
                "distance": number(pick(row, "Distance", "distance")),
                "seconds": seconds(pick(row, "Moving Time", "Elapsed Time", "moving_time")),
                "elevation": number(pick(row, "Elevation Gain", "elevation_gain", "Total Elevation Gain")),
            })
    if not rows:
        raise SystemExit("No activities found in the CSV.")
    end = args.week_ending or max(a["date"] for a in rows)
    start, end = week_range(end)
    activities = [a for a in rows if start <= a["date"] <= end]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"strava-week-{end.isoformat()}"
    svg_path = args.out_dir / f"{stem}.svg"
    png_path = args.out_dir / f"{stem}.png"
    caption_path = args.out_dir / f"{stem}-caption.txt"
    svg_path.write_text(make_svg(start, end, activities, distance_unit), encoding="utf-8")
    total_distance = sum(float(a["distance"]) for a in activities)
    total_seconds = sum(int(a["seconds"]) for a in activities)
    caption_path.write_text(f"Weekly movement recap — {start:%b %-d}–{end:%b %-d, %Y}\n\n{len(activities)} activities · {total_distance:.1f} {distance_unit} · {fmt_time(total_seconds)}\n\nWhat did you move this week? 🏃‍♀️🚴‍♂️🏊\n#Strava #WeeklyRecap", encoding="utf-8")
    import subprocess
    converters = [
        ["rsvg-convert", "-o", str(png_path), str(svg_path)],
        ["sips", "-s", "format", "png", str(svg_path), "--out", str(png_path)],
    ]
    for command in converters:
        try:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            break
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    else:
        print("PNG conversion unavailable on this host; the editable SVG is ready for export.")
    if png_path.exists():
        print(f"Created {png_path}")
    print(f"Created {svg_path}")
    print(f"Created {caption_path}")


if __name__ == "__main__":
    main()
