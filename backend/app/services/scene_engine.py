"""Script → Scene engine.

Breaks a script into generation-ready scenes. Two strategies share one output schema:

* ``HeuristicSceneAnalyzer`` – deterministic, works offline (markers, sluglines, paragraphs,
  sentence packing to the target clip length, character/dialogue detection).
* ``LLMSceneAnalyzer`` – asks the configured LLM for a structured breakdown and validates it;
  falls back to the heuristic analyzer on any failure.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.agent.llm import LLMClient, LLMMessage

logger = logging.getLogger(__name__)

WORDS_PER_SECOND = 2.5  # average narration pace (~150 wpm)

SCENE_MARKER = re.compile(r"^\s*(?:#+\s*)?(?:scene|shot|sequence)\s*[#:\-]?\s*(\d+)[^\n]*$", re.I)
SLUGLINE = re.compile(r"^\s*(?:\d+\s*[.)]\s*)?(INT|EXT|INT/EXT|I/E)\.?\s*[\-–—:]?\s*(.+?)(?:\s*[\-–—]\s*(DAY|NIGHT|MORNING|EVENING|DUSK|DAWN|AFTERNOON|CONTINUOUS|LATER))?\s*$", re.I)
SEPARATOR = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,}|={3,})\s*$")
DIALOGUE_LINE = re.compile(r"^\s*([A-Z][A-Z0-9 .'\-]{1,30}?)\s*(?:\([^)]*\))?\s*:\s*(.+)$")
DIALOGUE_BLOCK_NAME = re.compile(r"^\s*([A-Z][A-Z0-9 .'\-]{1,30})\s*(?:\([^)]*\))?\s*$")
PARENTHETICAL = re.compile(r"^\s*\(.*\)\s*$")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
MOVEMENT_WORDS = re.compile(r"\b(runs?|running|walks?|walking|rides?|riding|drives?|driving|chases?|flies|flying|jumps?|climbs?|dances?|swims?|races?)\b", re.I)
EMOTION_WORDS = re.compile(r"\b(cries|crying|smiles?|laughs?|whispers?|screams?|tears|stares?|gazes?|looks? (?:at|into))\b", re.I)
LANDSCAPE_WORDS = re.compile(r"\b(village|city|forest|mountain|river|pond|lake|ocean|sea|desert|sky|field|street|market|temple|skyline|valley|beach|road|highway)\b", re.I)

CAMERA_LIBRARY = {
    "establishing": "Wide establishing shot, slow aerial drift, deep depth of field",
    "movement": "Dynamic tracking shot following the subject, smooth gimbal motion",
    "emotion": "Intimate close-up, shallow depth of field, slow push-in on the face",
    "dialogue": "Medium shot at eye level, gentle handheld feel, soft rack focus between speakers",
    "default": "Medium-wide shot, slow dolly-in, natural lighting",
    "detail": "Macro insert shot, slow pan across details, soft bokeh",
}

STYLE_SUFFIX = {
    "cinematic": "cinematic lighting, anamorphic lens, film grain, color graded, 24fps motion blur, highly detailed",
    "realistic": "photorealistic, natural lighting, real camera footage, 35mm lens, high dynamic range",
    "photorealistic": "photorealistic, ultra detailed, natural skin texture, realistic lighting, 8k",
    "documentary": "documentary footage, handheld camera, natural light, authentic, observational",
    "anime": "anime style, vibrant colours, expressive characters, cel shading, studio quality animation",
    "3d": "3D animated, Pixar-style render, subsurface scattering, soft global illumination",
    "cartoon": "2D cartoon animation, bold outlines, flat colours, playful motion",
    "noir": "black and white film noir, hard shadows, venetian blind lighting, smoky atmosphere",
}


@dataclass
class SceneDraft:
    title: str = ""
    duration_seconds: float = 8.0
    characters: list[str] = field(default_factory=list)
    location: str = ""
    visual: str = ""
    camera: str = ""
    dialogue: str = ""
    narration: str = ""
    prompt: str = ""
    negative_prompt: str = ""
    platform_key: str | None = None
    reference_asset_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnalysisResult:
    scenes: list[SceneDraft]
    method: str
    warnings: list[str] = field(default_factory=list)


@dataclass
class AnalysisContext:
    visual_style: str = "cinematic"
    clip_duration_seconds: float = 8.0
    target_duration_seconds: float | None = None
    max_scenes: int = 200
    characters: list[dict[str, Any]] = field(default_factory=list)  # {name, description, reference_asset_id}
    aspect_ratio: str = "16:9"
    platform_name: str = "a web video generator"
    negative_prompt: str = "blurry, low quality, distorted faces, extra limbs, text, watermark, flicker"


# ------------------------------------------------------------------------ heuristics
class HeuristicSceneAnalyzer:
    def analyze(self, script: str, ctx: AnalysisContext) -> AnalysisResult:
        warnings: list[str] = []
        text = _normalise(script)
        if not text.strip():
            return AnalysisResult([], "heuristic", ["Script is empty"])
        blocks = self._split_blocks(text)
        clip = max(1.0, float(ctx.clip_duration_seconds or 8.0))
        drafts: list[SceneDraft] = []
        for block in blocks:
            drafts.extend(self._block_to_scenes(block, ctx, clip))
        if not drafts:
            return AnalysisResult([], "heuristic", ["No scenes could be derived from the script"])
        if ctx.target_duration_seconds:
            self._fit_target_duration(drafts, ctx.target_duration_seconds, clip, warnings)
        if len(drafts) > ctx.max_scenes:
            warnings.append(f"Script produced {len(drafts)} scenes; truncated to the limit of {ctx.max_scenes}")
            drafts = drafts[: ctx.max_scenes]
        for i, draft in enumerate(drafts):
            draft.camera = draft.camera or self._camera_for(draft, i)
            draft.prompt = draft.prompt or build_prompt(draft, ctx)
            draft.negative_prompt = draft.negative_prompt or ctx.negative_prompt
            if not draft.title:
                draft.title = _title_from_text(draft.visual or draft.narration or draft.dialogue, i)
            draft.reference_asset_ids = _reference_ids_for(draft.characters, ctx)
        return AnalysisResult(drafts, "heuristic", warnings)

    # -- splitting ---------------------------------------------------------------
    def _split_blocks(self, text: str) -> list[dict[str, Any]]:
        lines = text.split("\n")
        blocks: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        explicit = False
        for line in lines:
            if SEPARATOR.match(line):
                current = None
                continue
            marker = SCENE_MARKER.match(line)
            slug = SLUGLINE.match(line)
            if marker or slug:
                explicit = True
                current = {"title": "", "location": "", "lines": []}
                if marker:
                    rest = re.sub(r"^\s*(?:#+\s*)?(?:scene|shot|sequence)\s*[#:\-]?\s*\d+\s*[:\-–—.]?\s*", "", line, flags=re.I).strip()
                    slug_in_rest = SLUGLINE.match(rest) if rest else None
                    if slug_in_rest:
                        current["location"] = _clean_location(slug_in_rest)
                    elif rest and (len(rest.split()) > 8 or rest.endswith((".", "!", "?")) or len(_sentences(rest)) > 1):
                        # "Scene 3: Night. A storm rolls in." – an inline description, not just a heading:
                        # keep it as scene content so characters/location/visuals are derived from it.
                        current["title"] = _title_from_text(_sentences(rest)[0] if _sentences(rest) else rest, len(blocks)).rstrip(".")
                        current["lines"].append(rest)
                    else:
                        current["title"] = rest
                else:
                    current["location"] = _clean_location(slug)
                blocks.append(current)
                continue
            if current is None:
                current = {"title": "", "location": "", "lines": []}
                blocks.append(current)
            current["lines"].append(line)
        if explicit:
            return [b for b in blocks if "".join(b["lines"]).strip() or b["title"] or b["location"]]
        # No explicit markers → paragraphs
        paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
        if len(paragraphs) <= 1:
            paragraphs = [text]
        return [{"title": "", "location": "", "lines": p.split("\n")} for p in paragraphs]

    def _block_to_scenes(self, block: dict[str, Any], ctx: AnalysisContext, clip: float) -> list[SceneDraft]:
        dialogue_lines: list[tuple[str, str]] = []
        action_lines: list[str] = []
        pending_speaker: str | None = None
        for raw in block["lines"]:
            line = raw.rstrip()
            if not line.strip():
                pending_speaker = None
                continue
            if PARENTHETICAL.match(line):
                continue
            m = DIALOGUE_LINE.match(line)
            if m and not SLUGLINE.match(line):
                dialogue_lines.append((m.group(1).strip().title(), m.group(2).strip()))
                pending_speaker = None
                continue
            m2 = DIALOGUE_BLOCK_NAME.match(line)
            if m2 and len(line.strip().split()) <= 4 and line.strip().upper() == line.strip():
                pending_speaker = m2.group(1).strip().title()
                continue
            if pending_speaker:
                dialogue_lines.append((pending_speaker, line.strip()))
                continue
            action_lines.append(line.strip())
        action_text = " ".join(action_lines).strip()
        known_names = [c["name"] for c in ctx.characters if c.get("name")]
        characters = _detect_characters(action_text + " " + " ".join(d[1] for d in dialogue_lines), known_names)
        for speaker, _ in dialogue_lines:
            if speaker not in characters:
                characters.append(speaker)
        location = block["location"] or _guess_location(action_text)
        # Sentence packing into clips of ~clip seconds
        units: list[tuple[str, str]] = []  # (kind, text)
        for sentence in _sentences(action_text):
            units.append(("action", sentence))
        for speaker, text in dialogue_lines:
            units.append(("dialogue", f"{speaker}: {text}"))
        if not units:
            if block["title"] or location:
                units.append(("action", block["title"] or f"Establishing shot of {location}"))
            else:
                return []
        chunks: list[list[tuple[str, str]]] = []
        current: list[tuple[str, str]] = []
        current_seconds = 0.0
        for unit in units:
            seconds = _estimate_seconds(unit[1])
            if current and current_seconds + seconds > clip * 1.15:
                chunks.append(current)
                current, current_seconds = [], 0.0
            current.append(unit)
            current_seconds += seconds
        if current:
            chunks.append(current)
        scenes: list[SceneDraft] = []
        for idx, chunk in enumerate(chunks):
            actions = [t for k, t in chunk if k == "action"]
            dialogues = [t for k, t in chunk if k == "dialogue"]
            total = sum(_estimate_seconds(t) for _, t in chunk)
            duration = float(min(max(round(total, 1), min(4.0, clip)), clip))
            draft = SceneDraft(
                title=(block["title"] if idx == 0 else f"{block['title']} (part {idx + 1})") if block["title"] else "",
                duration_seconds=duration,
                characters=[c for c in characters if any(c.lower() in t.lower() for _, t in chunk)] or characters[:2],
                location=location,
                visual=" ".join(actions) or f"{', '.join(characters) or 'The subject'} in {location or 'the scene'}",
                dialogue="\n".join(dialogues),
                narration=" ".join(actions),
            )
            scenes.append(draft)
        return scenes

    def _fit_target_duration(self, drafts: list[SceneDraft], target: float, clip: float, warnings: list[str]) -> None:
        total = sum(d.duration_seconds for d in drafts)
        if total <= 0:
            return
        ratio = target / total
        if 0.8 <= ratio <= 1.25:
            return
        for d in drafts:
            d.duration_seconds = float(min(clip, max(2.0, round(d.duration_seconds * ratio, 1))))
        new_total = sum(d.duration_seconds for d in drafts)
        if new_total < target * 0.8:
            warnings.append(
                f"Script covers ~{int(new_total)}s but the target is {int(target)}s; add more script content or increase clip duration."
            )

    def _camera_for(self, draft: SceneDraft, index: int) -> str:
        text = f"{draft.visual} {draft.dialogue}"
        if index == 0 or (LANDSCAPE_WORDS.search(draft.location or "") and not draft.dialogue and len(draft.visual) < 160):
            return CAMERA_LIBRARY["establishing"]
        if draft.dialogue:
            return CAMERA_LIBRARY["dialogue"]
        if MOVEMENT_WORDS.search(text):
            return CAMERA_LIBRARY["movement"]
        if EMOTION_WORDS.search(text):
            return CAMERA_LIBRARY["emotion"]
        return CAMERA_LIBRARY["default"] if index % 3 else CAMERA_LIBRARY["detail"]


# --------------------------------------------------------------------------- LLM mode
class LLMSceneAnalyzer:
    SYSTEM_PROMPT = (
        "You are a film director and prompt engineer preparing a script for an AI video generator. "
        "Split the script into sequential scenes that each fit in a single generated clip. "
        "Return ONLY JSON of the shape {\"scenes\": [{\"title\": str, \"duration_seconds\": number, \"characters\": [str], "
        "\"location\": str, \"visual\": str, \"camera\": str, \"dialogue\": str, \"narration\": str, \"prompt\": str}]}. "
        "The prompt must be a complete, self-contained generation prompt describing subject, action, setting, lighting, "
        "camera and style, and must include the physical description of every character that appears. Never exceed the "
        "maximum clip duration. Do not add commentary."
    )

    def __init__(self, client: LLMClient, fallback: HeuristicSceneAnalyzer | None = None) -> None:
        self.client = client
        self.fallback = fallback or HeuristicSceneAnalyzer()

    async def analyze(self, script: str, ctx: AnalysisContext) -> AnalysisResult:
        try:
            character_lines = "\n".join(f"- {c['name']}: {c.get('description', '')}" for c in ctx.characters if c.get("name")) or "(none provided)"
            user = (
                f"Visual style: {ctx.visual_style}\nAspect ratio: {ctx.aspect_ratio}\nMaximum clip duration: {ctx.clip_duration_seconds} seconds\n"
                f"Target total duration: {ctx.target_duration_seconds or 'not specified'} seconds\nTarget platform: {ctx.platform_name}\n"
                f"Characters:\n{character_lines}\n\nSCRIPT:\n{script[:60_000]}"
            )
            response = await self.client.complete(
                [LLMMessage("system", self.SYSTEM_PROMPT), LLMMessage("user", user)], json_mode=True, max_tokens=8000, temperature=0.3
            )
            data = response.json()
            raw_scenes = data.get("scenes") if isinstance(data, dict) else data
            if not isinstance(raw_scenes, list) or not raw_scenes:
                raise ValueError("LLM returned no scenes")
            drafts: list[SceneDraft] = []
            clip = float(ctx.clip_duration_seconds or 8.0)
            for i, raw in enumerate(raw_scenes[: ctx.max_scenes]):
                if not isinstance(raw, dict):
                    continue
                duration = _to_float(raw.get("duration_seconds"), clip)
                draft = SceneDraft(
                    title=str(raw.get("title") or "")[:200],
                    duration_seconds=float(min(max(duration, 1.0), clip)),
                    characters=[str(c)[:60] for c in (raw.get("characters") or []) if str(c).strip()][:10],
                    location=str(raw.get("location") or "")[:300],
                    visual=str(raw.get("visual") or "")[:8000],
                    camera=str(raw.get("camera") or "")[:2000],
                    dialogue=str(raw.get("dialogue") or "")[:8000],
                    narration=str(raw.get("narration") or "")[:8000],
                    prompt=str(raw.get("prompt") or "")[:12000],
                    negative_prompt=ctx.negative_prompt,
                )
                if not draft.prompt:
                    draft.prompt = build_prompt(draft, ctx)
                if not draft.title:
                    draft.title = _title_from_text(draft.visual or draft.narration, i)
                draft.reference_asset_ids = _reference_ids_for(draft.characters, ctx)
                drafts.append(draft)
            if not drafts:
                raise ValueError("LLM scenes were empty after validation")
            return AnalysisResult(drafts, f"llm:{response.model}", [])
        except Exception as exc:
            logger.warning("LLM scene analysis failed, falling back to heuristics: %s", exc)
            result = self.fallback.analyze(script, ctx)
            result.method = "heuristic-fallback"
            result.warnings.insert(0, f"AI analysis unavailable ({str(exc)[:160]}); used rule-based breakdown instead.")
            return result


# ----------------------------------------------------------------------------- helpers
def build_prompt(draft: SceneDraft, ctx: AnalysisContext) -> str:
    descriptions = []
    for name in draft.characters:
        match = next((c for c in ctx.characters if c.get("name", "").lower() == name.lower()), None)
        if match and match.get("description"):
            descriptions.append(f"{name} ({match['description'].strip().rstrip('.')})")
        else:
            descriptions.append(name)
    parts = []
    subject = ", ".join(descriptions)
    action = draft.visual.strip()
    if subject and action:
        parts.append(f"{subject}. {action}")
    elif action:
        parts.append(action)
    elif subject:
        parts.append(subject)
    if draft.location:
        parts.append(f"Setting: {draft.location}")
    if draft.dialogue:
        first_line = draft.dialogue.split("\n")[0]
        parts.append(f"Characters speaking naturally, lip-synced: \"{first_line.split(':', 1)[-1].strip()[:140]}\"")
    if draft.camera:
        parts.append(f"Camera: {draft.camera}")
    style = ctx.visual_style.strip().lower()
    suffix = STYLE_SUFFIX.get(style, f"{ctx.visual_style} style, highly detailed, consistent characters")
    parts.append(f"Style: {suffix}")
    parts.append(f"Aspect ratio {ctx.aspect_ratio}, duration {draft.duration_seconds:g} seconds")
    return ". ".join(p.rstrip(".") for p in parts if p).strip() + "."


def _normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    return re.sub(r"[ ]{2,}", " ", text)


def _sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = [p.strip() for p in SENTENCE_SPLIT.split(text) if p.strip()]
    merged: list[str] = []
    for part in parts:
        if merged and len(part.split()) < 4:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


def _estimate_seconds(text: str) -> float:
    words = max(1, len(text.split()))
    return max(1.5, words / WORDS_PER_SECOND)


def _clean_location(match: re.Match[str]) -> str:
    place = match.group(2).strip(" -–—:.")
    tod = match.group(3)
    prefix = match.group(1).upper()
    where = {"INT": "Interior", "EXT": "Exterior", "INT/EXT": "Interior/Exterior", "I/E": "Interior/Exterior"}.get(prefix, prefix)
    text = f"{where}: {place.title() if place.isupper() else place}"
    if tod:
        text += f", {tod.lower()}"
    return text[:300]


def _guess_location(text: str) -> str:
    m = re.search(r"\b(?:at|in|inside|near|on|across|through)\s+(?:the|a|an)\s+([A-Za-z][A-Za-z\s'-]{2,40}?)(?=[,.;:!?]|\s(?:where|while|as|and|with|under|when)\b|$)", text)
    if m:
        return m.group(1).strip().capitalize()[:120]
    m = LANDSCAPE_WORDS.search(text)
    return m.group(1).capitalize() if m else ""


def _detect_characters(text: str, known_names: list[str]) -> list[str]:
    found: list[str] = []
    lowered = text.lower()
    for name in known_names:
        if name and name.lower() in lowered:
            found.append(name)
    if not known_names:
        # Capitalised words that repeat are probably names (very rough but useful offline).
        candidates = re.findall(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{2,15})\b", text)
        counts: dict[str, int] = {}
        for c in candidates:
            counts[c] = counts.get(c, 0) + 1
        stop = {"The", "And", "But", "Then", "When", "She", "His", "Her", "They", "That", "This", "With", "From", "Into", "Scene", "Camera"}
        for name, count in counts.items():
            if count >= 2 and name not in stop:
                found.append(name)
    return found[:6]


def _title_from_text(text: str, index: int) -> str:
    words = text.strip().split()
    if not words:
        return f"Scene {index + 1}"
    title = " ".join(words[:7])
    return (title[:60] + ("…" if len(words) > 7 else "")).strip()


def _reference_ids_for(names: list[str], ctx: AnalysisContext) -> list[str]:
    ids: list[str] = []
    for name in names:
        for c in ctx.characters:
            if c.get("name", "").lower() == name.lower() and c.get("reference_asset_id"):
                ids.append(str(c["reference_asset_id"]))
    return ids


def _to_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
        if math.isnan(parsed) or parsed <= 0:
            return default
        return parsed
    except (TypeError, ValueError):
        return default


async def analyze_script(script: str, ctx: AnalysisContext, llm: LLMClient | None, *, use_llm: bool | None = None) -> AnalysisResult:
    if llm is not None and use_llm is not False:
        return await LLMSceneAnalyzer(llm).analyze(script, ctx)
    result = HeuristicSceneAnalyzer().analyze(script, ctx)
    if use_llm is True and llm is None:
        result.warnings.insert(0, "No AI provider is configured; used rule-based breakdown. Configure an LLM in Settings for smarter scenes.")
    return result
