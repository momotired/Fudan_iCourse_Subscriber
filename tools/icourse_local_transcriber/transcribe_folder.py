#!/usr/bin/env python3
"""Batch local iCourse videos: extract audio files then transcribe to text."""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


TOOL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_DIR.parent.parent
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi", ".flv"}
_WORKER_TRANSCRIBER = None


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
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        if override or key not in os.environ:
            os.environ[key] = value


def _discover_courses(icourse_dir: Path) -> list[Path]:
    return sorted([p for p in icourse_dir.iterdir() if p.is_dir()], key=lambda p: p.name)


def _collect_videos(course_dir: Path, recursive: bool = True) -> list[Path]:
    iterator = course_dir.rglob("*") if recursive else course_dir.glob("*")
    videos = []
    for path in iterator:
        if path.is_file() and path.suffix.lower() in VIDEO_EXTS:
            videos.append(path)
    return sorted(videos)


def _extract_audio(video_path: Path, audio_path: Path, audio_format: str, overwrite: bool) -> None:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = audio_path.with_suffix(audio_path.suffix + ".tmp")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    cmd += ["-y"]
    cmd += ["-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000"]

    if audio_format == "wav":
        cmd += ["-c:a", "pcm_s16le"]
    else:
        cmd += ["-c:a", "libmp3lame", "-b:a", "96k"]

    cmd += [str(tmp_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        if tmp_path.exists():
            tmp_path.unlink()
        raise RuntimeError(result.stderr.strip() or "ffmpeg failed to extract audio")
    if overwrite and audio_path.exists():
        audio_path.unlink()
    tmp_path.replace(audio_path)


def _get_worker_transcriber():
    """Lazily create one Transcriber instance per worker process."""
    global _WORKER_TRANSCRIBER  # pylint: disable=global-statement
    if _WORKER_TRANSCRIBER is None:
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
        from src.transcriber import Transcriber  # pylint: disable=import-error
        _WORKER_TRANSCRIBER = Transcriber()
    return _WORKER_TRANSCRIBER


def _process_one(task: dict) -> dict:
    """Process one video: extract audio, then optional transcription."""
    video_path = Path(task["video_path"])
    audio_path = Path(task["audio_path"])
    text_path = Path(task["text_path"])
    overwrite = bool(task["overwrite"])
    audio_only = bool(task["audio_only"])
    audio_format = task["audio_format"]
    transcribe_timeout = int(task["transcribe_timeout"])

    result = {"video_rel": task["video_rel"], "status": "processed", "message": ""}

    try:
        if audio_path.exists() and not overwrite:
            audio_msg = f"audio exists: {audio_path}"
        else:
            _extract_audio(video_path, audio_path, audio_format, overwrite=overwrite)
            audio_msg = f"audio written: {audio_path}"

        if audio_only:
            result["message"] = audio_msg
            return result

        if text_path.exists() and not overwrite:
            result["status"] = "skipped"
            result["message"] = f"transcript exists: {text_path}"
            return result

        text_path.parent.mkdir(parents=True, exist_ok=True)
        transcriber = _get_worker_transcriber()
        transcript = transcriber.transcribe_video(
            str(audio_path), timeout=transcribe_timeout
        )
        text_path.write_text(transcript, encoding="utf-8")
        result["message"] = f"transcript written: {text_path}"
        return result
    except Exception as exc:  # noqa: BLE001
        result["status"] = "failed"
        result["message"] = f"{type(exc).__name__}: {exc}"
        return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scan local iCourse folders, extract audio files from videos, "
            "then transcribe to text."
        )
    )
    parser.add_argument(
        "icourse_dir",
        help="Root folder containing course subfolders (downloaded iCourse videos).",
    )
    parser.add_argument(
        "--out-dir",
        default="",
        help="Output root directory (default: <icourse_dir>/_asr_output).",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="Optional .env file path for model-related env vars.",
    )
    parser.add_argument(
        "--audio-format",
        choices=["wav", "mp3"],
        default="wav",
        help="Audio output format (default: wav).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing audio/text files.",
    )
    parser.add_argument(
        "--audio-only",
        action="store_true",
        help="Only extract audio, do not run transcription.",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="List detected videos only, do not process files.",
    )
    parser.add_argument(
        "--non-recursive",
        action="store_true",
        help="Only scan one level in each course directory.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of worker processes for parallel processing (default: 1).",
    )
    parser.add_argument(
        "--transcribe-timeout",
        type=int,
        default=0,
        help=(
            "Per-file transcription timeout in seconds. "
            "Use 0 to disable timeout (default: 0)."
        ),
    )
    return parser


