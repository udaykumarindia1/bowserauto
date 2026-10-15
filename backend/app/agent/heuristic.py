"""Rule-based planner used when no LLM is configured (or as a fallback).

It understands the common "prompt → generate → wait → download" layout and a handful of
sub-goals that adapters delegate (upload references, enter prompt, start generation,
download). It is intentionally conservative: when unsure it asks for a human instead of
guessing.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.browser.driver import InteractiveElement, PageSnapshot

if TYPE_CHECKING:
    from app.agent.agent import AgentStep

PROMPT_HINT = re.compile(r"prompt|describe|imagine|message|what do you want|idea|type", re.I)
GENERATE_HINT = re.compile(r"^(generate|create|run|render|make|submit|send|go)\b", re.I)
DOWNLOAD_HINT = re.compile(r"download", re.I)
UPLOAD_HINT = re.compile(r"upload|attach|add image|reference|ingredient", re.I)
HUMAN_HINT = re.compile(r"captcha|verify you are human|i'm not a robot|security check|verification code|two-factor|sign in|log in|login", re.I)
BUSY_HINT = re.compile(r"generating|processing|queued|rendering|\b\d{1,3}%", re.I)
DONE_HINT = re.compile(r"complete|ready|finished|done", re.I)
FAIL_HINT = re.compile(r"failed|error|something went wrong", re.I)


class HeuristicPlanner:
    def plan(self, goal: str, inputs: dict[str, Any], snapshot: PageSnapshot, history: list[AgentStep]) -> dict[str, Any]:
        goal_l = goal.lower()
        text = snapshot.text or ""
        done_tools = [h.tool for h in history]
        if HUMAN_HINT.search(text[:4000]) and not any(e.role == "textbox" and PROMPT_HINT.search(e.placeholder + e.name) for e in snapshot.elements):
            return self._needs_human("The page shows a login or verification step that must be completed manually.")
        if any(h.tool == "browser.download" and h.result.ok for h in history):
            return {"tool": "task.complete", "args": {"message": "Output downloaded"}, "reasoning": "Download finished; completing."}
        # Sub-goals delegated by adapters -------------------------------------------------
        if "upload" in goal_l and "download" not in goal_l:
            if any(h.tool == "browser.upload" and h.result.ok for h in history):
                return {"tool": "task.complete", "args": {"message": "Upload done"}, "reasoning": "Reference uploaded."}
            el = self._find(snapshot, lambda e: e.input_type == "file") or self._find(snapshot, lambda e: UPLOAD_HINT.search(e.name or e.text))
            if el:
                return {"tool": "browser.upload", "args": {"index": el.index, "files": inputs.get("files")}, "reasoning": "Upload control detected."}
            return {"tool": "task.fail", "args": {"reason": "No upload control found", "retryable": False}, "reasoning": "No upload control on the page."}
        if ("prompt" in goal_l and "enter" in goal_l) or ("type" in goal_l and "prompt" in goal_l):
            if any(h.tool == "browser.type" and h.result.ok for h in history):
                return {"tool": "task.complete", "args": {"message": "Prompt entered"}, "reasoning": "Prompt entered."}
            el = self._prompt_box(snapshot)
            if el:
                return {"tool": "browser.type", "args": {"index": el.index, "text_to_type": inputs.get("prompt", "")}, "reasoning": "Prompt box detected."}
            return {"tool": "task.fail", "args": {"reason": "No prompt text box found", "retryable": False}, "reasoning": "No prompt field on the page."}
        if "start" in goal_l and "generation" in goal_l and "download" not in goal_l:
            if any(h.tool == "browser.click" and h.result.ok for h in history):
                return {"tool": "task.complete", "args": {"message": "Generation started"}, "reasoning": "Generate clicked."}
            el = self._generate_button(snapshot)
            if el:
                return {"tool": "browser.click", "args": {"index": el.index}, "reasoning": "Generation button detected."}
            return {"tool": "task.fail", "args": {"reason": "No generate button found", "retryable": False}, "reasoning": "No generate button on the page."}
        if "download" in goal_l and "prompt" not in goal_l:
            el = self._download_control(snapshot)
            if el:
                return {"tool": "browser.download", "args": {"index": el.index}, "reasoning": "Download button detected."}
            if BUSY_HINT.search(text):
                return {"tool": "browser.wait", "args": {"seconds": 5}, "reasoning": "Video is still processing."}
            return {"tool": "task.fail", "args": {"reason": "No download control found"}, "reasoning": "No download control visible."}
        # Full workflow ---------------------------------------------------------------------
        files = inputs.get("files") or []
        if files and "browser.upload" not in done_tools:
            el = self._find(snapshot, lambda e: e.input_type == "file") or self._find(snapshot, lambda e: UPLOAD_HINT.search(e.name or e.text))
            if el:
                return {"tool": "browser.upload", "args": {"index": el.index, "files": files}, "reasoning": "Uploading reference files first."}
        if "browser.type" not in done_tools:
            el = self._prompt_box(snapshot)
            if el:
                return {"tool": "browser.type", "args": {"index": el.index, "text_to_type": inputs.get("prompt", "")}, "reasoning": "Prompt box detected; entering the prompt."}
        if "browser.click" not in done_tools:
            el = self._generate_button(snapshot)
            if el:
                return {"tool": "browser.click", "args": {"index": el.index}, "reasoning": "Generation button detected, starting generation."}
        dl = self._download_control(snapshot)
        if dl:
            return {"tool": "browser.download", "args": {"index": dl.index}, "reasoning": "Download button detected."}
        if FAIL_HINT.search(text[-2000:]) and "browser.click" in done_tools:
            return {"tool": "task.fail", "args": {"reason": "The platform reported a failure"}, "reasoning": "Failure message visible."}
        if "browser.click" in done_tools:
            return {"tool": "browser.wait", "args": {"seconds": 5}, "reasoning": "Video is still processing."}
        if snapshot.url in ("", "about:blank") and inputs.get("url"):
            return {"tool": "browser.open", "args": {"url": inputs["url"]}, "reasoning": "Opening the platform."}
        return self._needs_human("The page layout is not recognised. Please take over the browser to continue, or configure an AI provider for the generic agent.")

    # ------------------------------------------------------------------ helpers
    def _needs_human(self, reason: str) -> dict[str, Any]:
        return {"tool": "task.needs_human", "args": {"reason": reason}, "reasoning": "Human assistance is required."}

    @staticmethod
    def _find(snapshot: PageSnapshot, predicate) -> InteractiveElement | None:  # noqa: ANN001
        for el in snapshot.elements:
            try:
                if predicate(el):
                    return el
            except Exception:
                continue
        return None

    def _prompt_box(self, snapshot: PageSnapshot) -> InteractiveElement | None:
        return (
            self._find(snapshot, lambda e: e.role == "textbox" and PROMPT_HINT.search(e.placeholder))
            or self._find(snapshot, lambda e: e.tag == "textarea")
            or self._find(snapshot, lambda e: e.role == "textbox" and e.input_type not in ("email", "password", "search"))
        )

    def _generate_button(self, snapshot: PageSnapshot) -> InteractiveElement | None:
        return self._find(snapshot, lambda e: e.role == "button" and not e.disabled and GENERATE_HINT.search(e.name or e.text))

    def _download_control(self, snapshot: PageSnapshot) -> InteractiveElement | None:
        return self._find(snapshot, lambda e: e.role in ("link", "button") and DOWNLOAD_HINT.search(e.name or e.text))
