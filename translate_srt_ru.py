#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Tuple

CODEX_BIN = os.environ.get("CODEX_BIN", "/opt/codex/bin/codex")
CODEX_MODEL = os.environ.get("CODEX_MODEL")
BATCH_SIZE = 50


def parse_srt_blocks(text: str) -> List[List[str]]:
    lines = text.splitlines()
    blocks: List[List[str]] = []
    current: List[str] = []
    for line in lines:
        if line.strip() == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def call_codex(prompt: str) -> str:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        output_path = tmp.name

    try:
        command = [
            CODEX_BIN,
            "exec",
            "--color",
            "never",
            "--output-last-message",
            output_path,
            "-",
        ]
        if CODEX_MODEL:
            command[2:2] = ["-m", CODEX_MODEL]
        subprocess.run(
            command,
            input=prompt,
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return Path(output_path).read_text(encoding="utf-8").strip()
    finally:
        try:
            os.unlink(output_path)
        except FileNotFoundError:
            pass


def translate_lines(lines: List[str]) -> List[str]:
    if not lines:
        return []

    indexed_lines = [{"id": index, "text": line} for index, line in enumerate(lines)]
    payload = json.dumps(indexed_lines, ensure_ascii=False)
    expected_count = len(lines)

    for attempt in range(3):
        prompt = (
            "Translate the following subtitle text entries from English to Russian.\n"
            "Return ONLY a JSON array of objects with fields \"id\" and \"text\".\n"
            f"Keep exactly {expected_count} entries, same order, and the same ids.\n"
            "If a line is empty, return an empty string in \"text\".\n"
            f"Entries:\n{payload}\n"
        )
        raw = call_codex(prompt)
        try:
            translated = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("[")
            end = raw.rfind("]")
            if start == -1 or end == -1:
                if attempt == 2:
                    raise ValueError(f"Model response is not JSON array: {raw}")
                continue
            translated = json.loads(raw[start : end + 1])

        if isinstance(translated, list) and len(translated) == len(lines):
            if translated and isinstance(translated[0], dict):
                texts = [item.get("text", "") for item in translated]
            else:
                texts = ["" if item is None else str(item) for item in translated]
            if len(texts) == len(lines):
                return texts

        if attempt == 2:
            break

    translated_lines: List[str] = []
    for line in lines:
        if line == "":
            translated_lines.append("")
            continue
        prompt = (
            "Translate the following subtitle text line from English to Russian.\n"
            "Return ONLY the translated line with no extra text.\n"
            f"Line:\n{line}\n"
        )
        translated_lines.append(call_codex(prompt).strip())
    if len(translated_lines) != len(lines):
        raise ValueError("Translated output has unexpected length")
    return translated_lines


def translate_file(path: Path, out_path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    blocks = parse_srt_blocks(text)
    with out_path.open("w", encoding="utf-8") as out_file:
        for start in range(0, len(blocks), BATCH_SIZE):
            batch_number = start // BATCH_SIZE + 1
            total_batches = (len(blocks) + BATCH_SIZE - 1) // BATCH_SIZE
            print(f"Translating {path} batch {batch_number}/{total_batches}")
            batch = blocks[start : start + BATCH_SIZE]
            text_lines: List[str] = []
            line_counts: List[int] = []
            for block in batch:
                lines = block[2:]
                line_counts.append(len(lines))
                text_lines.extend(lines)

            translated_lines = translate_lines(text_lines)
            index = 0
            for block, count in zip(batch, line_counts):
                number_line = block[0]
                time_line = block[1] if len(block) > 1 else ""
                out_file.write(number_line + "\n")
                out_file.write(time_line + "\n")
                for _ in range(count):
                    out_file.write(translated_lines[index] + "\n")
                    index += 1
                out_file.write("\n")


def main() -> int:
    repo_root = Path.cwd()
    srt_files = sorted(repo_root.rglob("*.srt"))
    found = 0
    translated = 0
    skipped = 0
    errors: List[str] = []

    for path in srt_files:
        if path.name.endswith(".ru.srt") or path.name.endswith("_ru.srt"):
            continue
        found += 1
        out_path = path.with_name(path.name[:-4] + ".ru.srt")
        if out_path.exists():
            skipped += 1
            continue
        try:
            translate_file(path, out_path)
            translated += 1
        except Exception as exc:  # noqa: BLE001
            if out_path.exists():
                out_path.unlink()
            errors.append(f"{path}: {exc}")

    print("Translation report")
    print(f"Found .srt files: {found}")
    print(f"Translated: {translated}")
    print(f"Skipped (existing .ru.srt): {skipped}")
    if errors:
        print("Errors:")
        for err in errors:
            print(f"- {err}")
    else:
        print("Errors: none")

    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
