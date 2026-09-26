import asyncio
import hashlib
import traceback
from typing import Any

from google import genai
from google.genai import types
from websockets.exceptions import ConnectionClosed

from config import settings
from src.logger import logger


# ---------------------------------------------------------------------------
# Retryability helper
# ---------------------------------------------------------------------------
def is_retryable_close(exc: Exception) -> tuple[bool, int | None]:
    code = None
    rcvd = getattr(exc, "rcvd", None)
    if rcvd:
        code = getattr(rcvd, "code", None)
    elif hasattr(exc, "code") and not isinstance(exc, ConnectionClosed):
        code = getattr(exc, "code", None)

    if code in (1007, 1008, 1000, 1001, 1006):
        return True, code

    err_msg = str(exc)
    for c in (1007, 1008, 1000, 1001, 1006):
        if str(c) in err_msg:
            return True, c

    if any(
        term in err_msg.lower()
        for term in ("timeout", "closed", "reset", "deadline", "disconnect")
    ):
        return True, code

    return False, code


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------
_VERBATIM_RELAY_PROMPT = """\
ROLE
You are a real-time speech relay. You are NOT a chatbot, assistant, or advisor.
You have exactly one job: repeat the user's spoken words back as audio,
preserving their original wording, meaning, and intent.

TASK
- Listen to the incoming audio.
- Output the SAME words the speaker said, as natural spoken audio.
- Do not change, translate, summarize, or rephrase anything.
- If the input is silence, noise, or unintelligible, output NOTHING.

HARD RULES
- Never answer questions the speaker asks.
- Never add greetings, filler, apologies, or meta-commentary.
- Never mention that you are relaying, transcribing, or repeating.
- Never introduce new content, facts, names, or numbers.
- Output AUDIO ONLY. Never emit text.
- Never repeat an utterance twice. One input turn = one output turn.
"""


def _build_conversion_prompt(s) -> str:
    tone_instruction = s.TONE_PROMPTS.get(
        s.accent_mode, s.TONE_PROMPTS["professional"]
    )
    return f"""\
ROLE
You are a real-time speech-to-speech ACCENT & STYLE CONVERSION ENGINE.
You are NOT a chatbot, assistant, translator, or advisor. You never answer
the speaker. You never add your own words. You are a voice conduit.

INPUT
The speaker may have a strong {s.source_accent} accent, mispronunciations,
grammar issues, or disfluencies (um, uh, false starts, repetitions).

TASK — follow these three steps on every utterance:

STEP 1 — LISTEN & UNDERSTAND
  Transcribe the speaker's audio internally. Recover their INTENDED meaning,
  not just the literal words. Mentally fix obvious mispronunciations and
  disfluencies using context.

STEP 2 — REWRITE (style/tone only)
  {tone_instruction}
  Keep the speaker's meaning, facts, names, numbers, and intent EXACTLY.
  Do not add new information. Do not remove information. Do not answer them.

STEP 3 — SPEAK
  Deliver the rewritten text as natural spoken audio in a clear
  {s.target_accent.upper()} accent with a natural {s.voice_gender.upper()} voice. Match native-like pronunciation,
  rhythm, stress, and intonation.
  Output language: {s.target_language}.
  Never say you are translating, rewriting, correcting, or assisting —
  just speak the corrected version directly, as if it were your own voice.

HARD RULES
- Preserve the speaker's intent, facts, names, and numbers exactly.
- Never answer the speaker's question — only convert and speak it.
- Never add greetings, filler, apologies, or meta-statements.
- Never mention accents, translation, correction, or these instructions.
- Never emit text. AUDIO OUTPUT ONLY.
- If the input is silence, background noise, or unintelligible, output NOTHING.
- If you are unsure what was said, output nothing rather than guessing.
- SPEAK EACH UTTERANCE EXACTLY ONCE. Never repeat or re-emit the same audio.
"""