def _print_progress(
    done: int,
    total: int,
    processed: int,
    skipped: int,
    failed: int,
    running: int | None = None,
    pending: int | None = None,
) -> None:
    """Print a compact progress line."""
    pct = (done * 100.0 / total) if total else 100.0
    base = (
        f"[Progress] {done}/{total} ({pct:.1f}%) | "
        f"processed={processed}, skipped={skipped}, failed={failed}"
    )
    if running is not None and pending is not None:
        base += f", running={running}, pending={pending}"
    print(base)


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    icourse_dir = Path(args.icourse_dir).expanduser()
    if not icourse_dir.is_absolute():
        icourse_dir = (Path.cwd() / icourse_dir).resolve()
    else:
        icourse_dir = icourse_dir.resolve()

    if not icourse_dir.exists() or not icourse_dir.is_dir():
        print(f"Invalid icourse_dir: {icourse_dir}")
        return 1

    if args.env_file:
        _load_env_file(Path(args.env_file).expanduser().resolve())

    output_root = Path(args.out_dir).expanduser() if args.out_dir else (icourse_dir / "_asr_output")
    if not output_root.is_absolute():
        output_root = (Path.cwd() / output_root).resolve()
    else:
        output_root = output_root.resolve()

    courses = _discover_courses(icourse_dir)
    if not courses:
        print(f"No course directories found under: {icourse_dir}")
        return 0

    total_videos = 0
    for course_dir in courses:
        videos = _collect_videos(course_dir, recursive=(not args.non_recursive))
        if videos:
            print(f"[Course] {course_dir.name}: {len(videos)} videos")
            for video_path in videos:
                print(f"  - {video_path.relative_to(icourse_dir)}")
            total_videos += len(videos)

    if total_videos == 0:
        print("No video files found.")
        return 0

    if args.list_only:
        print(f"\n[ListOnly] total videos: {total_videos}")
        return 0

    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found in PATH.")
        return 1

    processed = 0
    skipped = 0
    failed = 0
    transcribe_timeout = int(args.transcribe_timeout)

    tasks = []
    for course_dir in courses:
        course_videos = _collect_videos(course_dir, recursive=(not args.non_recursive))
        if not course_videos:
            continue

        for video_path in course_videos:
            rel_video = video_path.relative_to(course_dir)
            rel_stem = rel_video.with_suffix("")

            course_output = output_root / course_dir.name
            audio_path = (course_output / "audio" / rel_stem).with_suffix(f".{args.audio_format}")
            text_path = (course_output / "transcripts" / rel_stem).with_suffix(".txt")
            tasks.append(
                {
                    "video_rel": str(video_path.relative_to(icourse_dir)),
                    "video_path": str(video_path),
                    "audio_path": str(audio_path),
                    "text_path": str(text_path),
                    "audio_format": args.audio_format,
                    "overwrite": bool(args.overwrite),
                    "audio_only": bool(args.audio_only),
                    "transcribe_timeout": transcribe_timeout,
                }
            )

    workers = max(1, int(args.workers))
    if workers > 1:
        print(f"\n[Run] parallel mode with {workers} worker processes")
    else:
        print("\n[Run] single-process mode")
    if transcribe_timeout > 0:
        print(f"[Run] transcription timeout: {transcribe_timeout}s per file")
    else:
        print("[Run] transcription timeout: disabled")

    total_tasks = len(tasks)
    done_count = 0

    def _consume(result: dict, running: int | None = None, pending: int | None = None) -> None:
        nonlocal processed, skipped, failed, done_count
        status = result["status"]
        message = result["message"]
        video_rel = result["video_rel"]
        print(f"[{status}] {video_rel} | {message}")
        if status == "processed":
            processed += 1
        elif status == "skipped":
            skipped += 1
        else:
            failed += 1
        done_count += 1
        _print_progress(
            done=done_count,
            total=total_tasks,
            processed=processed,
            skipped=skipped,
            failed=failed,
            running=running,
            pending=pending,
        )

    if workers == 1:
        for idx, task in enumerate(tasks, start=1):
            print(f"[Start {idx}/{total_tasks}] {task['video_rel']}")
            _consume(_process_one(task))
    else:
        with ProcessPoolExecutor(max_workers=workers) as executor:
            future_to_task = {
                executor.submit(_process_one, task): task for task in tasks
            }
            heartbeat_last = 0.0

            while future_to_task:
                done_futures, _ = wait(
                    list(future_to_task),
                    timeout=2.0,
                    return_when=FIRST_COMPLETED,
                )

                if not done_futures:
                    now = time.time()
                    if now - heartbeat_last >= 2.0:
                        running = sum(
                            1 for f in future_to_task if f.running()
                        )
                        pending = len(future_to_task) - running
                        _print_progress(
                            done=done_count,
                            total=total_tasks,
                            processed=processed,
                            skipped=skipped,
                            failed=failed,
                            running=running,
                            pending=pending,
                        )
                        heartbeat_last = now
                    continue

                for future in done_futures:
                    task = future_to_task.pop(future)
                    try:
                        result = future.result()
                    except Exception as exc:  # noqa: BLE001
                        result = {
                            "video_rel": task["video_rel"],
                            "status": "failed",
                            "message": f"{type(exc).__name__}: {exc}",
                        }
                    running = sum(
                        1 for f in future_to_task if f.running()
                    )
                    pending = len(future_to_task) - running
                    _consume(result, running=running, pending=pending)
                heartbeat_last = time.time()

    print("\n[Done]")
    print(f"  total videos: {total_videos}")
    print(f"  processed: {processed}")
    print(f"  skipped: {skipped}")
    print(f"  failed: {failed}")
    print(f"  output root: {output_root}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
