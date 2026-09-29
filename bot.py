import argparse
import json
import os
import re
import time
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv
from urllib.error import URLError
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


load_dotenv(Path(__file__).resolve().parent / ".env")

ATOM = "http://www.w3.org/2005/Atom"
YOUTUBE = "http://www.youtube.com/xml/schemas/2015"
DEFAULT_CHANNEL_IDS = (
    "UC2FZQeaJkpnO6IMkQglvPsg",  # @matarmstrongmk2
    "UCcLYOTz3ct6_lk9iLToxxAw",  # @MatArmstrongbmx
)
DEFAULT_TELEGRAM_CHAT_ID = "5594740512"
DEFAULT_SAMPLE_SECONDS = 1
FALLBACK_SAMPLE_SECONDS = 5
OVERVIEW_SAMPLE_SECONDS = 15
CODE_TOKEN = re.compile(r"\b[A-Z0-9][A-Z0-9_-]{3,23}\b")
VTT_TIME = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2}[.,]\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2}[.,]\d{3})"
)
CONTEXT_TERMS = re.compile(
    r"\b(bonnet|under the car|tools?|giveaway|vouchers?|promo code|code|"
    r"one hundred pounds|100 pounds)\b|£\s*100",
    re.IGNORECASE,
)
EVERY_TIME = re.compile(r"\b(?:every time|each time|whenever)\b", re.IGNORECASE)
GIVEAWAY_CONTEXT = re.compile(
    r"\b(?:giveaway|give[- ]away|voucher|promo(?:tional)? code|discount code|"
    r"donat(?:e|ed|ing|ion))\b|"
    r"£\s*\d+|\b\d+\s*(?:gbp|pounds?)\b",
    re.IGNORECASE,
)
CODE_SCREEN_CUE = re.compile(
    r"\b(?:code|vouchers?)\b.{0,60}\b(?:on|in) (?:the )?screen\b|"
    r"\b(?:on|in) (?:the )?screen\b.{0,60}\b(?:code|vouchers?)\b|"
    r"\b(?:code|vouchers?)\b.{0,40}\b(?:appear|show|display)\w*\b",
    re.IGNORECASE,
)
PRIZE_CUE = re.compile(
    r"\b(?:giveaway|give[- ]away|gift\s+vouchers?|vouchers?|"
    r"donat(?:e|ed|ing|ion))\b|£\s*\d+|\b\d+\s*(?:gbp|pounds?|quid)\b",
    re.IGNORECASE,
)
EVENT_PATTERNS = {
    "bonnet/hood": re.compile(r"\b(?:bonnet|hood|engine hatch|engine cover)\b", re.IGNORECASE),
    "under-car": re.compile(r"\bunder(?:neath)?\s+(?:the\s+)?(?:car|vehicle)\b", re.IGNORECASE),
    "tools": re.compile(
        r"\b(?:tools?|toolbox|wrench|spanner|screwdriver|socket|ratchet)\b",
        re.IGNORECASE,
    ),
    "plug-in": re.compile(
        r"\bplug(?:s|ged|ging)?\s+(?:(?:the|a)\s+)?(?:auto|car|vehicle|in)\b|"
        r"\bplugs?\s+in\b",
        re.IGNORECASE,
    ),
    "lift/jack": re.compile(r"\b(?:jack|lift(?:ed|ing)?)\b", re.IGNORECASE),
    "wheel": re.compile(r"\bwheels?\b", re.IGNORECASE),
}
COMMON_WORDS = {
    "ABOUT", "AFTER", "BECAUSE", "BONNET", "BUTTON", "CHALLENGE", "CODE",
    "DOWN", "GIVEAWAY", "HAVE", "HERE", "LOOK", "MONEY", "MORE", "NEED",
    "OPEN", "OVER", "THAT", "THEIR", "THERE", "THESE", "THEY", "THIS",
    "TOOLS", "UNDER", "VOUCHER", "WILL", "WITH", "WORKING",
}


@dataclass
class Caption:
    start: float
    end: float
    text: str


@dataclass
class Finding:
    code: str
    timestamp: float
    sightings: int
    context: str = ""


@dataclass
class EventWindow:
    start: float
    end: float
    labels: tuple[str, ...]


