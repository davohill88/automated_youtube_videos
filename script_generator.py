from __future__ import annotations
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path("data")
SCRIPTS_DIR = Path("scripts")
INPUT_FILE = DATA_DIR / "fantagraphics_upcoming.json"

WORDS_PER_MINUTE = 150

STYLE_GUIDES = {
    "enthusiast": (
        "You are a knowledgeable, passionate comic book critic and historian. "
        "Your tone is informed and thoughtful — like a seasoned film critic who genuinely loves the medium. "
        "Celebrate artistry, editorial vision, and cultural significance. "
        "Energy: 6/10. Use precise language, reference artistic movements, and connect books to their historical context."
    ),
    "hype": (
        "You are a high-energy comic book hype host. "
        "Your tone is electric, urgent, and infectious — like a sports commentator for sequential art. "
        "Every book is an EVENT. Use exclamation points, build momentum, make listeners feel they NEED these books immediately. "
        "Energy: 10/10. Short punchy sentences. CAPS for emphasis."
    ),
    "chill": (
        "You are a laid-back comic shop owner sharing new releases with regulars. "
        "Your tone is conversational, warm, and unhurried — like talking to a knowledgeable friend. "
        "Genuinely enthusiastic but never pushy. Sprinkle in dry humor and personal asides. "
        "Energy: 4/10. Contractions, casual phrasing, occasional tangents."
    ),
}

SYSTEM_PROMPT = """You generate YouTube episode scripts for a comic book new releases channel focused on Fantagraphics titles.

Your output MUST be valid JSON with this exact structure:
{
  "episode_title": "string",
  "intro": {
    "text": "string (spoken script for intro)",
    "hype_rating": number (1-10),
    "visual_notes": "string (B-roll/graphic suggestions for editors)"
  },
  "books": [
    {
      "title": "string",
      "segment_text": "string (spoken script for this book)",
      "hype_rating": number (1-10),
      "visual_notes": "string",
      "cover_image_url": "string or null",
      "price": "string or null"
    }
  ],
  "outro": {
    "text": "string",
    "hype_rating": number (1-10),
    "visual_notes": "string"
  }
}

Rules:
- hype_rating 1=very calm, 10=maximum hype; match the segment energy
- visual_notes are practical directions for video editors
- segment_text is the exact words spoken — no stage directions, no [brackets]
- Each book segment: 30-90 seconds of speaking time (75-225 words at 150 wpm)
- Intro must mention it is a Fantagraphics new releases episode
- Outro must include a call to action (like, subscribe, comment)
- Return ONLY valid JSON — no markdown fences, no prose before or after"""


