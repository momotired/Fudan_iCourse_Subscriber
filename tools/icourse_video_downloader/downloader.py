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
TOOLS_ROOT = TOOL_DIR.parent


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


def _extract_sub_id_from_name(path: Path) -> str | None:
    """Extract sub_id from local filename stem.

    Supports names like:
    - 123456.mp4
    - 123456_lecture-title.mp4
    - 123456-lecture-title.mp4
    """
    stem = path.stem.strip()
    match = re.match(r"^(\d+)(?:[_-].*)?$", stem)
    if not match:
        return None
    return match.group(1)


def _scan_downloaded_sub_ids(course_dirs: list[Path]) -> set[str]:
    """Collect downloaded sub_ids by scanning existing mp4 filenames."""
    sub_ids = set()
    for course_dir in course_dirs:
        if not course_dir.exists() or not course_dir.is_dir():
            continue
        for file_path in course_dir.glob("*.mp4"):
            sub_id = _extract_sub_id_from_name(file_path)
            if sub_id:
                sub_ids.add(sub_id)
    return sub_ids


def _resolve_course_dirs(out_dir: Path, course_id: str, course_title: str) -> tuple[Path, list[Path]]:
    """Return target course dir and all same-course dirs for local scan."""
    dir_name = f"{course_id}-{_safe_filename(course_title, fallback='course')}"
    target_dir = out_dir / dir_name
    same_course_dirs = []
    if out_dir.exists():
        for d in out_dir.iterdir():
            if not d.is_dir():
                continue
            if d.name.startswith(f"{course_id}-") or d.name.startswith(f"{course_id}_"):
                same_course_dirs.append(d)
    if target_dir not in same_course_dirs:
        same_course_dirs.append(target_dir)
    return target_dir, same_course_dirs


def _format_size(num_bytes: float) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(num_bytes)
    unit_idx = 0
    while value >= 1024 and unit_idx < len(units) - 1:
        value /= 1024
        unit_idx += 1
    return f"{value:.1f}{units[unit_idx]}"


def _format_eta(seconds: float) -> str:
    if seconds < 0:
        return "--:--"
    total = int(seconds)
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _download_video_with_progress(client, video_url: str, output_path: Path, chunk_size: int = 1024 * 256) -> Path:
    """Download one video with progress + speed display."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    start = time.time()
    last_print = start
    downloaded = 0

    resp = client.vpn.get(video_url, stream=True, timeout=300)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))

    try:
        with tmp_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)

                now = time.time()
                if now - last_print < 0.5:
                    continue
                elapsed = max(now - start, 1e-6)
                avg_speed = downloaded / elapsed
                instant_speed = len(chunk) / max(now - last_print, 1e-6)

                if total > 0:
                    pct = downloaded * 100 / total
                    remaining = max(total - downloaded, 0)
                    eta = remaining / max(avg_speed, 1e-6)
                    msg = (
                        f"\r      {pct:6.2f}%  "
                        f"{_format_size(downloaded)}/{_format_size(total)}  "
                        f"速率 {_format_size(instant_speed)}/s  "
                        f"均速 {_format_size(avg_speed)}/s  "
                        f"ETA {_format_eta(eta)}"
                    )
                else:
                    msg = (
                        f"\r      已下载 {_format_size(downloaded)}  "
                        f"速率 {_format_size(instant_speed)}/s  "
                        f"均速 {_format_size(avg_speed)}/s"
                    )
                print(msg, end="", flush=True)
                last_print = now
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    finally:
        resp.close()

    elapsed = max(time.time() - start, 1e-6)
    avg_speed = downloaded / elapsed
    if total > 0 and downloaded < total:
        if tmp_path.exists():
            tmp_path.unlink()
        raise RuntimeError(
            f"Incomplete download: {downloaded}/{total} bytes "
            f"({downloaded / total:.1%})"
        )

    os.replace(tmp_path, output_path)
    print(
        f"\r      100.00%  {_format_size(downloaded)}/{_format_size(total or downloaded)}  "
        f"均速 {_format_size(avg_speed)}/s  用时 {_format_eta(elapsed)}"
    )
    return output_path


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
    default_out_dir = TOOLS_ROOT / "course"
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
    out_dir_path = Path(configured_out_dir).expanduser()
    if not out_dir_path.is_absolute():
        out_dir_path = PROJECT_ROOT / out_dir_path
    out_dir = out_dir_path.resolve()
    if not args.list_only:
        out_dir.mkdir(parents=True, exist_ok=True)

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

        course_dir, scan_dirs = _resolve_course_dirs(out_dir, course_id, course_title)
        downloaded_sub_ids = _scan_downloaded_sub_ids(scan_dirs)
        if downloaded_sub_ids:
            print(f"  Local downloaded videos (by sub_id scan): {len(downloaded_sub_ids)}")
        course_dir.mkdir(parents=True, exist_ok=True)

        for lec in selected:
            sub_id = str(lec["sub_id"])
            sub_title = lec.get("sub_title", sub_id)
            safe_title = _safe_filename(sub_title, fallback=sub_id)
            target_path = course_dir / f"{sub_id}_{safe_title}.mp4"

            if not args.overwrite and sub_id in downloaded_sub_ids:
                print(f"    [skip] already downloaded sub_id={sub_id}")
                total_skipped += 1
                continue

            print(f"    [download] sub_id={sub_id} -> {target_path}")
            video_url = client.get_video_url(course_id, sub_id)
            if not video_url:
                print(f"    [fail] no video url for sub_id={sub_id}")
                continue

            _download_video_with_progress(client, video_url, target_path)
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