def timestamp_seconds(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02}:{(total % 3600) // 60:02}:{total % 60:02}"


def parse_vtt(path: Path) -> list[Caption]:
    if not path.exists():
        return []
    captions = []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    index = 0
    while index < len(lines):
        match = VTT_TIME.search(lines[index])
        if match:
            text_lines = []
            index += 1
            while index < len(lines) and lines[index].strip():
                text_lines.append(re.sub(r"<[^>]+>", "", lines[index]).strip())
                index += 1
            text = " ".join(part for part in text_lines if part)
            if text:
                captions.append(
                    Caption(
                        timestamp_seconds(match.group("start")),
                        timestamp_seconds(match.group("end")),
                        text,
                    )
                )
        index += 1
    return captions


def caption_context(captions: list[Caption], timestamp: float) -> str:
    nearby = [
        caption.text
        for caption in captions
        if caption.start <= timestamp + 30 and caption.end >= timestamp - 30
    ]
    matching = [text for text in nearby if CONTEXT_TERMS.search(text)]
    return " ".join(matching[:3])


def transcript_event_windows(
    captions: list[Caption],
    before_seconds: int = 6,
    after_seconds: int = 12,
) -> tuple[list[EventWindow], str]:
    rule_events: set[str] = set()
    rule_summaries = []

    for caption in captions:
        if not EVERY_TIME.search(caption.text):
            continue
        nearby = [
            item
            for item in captions
            if item.start <= caption.end + 8 and item.end >= caption.start - 8
        ]
        rule_text = " ".join(dict.fromkeys(item.text for item in nearby))
        if not GIVEAWAY_CONTEXT.search(rule_text):
            continue
        matched_events = {
            label
            for label, pattern in EVENT_PATTERNS.items()
            if pattern.search(rule_text)
        }
        if matched_events:
            rule_events.update(matched_events)
            normalized_rule = " ".join(rule_text.split())
            rule_match = EVERY_TIME.search(normalized_rule)
            event_positions = [
                match.start()
                for pattern in EVENT_PATTERNS.values()
                for match in pattern.finditer(normalized_rule)
            ]
            giveaway_positions = [
                match.end() for match in GIVEAWAY_CONTEXT.finditer(normalized_rule)
            ]
            excerpt_start = max(0, min([rule_match.start(), *event_positions]) - 60)
            excerpt_end = min(
                len(normalized_rule),
                max([rule_match.end(), *giveaway_positions]) + 100,
            )
            rule_summaries.append(normalized_rule[excerpt_start:excerpt_end])

    raw_windows = []
    for caption in captions:
        if CODE_SCREEN_CUE.search(caption.text):
            raw_windows.append(
                EventWindow(
                    start=max(0.0, caption.start - before_seconds),
                    end=caption.end + after_seconds,
                    labels=("code-on-screen",),
                )
            )
        if PRIZE_CUE.search(caption.text):
            raw_windows.append(
                EventWindow(
                    start=max(0.0, caption.start - before_seconds),
                    end=caption.end + after_seconds,
                    labels=("prize-mention",),
                )
            )
        matched_events = tuple(
            label
            for label in sorted(rule_events)
            if EVENT_PATTERNS[label].search(caption.text)
        )
        if matched_events:
            raw_windows.append(
                EventWindow(
                    start=max(0.0, caption.start - before_seconds),
                    end=caption.end + after_seconds,
                    labels=matched_events,
                )
            )

    merged_windows: list[EventWindow] = []
    for window in sorted(raw_windows, key=lambda item: item.start):
        if merged_windows and window.start <= merged_windows[-1].end:
            previous = merged_windows[-1]
            merged_windows[-1] = EventWindow(
                start=previous.start,
                end=max(previous.end, window.end),
                labels=tuple(sorted(set(previous.labels) | set(window.labels))),
            )
        else:
            merged_windows.append(window)

    if not merged_windows:
        return [], ""
    if rule_events:
        summary = f"Recurring giveaway rule; detected trigger terms: {', '.join(sorted(rule_events))}"
    elif any("code-on-screen" in window.labels for window in merged_windows):
        summary = "Transcript announces a code or voucher on screen."
    else:
        summary = "Transcript contains prize or value mentions."
    return merged_windows, summary


def extract_codes(text: str) -> set[str]:
    codes = set()
    for token in CODE_TOKEN.findall(text.upper()):
        has_letters = any(character.isalpha() for character in token)
        has_digits = any(character.isdigit() for character in token)
        if not has_letters or (not has_digits and (len(token) < 7 or token in COMMON_WORDS)):
            continue
        if token not in COMMON_WORDS:
            codes.add(token.strip("-_"))
    return codes


def is_plausible_code(code: str, captions: list[Caption], timestamp: float) -> bool:
    letters = sum(character.isalpha() for character in code)
    digits = sum(character.isdigit() for character in code)
    if digits:
        return len(code) >= 6 and letters >= 2
    if len(code) < 7:
        return False
    return any(
        CODE_SCREEN_CUE.search(caption.text)
        and caption.start <= timestamp + 10
        and caption.end >= timestamp - 10
        for caption in captions
    )


def scan_video(
    video: Path,
    video_id: str,
    sample_seconds: int = DEFAULT_SAMPLE_SECONDS,
    captions_path: Path | None = None,
    on_finding: Callable[[Finding], None] | None = None,
) -> list[Finding]:
    if sample_seconds < 1:
        raise ValueError("Sample interval must be at least one second")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is required; install it and make sure it is on PATH")
    try:
        import pytesseract
        from PIL import Image
    except ImportError as error:
        raise RuntimeError("Install Python dependencies with: pip install -r requirements.txt") from error

    captions = parse_vtt(captions_path or video.with_suffix(".en.vtt"))
    event_windows, rule_summary = transcript_event_windows(captions)
    with tempfile.TemporaryDirectory(prefix="hwbt-frames-") as temporary:
        frame_samples: list[tuple[Path, float]] = []
        crop_filter = "crop=iw:ih*0.35:0:ih*0.65"
        if event_windows:
            print(f"Transcript giveaway rule: {rule_summary}")
            print(
                f"Transcript-guided OCR: {len(event_windows)} event windows, "
                f"plus a {OVERVIEW_SAMPLE_SECONDS}s full-video overview; "
                f"targeted sampling every {sample_seconds}s"
            )
            overview_pattern = str(Path(temporary) / "overview-%06d.jpg")
            overview_command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
                "-vf", f"fps=1/{OVERVIEW_SAMPLE_SECONDS},{crop_filter}",
                "-q:v", "4", overview_pattern,
            ]
            subprocess.run(overview_command, check=True, capture_output=True, text=True)
            overview_frames = sorted(Path(temporary).glob("overview-*.jpg"))
            frame_samples.extend(
                (frame, frame_index * OVERVIEW_SAMPLE_SECONDS)
                for frame_index, frame in enumerate(overview_frames)
            )
            for window_index, window in enumerate(event_windows):
                frame_pattern = str(Path(temporary) / f"event-{window_index:04d}-%06d.jpg")
                command = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-ss", f"{window.start:.3f}", "-i", str(video),
                    "-t", f"{window.end - window.start:.3f}",
                    "-vf", f"fps=1/{sample_seconds},{crop_filter}",
                    "-q:v", "4", frame_pattern,
                ]
                subprocess.run(command, check=True, capture_output=True, text=True)
                event_frames = sorted(
                    Path(temporary).glob(f"event-{window_index:04d}-*.jpg")
                )
                frame_samples.extend(
                    (frame, window.start + frame_index * sample_seconds)
                    for frame_index, frame in enumerate(event_frames)
                )
                print(
                    f"Transcript event {window_index + 1}/{len(event_windows)} "
                    f"({', '.join(window.labels)} at {format_timestamp(window.start)})",
                    flush=True,
                )
            frame_samples.sort(key=lambda sample: sample[1])
            unique_samples = []
            seen_timestamps = set()
            for sample in frame_samples:
                timestamp_key = round(sample[1], 2)
                if timestamp_key not in seen_timestamps:
                    seen_timestamps.add(timestamp_key)
                    unique_samples.append(sample)
            frame_samples = unique_samples
        else:
            frame_pattern = str(Path(temporary) / "frame-%06d.jpg")
            fallback_sample_seconds = max(sample_seconds, FALLBACK_SAMPLE_SECONDS)
            command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
                "-vf", f"fps=1/{fallback_sample_seconds},{crop_filter}",
                "-q:v", "4", frame_pattern,
            ]
            subprocess.run(command, check=True, capture_output=True, text=True)
            frames = sorted(Path(temporary).glob("frame-*.jpg"))
            frame_samples = [
                (frame, frame_index * fallback_sample_seconds)
                for frame_index, frame in enumerate(frames)
            ]
            print(
                "No transcript giveaway rule with a supported event term found; "
                f"using full-video sampling every {fallback_sample_seconds}s."
            )

        print(f"OCR scanning {len(frame_samples)} frames")
        counts: dict[str, list[float]] = {}
        for frame_index, (frame, timestamp) in enumerate(frame_samples):
            with Image.open(frame) as image:
                text = pytesseract.image_to_string(image, config="--psm 6")
            for code in extract_codes(text):
                if not is_plausible_code(code, captions, timestamp):
                    continue
                sightings = counts.setdefault(code, [])
                sightings.append(timestamp)
                if on_finding is not None and len(sightings) == 1:
                    on_finding(
                        Finding(
                            code=code,
                            timestamp=timestamp,
                            sightings=1,
                            context=caption_context(captions, timestamp),
                        )
                    )
            if (frame_index + 1) % 100 == 0 or frame_index + 1 == len(frame_samples):
                print(
                    f"OCR progress: {frame_index + 1}/{len(frame_samples)} frames",
                    flush=True,
                )

    findings = []
    for code, sightings in counts.items():
        first_seen = sightings[0]
        findings.append(
            Finding(
                code=code,
                timestamp=first_seen,
                sightings=len(sightings),
                context=caption_context(captions, first_seen),
            )
        )
    return sorted(findings, key=lambda finding: finding.timestamp)


