#!/usr/bin/env python3
"""Standalone iCourse video downloader.

This tool reuses the repository's WebVPN + iCourse auth/client code to
authenticate, list available playback lectures, and download selected videos
to local files.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path


TOOL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_DIR.parent.parent


def _load_env_file(path: Path, override: bool = False) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ."""
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export "):].strip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ("'", '"')
        ):
            value = value[1:-1]

        if override or key not in os.environ:
            os.environ[key] = value


def _parse_csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def _safe_filename(text: str, fallback: str = "lecture") -> str:
    text = text.strip()
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return fallback
    return text[:100].rstrip()


def _sub_id_sort_key(item: dict) -> tuple[int, str]:
    sub_id = str(item.get("sub_id", ""))
    if sub_id.isdigit():
        return (0, f"{int(sub_id):020d}")
    return (1, sub_id)


def _build_parser(default_env_file: Path, default_out_dir: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download iCourse playback videos to local files.",
    )
    parser.add_argument(
        "--env-file",
        default=str(default_env_file),
        help=f"Path to env file (default: {default_env_file})",
    )
    parser.add_argument(
        "--course-ids",
        default="",
        help="Comma-separated course IDs. Falls back to COURSE_IDS in env.",
    )
    parser.add_argument(
        "--sub-ids",
        default="",
        help="Comma-separated lecture sub_id values. Empty means all playback lectures.",
    )
    parser.add_argument(
        "--out-dir",
        default="",
        help=(
            "Base output directory. "
            f"Default: DOWNLOAD_DIR in env, else {default_out_dir}"
        ),
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="List matching lectures only, do not download.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing local files.",
    )
    parser.add_argument(
        "--login-retries",
        type=int,
        default=3,
        help="Max login attempts (default: 3).",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.2,
        help="Sleep seconds between downloads (default: 0.2).",
    )
    return parser


def _login_with_retry(webvpn_cls, max_attempts: int):
    """Create authenticated WebVPN+iCourse session with retry."""
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"[Login] attempt {attempt}/{max_attempts}")
            vpn = webvpn_cls()
            vpn.login()
            vpn.authenticate_icourse()
            return vpn
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            print(f"[Login] failed: {type(exc).__name__}: {exc}")
            if attempt < max_attempts:
                time.sleep(2)
    raise RuntimeError(f"Login failed after {max_attempts} attempts") from last_error


def main() -> int:
    default_env_file = TOOL_DIR / ".env"
    default_out_dir = TOOL_DIR / "downloads"
    parser = _build_parser(default_env_file, default_out_dir)
    args = parser.parse_args()

    env_file = Path(args.env_file).expanduser().resolve()
    _load_env_file(env_file)

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from src.icourse import ICourseClient  # pylint: disable=import-error
    from src.webvpn import WebVPNSession  # pylint: disable=import-error

    course_ids = _parse_csv(args.course_ids or os.environ.get("COURSE_IDS", ""))
    sub_ids_filter = set(_parse_csv(args.sub_ids))
    configured_out_dir = (
        args.out_dir
        or os.environ.get("DOWNLOAD_DIR", "")
        or str(default_out_dir)
    )
    out_dir = Path(configured_out_dir).expanduser().resolve()

    if not os.environ.get("StuId") or not os.environ.get("UISPsw"):
        print("Missing StuId/UISPsw. Set them in env file or shell environment.")
        return 1

    if not course_ids:
        print("No course IDs provided. Use --course-ids or set COURSE_IDS in .env.")
        return 1

    vpn = _login_with_retry(WebVPNSession, max_attempts=max(1, args.login_retries))
    client = ICourseClient(vpn)

    total_targets = 0
    total_downloaded = 0
    total_skipped = 0

    for course_id in course_ids:
        print(f"\n[Course] {course_id}")
        detail = client.get_course_detail(course_id)
        course_title = detail.get("title", f"course_{course_id}")
        lectures = detail.get("lectures", [])
        playback_lectures = [lec for lec in lectures if lec.get("has_playback")]
        playback_map = {str(lec["sub_id"]): lec for lec in playback_lectures}

        if sub_ids_filter:
            selected = []
            for sub_id in sub_ids_filter:
                lec = playback_map.get(sub_id)
                if lec:
                    selected.append(lec)
                else:
                    print(f"  - skip sub_id={sub_id}: not found or no playback")
        else:
            selected = playback_lectures

        selected = sorted(selected, key=_sub_id_sort_key)
        print(f"  Title: {course_title}")
        print(f"  Playback lectures: {len(playback_lectures)}")
        print(f"  Selected: {len(selected)}")

        if not selected:
            continue

        for lec in selected:
            sub_id = str(lec["sub_id"])
            sub_title = lec.get("sub_title", sub_id)
            lec_date = lec.get("date", "")
            total_targets += 1
            print(f"  - [{sub_id}] {sub_title} ({lec_date})")

        if args.list_only:
            continue

        course_dir_name = f"{course_id}_{_safe_filename(course_title, fallback='course')}"
        course_dir = out_dir / course_dir_name
        course_dir.mkdir(parents=True, exist_ok=True)

        for lec in selected:
            sub_id = str(lec["sub_id"])
            sub_title = lec.get("sub_title", sub_id)
            safe_title = _safe_filename(sub_title, fallback=sub_id)
            target_path = course_dir / f"{sub_id}_{safe_title}.mp4"

            if target_path.exists() and not args.overwrite:
                print(f"    [skip] exists: {target_path}")
                total_skipped += 1
                continue

            print(f"    [download] sub_id={sub_id} -> {target_path}")
            video_url = client.get_video_url(course_id, sub_id)
            if not video_url:
                print(f"    [fail] no video url for sub_id={sub_id}")
                continue

            client.download_video(video_url, str(target_path))
            total_downloaded += 1
            if args.sleep > 0:
                time.sleep(args.sleep)

    print("\n[Done]")
    print(f"  targets: {total_targets}")
    print(f"  downloaded: {total_downloaded}")
    print(f"  skipped(existing): {total_skipped}")
    if args.list_only:
        print("  mode: list-only")
    print(f"  output base: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