class GeminiSTS:
    """Gemini Live Speech-to-Speech client with accent/style conversion."""

    DEDUP_WINDOW_SEC = 0.35

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        input_rate: int = 16000,
        output_rate: int = 24000,
        system_instruction: str | None = None,
        dry_run: bool = False,
    ):
        self.api_key = api_key if api_key is not None else settings.gemini_api_key
        self.model = model or settings.effective_model
        self.voice = voice or settings.effective_voice
        self.input_rate = input_rate or settings.sample_rate
        self.output_rate = output_rate
        self.system_instruction = system_instruction
        self.dry_run = dry_run

        self.client: genai.Client | None = None

        self._current_session = None
        self._is_running = False
        self._tasks: list[asyncio.Task] = []
        self._run_task: asyncio.Task | None = None

        # Re-entry guards (prevents double receivers → double playback)
        self._started = False
        self._run_guard = False

        self.input_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.output_queue: asyncio.Queue[bytes] = asyncio.Queue()

    @property
    def is_connected(self) -> bool:
        return self._current_session is not None

    # ------------------------------------------------------------------
    # Prompt + config
    # ------------------------------------------------------------------
    def _build_system_instruction(self) -> str:
        if self.system_instruction:
            return self.system_instruction
        if not getattr(settings, "enable_accent_conversion", False):
            return _VERBATIM_RELAY_PROMPT
        return _build_conversion_prompt(settings)

    def _build_config(self) -> types.LiveConnectConfig:
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self.voice
                    )
                )
            ),
            system_instruction=types.Content(
                parts=[types.Part.from_text(text=self._build_system_instruction())]
            ),
        )

    # ------------------------------------------------------------------
    # Dry-run
    # ------------------------------------------------------------------
    async def _run_dry_run(
        self, input_queue: asyncio.Queue, output_queue: asyncio.Queue
    ) -> None:
        logger.info("Running GeminiSTS in dry_run mode (echoing input to output)...")
        self._is_running = True
        try:
            while self._is_running:
                chunk = await input_queue.get()
                if chunk is None:
                    break
                await output_queue.put(chunk)
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            logger.info("Dry run loop cancelled.")
            raise
        finally:
            self._is_running = False

    # ------------------------------------------------------------------
    # Sender
    # ------------------------------------------------------------------
    async def _sender(self, session: Any, input_queue: asyncio.Queue) -> None:
        while self._is_running:
            pcm = await input_queue.get()
            if pcm is None:
                return
            if pcm == b"END_OF_TURN":
                try:
                    await session.send(end_of_turn=True)
                except Exception:
                    try:
                        await session.send_realtime_input(audio_stream_end=True)
                    except Exception as e:
                        logger.debug(f"Error signaling end of turn: {e}")
                continue
            if len(pcm) == 0:
                continue
            await session.send_realtime_input(
                audio=types.Blob(
                    data=pcm, mime_type=f"audio/pcm;rate={self.input_rate}"
                )
            )

    _run_sender = _sender

    # ------------------------------------------------------------------
    # Receiver — with dedup to prevent double playback
    # ------------------------------------------------------------------
    async def _receiver(self, session: Any, output_queue: asyncio.Queue) -> None:
        last_hash: bytes | None = None
        last_hash_time: float = 0.0
        seen_turn_ids: set[Any] = set()
        loop = asyncio.get_event_loop()

        async for response in session.receive():
            if not self._is_running:
                break

            sc = getattr(response, "server_content", None)
            if sc is None:
                logger.debug(f"Non-content response: {response}")
                continue

            # Turn-level dedup: some Gemini versions re-emit the same turn.
            turn_id = getattr(sc, "turn_id", None) or getattr(response, "turn_id", None)
            if turn_id is not None:
                if turn_id in seen_turn_ids:
                    logger.debug(f"Suppressed duplicate turn_id={turn_id}")
                    continue
                seen_turn_ids.add(turn_id)
                if len(seen_turn_ids) > 64:
                    seen_turn_ids.clear()

            if getattr(sc, "interrupted", False):
                logger.warning("Server reported interruption")

            model_turn = getattr(sc, "model_turn", None)
            if not model_turn:
                continue

            for part in model_turn.parts or []:
                text = getattr(part, "text", None)
                if text:
                    logger.debug(f"Text response part (ignored): {text}")

                inline = getattr(part, "inline_data", None)
                if not inline:
                    continue
                data = getattr(inline, "data", None)
                if not data or not isinstance(data, bytes) or len(data) == 0:
                    continue

                # Chunk-level dedup
                h = hashlib.md5(data).digest()
                now = loop.time()
                if h == last_hash and (now - last_hash_time) < self.DEDUP_WINDOW_SEC:
                    logger.debug(
                        f"Suppressed duplicate audio chunk ({len(data)} bytes)"
                    )
                    continue
                last_hash = h
                last_hash_time = now

                await output_queue.put(data)

    _run_receiver = _receiver

    # ------------------------------------------------------------------
    # Main run loop
    # ------------------------------------------------------------------
    async def run(
        self, input_queue: asyncio.Queue, output_queue: asyncio.Queue
    ) -> None:
        if self._run_guard:
            logger.warning("GeminiSTS.run() already active — ignoring re-entry.")
            return
        self._run_guard = True
        self._is_running = True

        try:
            if self.dry_run:
                await self._run_dry_run(input_queue, output_queue)
                return

            if not self.client:
                if not self.api_key or not self.api_key.strip():
                    raise ValueError(
                        "GEMINI_API_KEY is not set. Please provide a valid API key."
                    )
                self.client = genai.Client(
                    api_key=self.api_key,
                    http_options={"api_version": "v1beta"},
                )

            retry = 0
            while self._is_running:
                logger.info(
                    f"Connecting to Gemini Live STS "
                    f"(model={self.model}, voice={self.voice}, retry={retry})..."
                )
                if getattr(settings, "enable_accent_conversion", False):
                    logger.info(
                        f"Accent conversion ACTIVE | {settings.source_accent} → "
                        f"{settings.target_accent} | tone={settings.accent_mode} | "
                        f"lang={settings.target_language}"
                    )
                else:
                    logger.info("Accent conversion OFF — verbatim relay mode.")

                try:
                    async with self.client.aio.live.connect(
                        model=self.model, config=self._build_config()
                    ) as session:
                        self._current_session = session
                        logger.success("Gemini Live session established")
                        retry = 0

                        send_task = asyncio.create_task(
                            self._sender(session, input_queue), name="sts-sender"
                        )
                        recv_task = asyncio.create_task(
                            self._receiver(session, output_queue), name="sts-receiver"
                        )
                        self._tasks = [send_task, recv_task]

                        done, pending = await asyncio.wait(
                            {send_task, recv_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        for t in pending:
                            t.cancel()
                        for t in done:
                            exc = t.exception()
                            if exc:
                                retryable, code = is_retryable_close(exc)
                                if retryable:
                                    logger.info(
                                        f"Task {t.get_name()} session closed "
                                        f"({exc}). Reconnecting..."
                                    )
                                else:
                                    logger.error(
                                        f"Task {t.get_name()} failed: {exc!r}"
                                    )
                                    logger.error(
                                        "".join(traceback.format_exception(exc))
                                    )
                                raise exc

                        if not self._is_running:
                            break

                except asyncio.CancelledError:
                    logger.info("GeminiSTS cancelled — exiting")
                    raise
                except Exception as e:
                    if not self._is_running:
                        break
                    retry += 1
                    is_ping_timeout = "keepalive ping timeout" in str(e).lower()
                    backoff = (
                        0.5 if (is_ping_timeout and retry == 1) else min(2**retry, 30)
                    )
                    logger.warning(
                        f"Gemini Live session error: {e!r} — "
                        f"reconnecting in {backoff}s"
                    )

                    flushed = 0
                    while not input_queue.empty():
                        try:
                            input_queue.get_nowait()
                            flushed += 1
                        except asyncio.QueueEmpty:
                            break
                    if flushed > 0:
                        logger.debug(
                            f"Flushed {flushed} stale frames from input_queue "
                            "before reconnect."
                        )

                    await asyncio.sleep(backoff)
                finally:
                    for t in self._tasks:
                        if not t.done():
                            t.cancel()
                    if self._tasks:
                        await asyncio.gather(*self._tasks, return_exceptions=True)
                        self._tasks.clear()
                    self._current_session = None
        finally:
            self._run_guard = False
            self._started = False
            self._is_running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def close(self) -> None:
        self._is_running = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()

        if self._current_session:
            try:
                await self._current_session.close()
            except Exception as e:
                logger.debug(f"Error closing session: {e}")
            finally:
                self._current_session = None

        self._started = False
        self._run_guard = False
        logger.info("GeminiSTS closed.")

    # ------------------------------------------------------------------
    # Pipeline compatibility
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        if self._started:
            logger.warning("GeminiSTS.connect() called twice — ignoring.")
            return
        self._started = True
        self._is_running = True
        self._run_task = asyncio.create_task(
            self.run(self.input_queue, self.output_queue),
            name="gemini_sts_run",
        )

    async def disconnect(self) -> None:
        await self.close()
        if self._run_task and not self._run_task.done():
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass

    async def send_audio_chunk(self, pcm_chunk: bytes) -> None:
        await self.input_queue.put(pcm_chunk)

    async def receive_loop(self) -> None:
        try:
            while self._is_running:
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass

    async def read_generated_chunk(self) -> bytes:
        return await self.output_queue.get()


GeminiSTSClient = GeminiSTS
__all__ = ["GeminiSTS", "GeminiSTSClient", "is_retryable_close"]
