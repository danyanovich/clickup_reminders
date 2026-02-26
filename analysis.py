from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Optional

import requests

try:  # pragma: no cover - optional dependency handling
    from openai import OpenAI  # type: ignore
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore

import openai  # type: ignore

try:  # pragma: no cover - support both package and script execution
    from .core.models import AnalysisResult
except ImportError:  # pragma: no cover - script mode fallback
    from core.models import AnalysisResult  # type: ignore


class VoiceAnalyzer:
    """
    Wrapper around OpenAI APIs used to transcribe and analyse call recordings.

    The class gracefully handles environments where the new `openai` package is
    not available by falling back to the legacy interface.
    """

    SYSTEM_PROMPT = (
        "Ты анализируешь ответы на напоминания о задачах. Определи, выполнена ли задача, "
        "и верни JSON с полями: completed (true/false), confidence (0-1), summary "
        "(краткое описание ответа)."
    )

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = OpenAI(api_key=api_key) if OpenAI else None
        openai.api_key = api_key  # legacy fallback

    def analyze_recording(self, recording_url: str) -> AnalysisResult:
        audio_path: Optional[Path] = None
        try:
            audio_path = self._download_recording(recording_url)
            transcript_text = self._transcribe(audio_path)
            analysis_payload = self._analyse_text(transcript_text)
            result = AnalysisResult(
                completed=bool(analysis_payload.get("completed")),
                confidence=float(analysis_payload.get("confidence", 0)),
                summary=str(analysis_payload.get("summary", "")),
                transcript=transcript_text,
            )
            return result
        except Exception as exc:  # pragma: no cover - external services
            return AnalysisResult(
                completed=False,
                confidence=0.0,
                summary=f"Error analyzing response: {exc}",
                transcript="",
            )
        finally:
            if audio_path:
                try:
                    audio_path.unlink(missing_ok=True)
                except FileNotFoundError:  # pragma: no cover - defensive cleanup
                    pass
                except OSError:  # pragma: no cover - best-effort cleanup
                    pass

    def _download_recording(self, recording_url: str) -> Path:
        response = requests.get(recording_url, timeout=30)
        response.raise_for_status()
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        temp_file.write(response.content)
        temp_file.flush()
        temp_file.close()
        return Path(temp_file.name)

    def _transcribe(self, audio_path: Path) -> str:
        with audio_path.open("rb") as audio_file:
            if self._client:
                transcript = self._client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="ru",
                )
                return transcript.text  # type: ignore[attr-defined]

            try:
                transcript = openai.audio.transcriptions.create(  # type: ignore[attr-defined]
                    model="whisper-1",
                    file=audio_file,
                    language="ru",
                )
                return transcript.text  # type: ignore[attr-defined]
            except AttributeError:
                transcript = openai.Audio.transcribe(  # type: ignore[attr-defined]
                    model="whisper-1",
                    file=audio_file,
                    language="ru",
                )
                return transcript["text"]

    def _analyse_text(self, transcript_text: str) -> dict:
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": f"Ответ пользователя: {transcript_text}"},
        ]

        if self._client:
            analysis = self._client.chat.completions.create(
                model="gpt-4",
                messages=messages,
                response_format={"type": "json_object"},
            )
            message_content = analysis.choices[0].message.content
        else:
            try:
                analysis = openai.chat.completions.create(  # type: ignore[attr-defined]
                    model="gpt-4",
                    messages=messages,
                    response_format={"type": "json_object"},
                )
                message_content = analysis.choices[0].message.content
            except AttributeError:
                analysis = openai.ChatCompletion.create(model="gpt-4", messages=messages)
                message_content = analysis["choices"][0]["message"]["content"]

        if not message_content:
            return {}
        return json.loads(message_content)