def channel_feed(channel_id: str) -> list[dict[str, str]]:
    url = f"https://www.youtube.com/feeds/videos.xml?{urlencode({'channel_id': channel_id})}"
    request = Request(url, headers={"User-Agent": "hwbt-voucher-monitor/1.0"})
    with urlopen(request, timeout=30) as response:
        root = ET.fromstring(response.read())
    videos = []
    for entry in root.findall(f"{{{ATOM}}}entry"):
        video_id = entry.findtext(f"{{{YOUTUBE}}}videoId")
        title = entry.findtext(f"{{{ATOM}}}title", default="Untitled video")
        published = entry.findtext(f"{{{ATOM}}}published", default="")
        if video_id:
            videos.append({"id": video_id, "title": title, "published": published})
    videos.sort(key=lambda item: item["published"], reverse=True)
    return videos


def latest_video(videos: list[dict[str, str]]) -> dict[str, str]:
    if not videos:
        raise RuntimeError("No videos were found in the channel feed")
    return max(videos, key=lambda item: item.get("published", ""))


def new_upload_after(
    baseline_video_id: str,
    videos: list[dict[str, str]],
) -> dict[str, str] | None:
    if not videos:
        return None
    latest = latest_video(videos)
    return latest if latest["id"] != baseline_video_id else None


