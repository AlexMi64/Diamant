#!/usr/bin/env python3
"""
Send text from a .txt file to a private Telegram chat via bot API.

Usage:
  export BOT_TOKEN="123456:ABCDEF..."
  python send_text.py --chat-id 123456789 ./message.txt
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib import error, request

MAX_TELEGRAM_MESSAGE_LENGTH = 4096


class InputValidationError(Exception):
    """Raised when CLI arguments or local inputs are invalid."""


class NetworkError(Exception):
    """Raised on transport-level failures."""


@dataclass
class TelegramAPIError(Exception):
    """Structured Telegram API error."""

    status_code: int
    description: str
    retry_after: Optional[int] = None

    def __str__(self) -> str:
        retry_suffix = (
            f" (retry_after={self.retry_after}s)" if self.retry_after is not None else ""
        )
        return f"HTTP {self.status_code}: {self.description}{retry_suffix}"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send text from .txt file to Telegram chat via bot API."
    )
    parser.add_argument(
        "--chat-id",
        required=True,
        help="Target private chat ID (numeric, can be negative).",
    )
    parser.add_argument(
        "file_path",
        help="Path to .txt file with content to send.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP timeout in seconds (default: 10).",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Retry attempts for transient failures (default: 3).",
    )
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if not re.fullmatch(r"-?\d+", str(args.chat_id)):
        raise InputValidationError(
            "Invalid --chat-id. Expected numeric value, e.g. 123456789 or -1001234567890."
        )
    if args.timeout <= 0:
        raise InputValidationError("--timeout must be > 0.")
    if args.max_retries < 0:
        raise InputValidationError("--max-retries must be >= 0.")


def load_token_from_env() -> str:
    token = os.environ.get("BOT_TOKEN", "").strip()
    if not token:
        raise InputValidationError(
            "BOT_TOKEN is not set. Export it before running the script."
        )
    return token


def read_text_file(file_path: str) -> str:
    path = Path(file_path)
    if path.suffix.lower() != ".txt":
        raise InputValidationError("Input file must have .txt extension.")
    if not path.exists() or not path.is_file():
        raise InputValidationError(f"Input file not found: {path}")

    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise InputValidationError(
                "Unable to decode file as UTF-8/UTF-8 with BOM."
            ) from exc
    except OSError as exc:
        raise InputValidationError(f"Failed to read file: {exc}") from exc

    content = content.strip()
    if not content:
        raise InputValidationError("Input file is empty after trimming outer whitespace.")
    return content


def split_into_chunks(text: str, limit: int = MAX_TELEGRAM_MESSAGE_LENGTH) -> List[str]:
    if limit <= 0:
        raise ValueError("limit must be > 0")

    chunks: List[str] = []
    remaining = text
    separators = ("\n\n", "\n", " ")

    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break

        window = remaining[:limit]
        split_index = -1
        for separator in separators:
            pos = window.rfind(separator)
            if pos > 0:
                split_index = pos + len(separator)
                break

        if split_index <= 0:
            split_index = limit

        chunk = remaining[:split_index]
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_index:]

    return chunks


def _parse_retry_after(payload: Dict[str, Any]) -> Optional[int]:
    parameters = payload.get("parameters")
    if isinstance(parameters, dict):
        retry_after = parameters.get("retry_after")
        if isinstance(retry_after, int) and retry_after >= 0:
            return retry_after
    return None


def _build_api_error(status_code: int, raw_body: str) -> TelegramAPIError:
    description = raw_body.strip() or "Unknown Telegram API error"
    retry_after: Optional[int] = None

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        return TelegramAPIError(status_code=status_code, description=description)

    if isinstance(payload, dict):
        description = str(payload.get("description", description))
        retry_after = _parse_retry_after(payload)
        api_error_code = payload.get("error_code")
        if isinstance(api_error_code, int):
            status_code = api_error_code

    return TelegramAPIError(
        status_code=status_code,
        description=description,
        retry_after=retry_after,
    )


def telegram_api_post(
    token: str,
    method: str,
    payload: Dict[str, Any],
    timeout: float,
) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=timeout) as response:
            status = response.getcode()
            raw_response = response.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        raw_error = exc.read().decode("utf-8", errors="replace")
        raise _build_api_error(exc.code, raw_error) from exc
    except error.URLError as exc:
        raise NetworkError(f"Network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise NetworkError(f"Timeout: {exc}") from exc
    except OSError as exc:
        raise NetworkError(f"OS/network error: {exc}") from exc

    if status < 200 or status >= 300:
        raise _build_api_error(status, raw_response)

    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        raise TelegramAPIError(
            status_code=status, description="Telegram returned non-JSON response"
        ) from exc

    if not isinstance(parsed, dict):
        raise TelegramAPIError(
            status_code=status, description="Telegram returned unexpected response shape"
        )

    if not parsed.get("ok", False):
        raise _build_api_error(status, raw_response)

    return parsed


def _is_retryable_api_error(exc: TelegramAPIError) -> bool:
    return exc.status_code == 429 or 500 <= exc.status_code <= 599


def _retry_delay_seconds(attempt_number: int, retry_after: Optional[int]) -> int:
    if retry_after is not None:
        return retry_after
    # 1, 2, 4 for attempts 1, 2, 3...
    return 2 ** (attempt_number - 1)


def send_chunk_with_retry(
    token: str,
    chat_id: str,
    chunk: str,
    timeout: float,
    max_retries: int,
    chunk_index: int,
    total_chunks: int,
) -> int:
    attempt = 0
    while True:
        attempt += 1
        try:
            telegram_api_post(
                token=token,
                method="sendMessage",
                payload={"chat_id": chat_id, "text": chunk},
                timeout=timeout,
            )
            print(
                f"Chunk {chunk_index}/{total_chunks} sent successfully on attempt {attempt}."
            )
            return attempt
        except TelegramAPIError as exc:
            retryable = _is_retryable_api_error(exc)
            retries_used = attempt - 1
            if not retryable or retries_used >= max_retries:
                raise
            delay = _retry_delay_seconds(retries_used + 1, exc.retry_after)
            print(
                f"Chunk {chunk_index}/{total_chunks} failed ({exc}). "
                f"Retrying in {delay}s..."
            )
            time.sleep(delay)
        except NetworkError as exc:
            retries_used = attempt - 1
            if retries_used >= max_retries:
                raise
            delay = _retry_delay_seconds(retries_used + 1, None)
            print(
                f"Chunk {chunk_index}/{total_chunks} network issue ({exc}). "
                f"Retrying in {delay}s..."
            )
            time.sleep(delay)


def send_text(
    token: str,
    chat_id: str,
    text: str,
    timeout: float,
    max_retries: int,
) -> int:
    chunks = split_into_chunks(text)
    total_attempts = 0

    print(f"Prepared {len(chunks)} chunk(s) for sending.")
    for index, chunk in enumerate(chunks, start=1):
        total_attempts += send_chunk_with_retry(
            token=token,
            chat_id=chat_id,
            chunk=chunk,
            timeout=timeout,
            max_retries=max_retries,
            chunk_index=index,
            total_chunks=len(chunks),
        )

    return total_attempts


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = parse_args(argv)
        validate_args(args)

        token = load_token_from_env()
        text = read_text_file(args.file_path)

        attempts = send_text(
            token=token,
            chat_id=str(args.chat_id),
            text=text,
            timeout=args.timeout,
            max_retries=args.max_retries,
        )

        chunk_count = len(split_into_chunks(text))
        print(
            f"Done: sent {chunk_count} chunk(s) successfully, total attempts: {attempts}."
        )
        return 0
    except InputValidationError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    except TelegramAPIError as exc:
        print(f"Telegram API error: {exc}", file=sys.stderr)
        return 1
    except NetworkError as exc:
        print(f"Network error after retries: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
