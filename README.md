# Voucher Code Video Monitor

A small Python bot that polls YouTube channel feeds, downloads new uploads, and reports likely voucher codes with video timestamps. When English captions contain a giveaway rule, supported event terms, prize mentions, or an on-screen-code announcement, OCR targets those timestamps and also samples the full video every 15 seconds. If no useful captions are found, it samples the full video every five seconds.

OCR results are candidates, not guaranteed codes. Review the timestamp and verify each candidate before using it. This tool does not interact with checkout pages or submit purchases.

## Requirements

- Python 3.10+
- `ffmpeg` on `PATH`
- Tesseract OCR on `PATH`
- The Python packages in `requirements.txt`
- Permission to access and process the video source; check YouTube's terms and the creator's rights before using automated downloading.

On Debian or Ubuntu, install the system tools with:

```sh
sudo apt install ffmpeg tesseract-ocr
```

Then install the Python packages:

```sh
python3 -m venv .venv
source .venv/bin/activate.fish
python3 -m pip install -r requirements.txt
```

## Test the latest MK2 video

This downloads and scans the newest upload from Mat Armstrong MK2; no video ID is needed:

```sh
python3 bot.py scan-latest
```

The command downloads the full video to `videos/` (transcript guidance reduces OCR work, not video download time). It scans targeted caption windows every second and adds a full-video overview sample every 15 seconds, catching codes that captions do not mention. If captions are unavailable or no useful cues are found, it samples the full video every five seconds. Pass `--sample-seconds 5` for faster, less thorough targeted sampling; this can miss brief overlays. OCR progress and total execution time are printed. Transcript analysis uses keywords rather than general-purpose semantic understanding, so it can still miss unmentioned events or misread text.

## Monitor uploads

The default monitor watches both channels:

- [Mat Armstrong MK2](https://www.youtube.com/@matarmstrongmk2)
- [MatArmstrongbmx](https://www.youtube.com/@MatArmstrongbmx)

```sh
python3 bot.py monitor
```

The bot checks every minute by default, uses transcript-guided OCR when captions provide useful cues, downloads new uploads to `videos/`, and records completed video IDs in `.hwbt-state.json`. On its first run, it scans the current newest upload from each channel and skips older feed entries. To monitor only MK2, run:

```sh
python3 bot.py monitor --channel-id UC2FZQeaJkpnO6IMkQglvPsg
```

To change the polling interval, pass `--poll-seconds 120` (minimum 30 seconds). Targeted windows use one-second sampling by default, alongside the 15-second overview pass; pass `--sample-seconds 5` for faster targeted scanning with a higher chance of missing brief overlays. Start the monitor before the expected upload and leave it running; it detects new feed entries on its next check. Alerts are sent after the full video has downloaded and been scanned, so they will not arrive at the exact moment the upload appears. Telegram alerts include the timestamp as text without a YouTube thumbnail link.

To monitor different channels, pass one or more channel IDs; specifying them replaces the two defaults:

```sh
python3 bot.py monitor --channel-id UC_CHANNEL_ID --channel-id UC_ANOTHER_CHANNEL_ID
```

To configure Telegram once, open the ignored `.env` file in the project root and paste the newly generated BotFather token after `TELEGRAM_BOT_TOKEN=`. The script loads this file relative to `bot.py`, so it works from other directories too. Messages go to the configured default chat ID; `TELEGRAM_CHAT_ID` can optionally override it.

```sh
TELEGRAM_BOT_TOKEN=your-new-bot-token
```

Keep `.env` private and never commit it. Then start the monitor as usual:

```sh
python3 bot.py monitor
```

Without a token, alerts print to the terminal. The monitor runs until stopped with Ctrl+C.

## Trigger scans from Telegram

Start the listener from the project directory and leave it running:

```sh
cd ~/Code/hwbt_bot
.venv311/bin/python bot.py telegram
```

Send `/scan` to scan the latest MK2 upload immediately. Send `/wait` to save the current latest MK2 video as the baseline; the listener then checks the channel feed about every 20 seconds and starts a scan when a different latest video appears. It replies with the detected title before scanning, sends OCR candidates as they are found, and reports when the scan completes. Use `/status` to check the wait and `/cancel` to stop waiting. `/help` lists commands. Only messages from the configured private chat ID are accepted. Stop the listener with Ctrl+C.

## Scan a local video

```sh
python bot.py scan path/to/video.mp4 --video-id VIDEO_ID
```

By default it samples transcript-targeted frames every second plus overview frames every 15 seconds; without useful captions it samples the full video every five seconds. Use `--sample-seconds 5` for faster, less thorough targeted sampling. Total elapsed time is printed when the command finishes.