def mark_channel_initialized(
    channel_id: str,
    videos: list[dict[str, str]],
    processed: set[str],
    initialized_channels: set[str],
) -> bool:
    if channel_id in initialized_channels:
        return False
    newest = latest_video(videos) if videos else None
    processed.update(
        item["id"]
        for item in videos
        if newest is None or item["id"] != newest["id"]
    )
    initialized_channels.add(channel_id)
    return True


def download_video(video_id: str, directory: Path) -> tuple[Path, Path]:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as error:
        raise RuntimeError("Install Python dependencies with: pip install -r requirements.txt") from error

    directory.mkdir(parents=True, exist_ok=True)
    options = {
        "format": "bestvideo[height<=720]/bestvideo",
        "outtmpl": str(directory / f"{video_id}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en.*", "en"],
        "subtitlesformat": "vtt/best",
        "noplaylist": True,
    }
    with YoutubeDL(options) as downloader:
        info = downloader.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
        video = Path(downloader.prepare_filename(info))
    if not video.exists():
        matches = list(directory.glob(f"{video_id}.*"))
        video_matches = [path for path in matches if path.suffix not in {".vtt", ".json"}]
        if not video_matches:
            raise RuntimeError(f"Downloaded video file not found for {video_id}")
        video = video_matches[0]
    subtitle = video.with_suffix(".en.vtt")
    if not subtitle.exists():
        subtitles = list(directory.glob(f"{video_id}*.vtt"))
        subtitle = subtitles[0] if subtitles else subtitle
    return video, subtitle