def load_products(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("products", data) if isinstance(data, dict) else data


def build_user_prompt(products: list[dict], style: str) -> str:
    style_desc = STYLE_GUIDES[style]
    lines = [
        f"STYLE PERSONA:\n{style_desc}",
        "",
        f"UPCOMING FANTAGRAPHICS RELEASES ({len(products)} titles):",
    ]
    for i, p in enumerate(products, 1):
        entry = [f"{i}. {p['title']}"]
        if p.get("description"):
            entry.append(f"   Description: {p['description'][:300]}")
        if p.get("price"):
            entry.append(f"   Price: ${p['price']}")
        if p.get("creators"):
            entry.append(f"   Creators: {', '.join(p['creators'])}")
        if p.get("release_date"):
            entry.append(f"   Release Date: {p['release_date']}")
        lines.append("\n".join(entry))
    lines.append(
        f"\nGenerate a complete YouTube episode script for all {len(products)} books "
        "using the style above. Return ONLY the JSON object."
    )
    return "\n\n".join(lines)


def estimate_duration_seconds(text: str, wpm: int = WORDS_PER_MINUTE) -> int:
    return round((len(text.split()) / wpm) * 60)


def generate_script(products: list[dict], style: str, client: anthropic.Anthropic) -> dict:
    user_prompt = build_user_prompt(products, style)
    with client.messages.stream(
        model="claude-opus-4-7",
        max_tokens=16000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        raw = stream.get_final_message().content[0].text
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif raw.strip().startswith("```"):
        raw = raw.strip()[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    # Find outermost JSON object in case of leading/trailing prose
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start != -1 and end > start:
        raw = raw[start:end]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}", file=sys.stderr)
        print("Raw response snippet:", raw[max(0, e.pos-100):e.pos+100], file=sys.stderr)
        raise


def build_tts_text(script_data: dict) -> str:
    parts = [script_data["intro"]["text"], ""]
    for book in script_data.get("books", []):
        parts.append(book["segment_text"])
        parts.append("")
    parts.append(script_data["outro"]["text"])
    return "\n".join(parts)


def build_timeline(script_data: dict, products_by_title: dict) -> dict:
    timeline = {
        "episode_title": script_data["episode_title"],
        "style": script_data.get("style", ""),
        "total_duration_seconds": 0,
        "segments": [],
    }
    cursor = 0

    def add_segment(seg_type, label, text, cover, notes, hype):
        nonlocal cursor
        dur = estimate_duration_seconds(text)
        m, s = divmod(cursor, 60)
        timeline["segments"].append({
            "type": seg_type,
            "label": label,
            "timestamp": f"{m:02d}:{s:02d}",
            "start_seconds": cursor,
            "duration_seconds": dur,
            "cover_image_url": cover,
            "visual_notes": notes,
            "hype_rating": hype,
        })
        cursor += dur

    intro = script_data["intro"]
    add_segment("intro", "Introduction", intro["text"], None, intro["visual_notes"], intro["hype_rating"])
    for book in script_data.get("books", []):
        cover = book.get("cover_image_url") or products_by_title.get(book["title"], {}).get("cover_image_url")
        add_segment("book", book["title"], book["segment_text"], cover, book["visual_notes"], book["hype_rating"])
    outro = script_data["outro"]
    add_segment("outro", "Outro / Call to Action", outro["text"], None, outro["visual_notes"], outro["hype_rating"])
    timeline["total_duration_seconds"] = cursor
    return timeline


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a YouTube episode script for Fantagraphics upcoming releases."
    )
    parser.add_argument(
        "--style",
        choices=["enthusiast", "hype", "chill"],
        default="enthusiast",
        help="Script style preset (default: enthusiast)",
    )
    parser.add_argument(
        "--input",
        default=str(INPUT_FILE),
        help=f"Input JSON path (default: {INPUT_FILE})",
    )
    parser.add_argument(
        "--max-books",
        type=int,
        default=None,
        help="Limit number of books included in the script",
    )
    args = parser.parse_args()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set. Add it to .env or export it.", file=sys.stderr)
        sys.exit(1)

    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        print("Run fantagraphics_scraper.py first.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading products from {input_path}...")
    products = load_products(input_path)
    if args.max_books:
        products = products[: args.max_books]

    print(f"Generating '{args.style}' script for {len(products)} books...")
    client = anthropic.Anthropic(api_key=api_key)
    script_data = generate_script(products, args.style, client)
    script_data["style"] = args.style
    script_data["generated_at"] = datetime.utcnow().isoformat() + "Z"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"fantagraphics_{args.style}_{timestamp}"

    script_path = SCRIPTS_DIR / f"{base_name}.json"
    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(script_data, f, indent=2, ensure_ascii=False)
    print(f"Script JSON:   {script_path}")

    tts_path = SCRIPTS_DIR / f"{base_name}.tts.txt"
    with open(tts_path, "w", encoding="utf-8") as f:
        f.write(build_tts_text(script_data))
    print(f"TTS text:      {tts_path}")

    products_by_title = {p["title"]: p for p in products}
    timeline = build_timeline(script_data, products_by_title)
    timeline_path = SCRIPTS_DIR / f"{base_name}.timeline.json"
    with open(timeline_path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, indent=2, ensure_ascii=False)
    print(f"Timeline JSON: {timeline_path}")

    total = timeline["total_duration_seconds"]
    print(f"\nEstimated runtime: {total // 60}m {total % 60}s ({len(script_data.get('books', []))} books)")


if __name__ == "__main__":
    main()
