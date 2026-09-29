from dataclasses import dataclass, field
from pathlib import Path
import logging

log = logging.getLogger(__name__)

MAX_BLOCKS = 50
MAX_LINES = 30


@dataclass
class Summary:
    headline: str
    counts: dict[str, int]
    lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_blocks(summary: Summary, target_date: str) -> list[dict]:
    blocks: list[dict] = [{
        "type": "header",
        "text": {"type": "plain_text", "text": f"부동산 브리핑 {target_date}", "emoji": True},
    }]

    for w in summary.warnings:
        blocks.append({"type": "context",
                       "elements": [{"type": "mrkdwn", "text": f":warning: {w}"}]})

    if not any(summary.counts.values()):
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn",
                                "text": "오늘은 조용합니다. 새로 올라온 건이 없습니다."}})
        return blocks[:MAX_BLOCKS]

    blocks.append({"type": "section",
                   "text": {"type": "mrkdwn", "text": f"*{summary.headline}*"}})
    blocks.append({"type": "section", "fields": [
        {"type": "mrkdwn", "text": f"*{k}*\n{v}"}
        for k, v in list(summary.counts.items())[:10]
    ]})

    shown = summary.lines[:MAX_LINES]
    if shown:
        blocks.append({"type": "divider"})
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": "\n".join(shown)}})
    if len(summary.lines) > MAX_LINES:
        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn",
             "text": f"외 {len(summary.lines) - MAX_LINES}건은 이미지 참조"}]})

    return blocks[:MAX_BLOCKS]


def post(token: str, channel: str, blocks: list[dict], text: str,
         png_path: Path | None = None) -> None:
    from slack_sdk import WebClient

    client = WebClient(token=token)
    client.chat_postMessage(channel=channel, blocks=blocks, text=text)
    if png_path and Path(png_path).exists():
        client.files_upload_v2(channel=channel, file=str(png_path), title=text)