def scan_latest(
    channel_id: str,
    video_directory: Path,
    sample_seconds: int = DEFAULT_SAMPLE_SECONDS,
) -> list[Finding]:
    if sample_seconds < 1:
        raise ValueError("Sample interval must be at least one second")
    item = latest_video(channel_feed(channel_id))
    print(f"Scanning latest video: {item['title']}")
    video, subtitle = download_video(item["id"], video_directory)
    findings = scan_video(
        video,
        item["id"],
        sample_seconds=sample_seconds,
        captions_path=subtitle,
        on_finding=lambda finding: announce(item["title"], item["id"], [finding]),
    )
    if not findings:
        announce(item["title"], item["id"], findings)
    return findings

def configured_chat_id() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", DEFAULT_TELEGRAM_CHAT_ID)


def send_notification(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = configured_chat_id()
    if not token:
        print(message)
        return
    payload = urlencode({"chat_id": chat_id, "text": message}).encode()
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError(f"Telegram notification failed with HTTP {response.status}")
    except HTTPError as error:
        raise RuntimeError(f"Telegram notification failed with HTTP {error.code}") from None
    except URLError:
        raise RuntimeError("Telegram notification request failed") from None

def telegram_updates(offset: int | None, timeout_seconds: int = 20) -> list[dict]:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in the project .env file first")
    parameters = {
        "timeout": timeout_seconds,
        "allowed_updates": json.dumps(["message"]),
    }
    if offset is not None:
        parameters["offset"] = offset
    request = Request(
        f"https://api.telegram.org/bot{token}/getUpdates?{urlencode(parameters)}",
        headers={"User-Agent": "hwbt-voucher-monitor/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds + 10) as response:
            result = json.loads(response.read())
    except HTTPError as error:
        raise RuntimeError(f"Telegram polling failed with HTTP {error.code}") from None
    except URLError:
        raise RuntimeError("Telegram polling request failed") from None
    if not result.get("ok"):
        raise RuntimeError("Telegram polling was rejected; check the bot configuration")
    return result.get("result", [])

def telegram_command(text: str) -> str | None:
    parts = text.strip().split(maxsplit=1)
    if not parts or not parts[0].startswith("/"):
        return None
    return parts[0].split("@", maxsplit=1)[0].lower()

def is_authorized_telegram_message(message: dict, chat_id: str) -> bool:
    sender_id = message.get("from", {}).get("id")
    message_chat_id = message.get("chat", {}).get("id")
    return str(sender_id) == chat_id and str(message_chat_id) == chat_id

def listen_telegram(video_directory: Path, sample_seconds: int) -> None:
    if not os.getenv("TELEGRAM_BOT_TOKEN"):
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in the project .env file first")
    if sample_seconds < 1:
        raise ValueError("Sample interval must be at least one second")

    authorized_chat_id = configured_chat_id()
    send_notification("Voucher monitor ready. Send /scan to scan now, /wait for the next MK2 upload, or /help for commands.")
    print(f"Listening for Telegram commands from chat {authorized_chat_id}", flush=True)
    offset = None
    waiting_after: dict[str, str] | None = None
    while True:
        try:
            updates = telegram_updates(offset)
        except RuntimeError:
            print("Telegram polling failed; retrying shortly.", flush=True)
            time.sleep(5)
            continue

        for update in updates:
            offset = update.get("update_id", 0) + 1
            message = update.get("message", {})
            if not is_authorized_telegram_message(message, authorized_chat_id):
                continue
            command = telegram_command(message.get("text", ""))
            if command in {"/start", "/help"}:
                send_notification("Commands: /scan scans the latest MK2 upload now; /wait waits for a newer MK2 upload before scanning; /cancel stops waiting; /status shows the current wait state.")
            elif command in {"/scan", "/latest"}:
                send_notification("Starting scan of the latest Mat Armstrong MK2 upload.")
                started_at = time.perf_counter()
                try:
                    findings = scan_latest(
                        DEFAULT_CHANNEL_IDS[0],
                        video_directory,
                        sample_seconds,
                    )
                except Exception:
                    print("Telegram-triggered scan failed.", flush=True)
                    send_notification("Scan failed. Check the monitor terminal for details.")
                else:
                    elapsed = time.perf_counter() - started_at
                    send_notification(
                        f"Scan complete: {len(findings)} candidate(s) in {elapsed:.1f} seconds."
                    )
            elif command in {"/wait", "/wait-new"}:
                try:
                    waiting_after = latest_video(channel_feed(DEFAULT_CHANNEL_IDS[0]))
                except (URLError, ET.ParseError, RuntimeError):
                    waiting_after = None
                    send_notification("Could not read the current MK2 upload, so waiting was not started.")
                else:
                    send_notification(
                        "Waiting for a newer MK2 upload than:\n"
                        f"{waiting_after['title']}\n"
                        "I will scan when a different latest video appears. Send /cancel to stop waiting."
                    )
            elif command == "/cancel":
                if waiting_after is None:
                    send_notification("No upload wait is active.")
                else:
                    waiting_after = None
                    send_notification("Stopped waiting for a new upload.")
            elif command == "/status":
                if waiting_after is None:
                    send_notification("No upload wait is active.")
                else:
                    send_notification(f"Waiting for a newer MK2 upload than: {waiting_after['title']}")
            elif command is not None:
                send_notification("Unknown command. Send /help for available commands.")

        if waiting_after is not None:
            try:
                new_upload = new_upload_after(
                    waiting_after["id"],
                    channel_feed(DEFAULT_CHANNEL_IDS[0]),
                )
            except (URLError, ET.ParseError, RuntimeError):
                print("Could not check the MK2 feed; will retry after the next Telegram poll.", flush=True)
            else:
                if new_upload is not None:
                    waiting_after = None
                    send_notification(
                        "New MK2 upload detected. Starting scan:\n"
                        f"{new_upload['title']}"
                    )
                    started_at = time.perf_counter()
                    try:
                        findings = scan_latest(
                            DEFAULT_CHANNEL_IDS[0],
                            video_directory,
                            sample_seconds,
                        )
                    except Exception:
                        print("Upload-triggered scan failed.", flush=True)
                        send_notification("Scan failed. Check the listener terminal for details.")
                    else:
                        elapsed = time.perf_counter() - started_at
                        send_notification(
                            f"Scan complete: {len(findings)} candidate(s) in {elapsed:.1f} seconds."
                        )


def announce(title: str, video_id: str, findings: list[Finding]) -> None:
    if not findings:
        print(f"No likely code overlays found in: {title}")
        return
    for finding in findings:
        context = f"\nCaption context: {finding.context}" if finding.context else ""
        send_notification(
            f"Possible voucher code: {finding.code}\n"
            f"Video: {title}\n"
            f"Timestamp: {format_timestamp(finding.timestamp)} "
            f"({finding.sightings} frame sighting(s))\n"
            f"{context.lstrip()}"
        )


def load_state(path: Path) -> tuple[set[str], set[str]]:
    if not path.exists():
        return set(), set()
    state = json.loads(path.read_text(encoding="utf-8"))
    return (
        set(state.get("processed", [])),
        set(state.get("initialized_channels", [])),
    )


def save_state(
    path: Path,
    processed: set[str],
    initialized_channels: set[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "processed": sorted(processed),
                "initialized_channels": sorted(initialized_channels),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def monitor(
    channel_ids: list[str],
    poll_seconds: int,
    video_directory: Path,
    state_path: Path,
    sample_seconds: int = DEFAULT_SAMPLE_SECONDS,
) -> None:
    if poll_seconds < 30:
        raise ValueError("Poll interval must be at least 30 seconds")
    if sample_seconds < 1:
        raise ValueError("Sample interval must be at least one second")
    processed, initialized_channels = load_state(state_path)
    while True:
        for channel_id in channel_ids:
            try:
                videos = channel_feed(channel_id)
                if mark_channel_initialized(
                    channel_id,
                    videos,
                    processed,
                    initialized_channels,
                ):
                    save_state(state_path, processed, initialized_channels)
                for item in reversed(videos):
                    video_id = item["id"]
                    if video_id in processed:
                        continue
                    print(f"New video from {channel_id}: {item['title']}")
                    video, subtitle = download_video(video_id, video_directory)
                    findings = scan_video(
                        video,
                        video_id,
                        sample_seconds=sample_seconds,
                        captions_path=subtitle,
                        on_finding=lambda finding: announce(
                            item["title"], video_id, [finding]
                        ),
                    )
                    if not findings:
                        announce(item["title"], video_id, findings)
                    processed.add(video_id)
                    save_state(state_path, processed, initialized_channels)
            except (URLError, ET.ParseError, subprocess.CalledProcessError, RuntimeError) as error:
                print(f"Monitor error for {channel_id}: {error}")
        time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Find likely voucher codes in YouTube video overlays.")
    commands = parser.add_subparsers(dest="command", required=True)

    monitor_parser = commands.add_parser("monitor", help="Poll the configured YouTube channels for new uploads")
    monitor_parser.add_argument(
        "--channel-id",
        action="append",
        dest="channel_ids",
        help="YouTube channel ID; repeat to monitor multiple channels (overrides defaults)",
    )
    monitor_parser.add_argument("--poll-seconds", type=int, default=60)
    monitor_parser.add_argument(
        "--sample-seconds",
        type=int,
        default=DEFAULT_SAMPLE_SECONDS,
        help="Seconds between OCR frames (default: 1; use 5+ for faster, less thorough scanning)",
    )
    monitor_parser.add_argument("--video-directory", type=Path, default=Path("videos"))
    monitor_parser.add_argument("--state-file", type=Path, default=Path(".hwbt-state.json"))

    latest_parser = commands.add_parser(
        "scan-latest",
        help="Download and scan the latest upload from a channel",
    )
    latest_parser.add_argument("--channel-id", default=DEFAULT_CHANNEL_IDS[0])
    latest_parser.add_argument(
        "--sample-seconds",
        type=int,
        default=DEFAULT_SAMPLE_SECONDS,
        help="Seconds between OCR frames (default: 1; use 5+ for faster, less thorough scanning)",
    )
    latest_parser.add_argument("--video-directory", type=Path, default=Path("videos"))

    scan_parser = commands.add_parser("scan", help="Scan a local video file")
    scan_parser.add_argument("video", type=Path)
    scan_parser.add_argument("--video-id", default="")
    scan_parser.add_argument(
        "--sample-seconds",
        type=int,
        default=DEFAULT_SAMPLE_SECONDS,
        help="Seconds between OCR frames (default: 1; use 5+ for faster, less thorough scanning)",
    )

    telegram_parser = commands.add_parser(
        "telegram",
        help="Listen for Telegram commands to scan the latest MK2 upload",
    )
    telegram_parser.add_argument("--sample-seconds", type=int, default=DEFAULT_SAMPLE_SECONDS)
    telegram_parser.add_argument("--video-directory", type=Path, default=Path("videos"))

    arguments = parser.parse_args()
    if arguments.command == "monitor":
        monitor(
            arguments.channel_ids or list(DEFAULT_CHANNEL_IDS),
            arguments.poll_seconds,
            arguments.video_directory,
            arguments.state_file,
            arguments.sample_seconds,
        )
    elif arguments.command == "scan-latest":
        if arguments.sample_seconds < 1:
            parser.error("--sample-seconds must be at least 1")
        scan_latest(
            arguments.channel_id,
            arguments.video_directory,
            arguments.sample_seconds,
        )
    elif arguments.command == "telegram":
        if arguments.sample_seconds < 1:
            parser.error("--sample-seconds must be at least 1")
        listen_telegram(arguments.video_directory, arguments.sample_seconds)
    else:
        if arguments.sample_seconds < 1:
            parser.error("--sample-seconds must be at least 1")
        findings = scan_video(
            arguments.video,
            arguments.video_id,
            arguments.sample_seconds,
            on_finding=lambda finding: announce(
                arguments.video.stem,
                arguments.video_id,
                [finding],
            ),
        )
        if not findings:
            announce(arguments.video.stem, arguments.video_id, findings)


if __name__ == "__main__":
    started_at = time.perf_counter()
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted.")
    finally:
        elapsed_seconds = time.perf_counter() - started_at
        print(f"Total execution time: {elapsed_seconds:.2f} seconds", flush=True)