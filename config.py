from typing import ClassVar, Optional, Union

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Gemini API
    # ------------------------------------------------------------------
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    gemini_model: str = Field(
        default="gemini-2.0-flash-exp", alias="GEMINI_MODEL"
    )
    gemini_live_model: Optional[str] = Field(
        default=None, alias="GEMINI_LIVE_MODEL"
    )
    gemini_voice: Optional[str] = Field(
        default=None,
        alias="GEMINI_VOICE",
        description="Explicit Gemini voice override (Puck, Charon, Aoede, Fenrir, Kore)",
    )

    # ------------------------------------------------------------------
    # Audio devices
    # ------------------------------------------------------------------
    input_device: Union[int, str] = Field(default="default", alias="INPUT_DEVICE")
    output_device: Union[int, str] = Field(default="default", alias="OUTPUT_DEVICE")
    virtual_mic_device: Union[int, str] = Field(
        default="CABLE Input", alias="VIRTUAL_MIC_DEVICE"
    )

    # ------------------------------------------------------------------
    # Audio stream config
    # ------------------------------------------------------------------
    sample_rate: int = Field(default=16000, alias="SAMPLE_RATE")
    output_sample_rate: int = Field(
        default=24000,
        alias="OUTPUT_SAMPLE_RATE",
        description="Gemini Live output PCM rate (do not change unless you resample).",
    )
    virtual_mic_sample_rate: int = Field(
        default=48000,
        alias="VIRTUAL_MIC_SAMPLE_RATE",
        description="Virtual mic rate — resample Gemini output 24k → 48k before writing.",
    )
    channels: int = Field(default=1, alias="CHANNELS")
    chunk_size: int = Field(default=1024, alias="CHUNK_SIZE")

    # ------------------------------------------------------------------
    # Noise gate / VAD
    # ------------------------------------------------------------------
    enable_noise_gate: bool = Field(default=True, alias="ENABLE_NOISE_GATE")
    noise_gate_threshold_db: float = Field(
        default=10.0, alias="NOISE_GATE_THRESHOLD_DB"
    )
    noise_gate_hangover_ms: float = Field(
        default=300.0, alias="NOISE_GATE_HANGOVER_MS"
    )

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ------------------------------------------------------------------
    # Accent / tone conversion
    # ------------------------------------------------------------------
    enable_accent_conversion: bool = Field(
        default=True, alias="ENABLE_ACCENT_CONVERSION"
    )
    accent_mode: str = Field(default="professional", alias="ACCENT_MODE")
    target_accent: str = Field(default="american", alias="TARGET_ACCENT")
    source_accent: str = Field(default="indian", alias="SOURCE_ACCENT")
    target_language: str = Field(default="english", alias="TARGET_LANGUAGE")
    voice_gender: str = Field(
        default="male",
        alias="VOICE_GENDER",
        description="Target voice gender/type: male | female (default: male)",
    )
    voice_type: Optional[str] = Field(
        default=None,
        alias="VOICE_TYPE",
        description="Alias for VOICE_GENDER (male | female)",
    )

    # ------------------------------------------------------------------
    # Class-level lookup tables (must be ClassVar so Pydantic ignores them)
    # ------------------------------------------------------------------
    ACCENT_GENDER_VOICE_MAP: ClassVar[dict[str, dict[str, str]]] = {
        "american": {"male": "Puck", "female": "Aoede"},
        "british": {"male": "Charon", "female": "Aoede"},
        "australian": {"male": "Fenrir", "female": "Kore"},
        "indian": {"male": "Puck", "female": "Kore"},
        "neutral": {"male": "Puck", "female": "Aoede"},
    }

    DEFAULT_GENDER_VOICES: ClassVar[dict[str, str]] = {
        "male": "Puck",
        "female": "Aoede",
    }

    # Backwards-compatibility alias
    ACCENT_VOICE_MAP: ClassVar[dict[str, str]] = {
        "american": "Puck",
        "british": "Charon",
        "australian": "Fenrir",
        "indian": "Kore",
        "neutral": "Aoede",
    }

    TONE_PROMPTS: ClassVar[dict[str, str]] = {
        "professional": (
            "Rewrite the user's speech in clear, grammatically correct, "
            "professional English. Preserve the original meaning exactly. "
            "Remove filler words (um, uh, like, you know). Use complete "
            "sentences. Do not add new information."
        ),
        "friendly": (
            "Rewrite the user's speech in warm, friendly, conversational "
            "English with correct grammar. Keep it natural and approachable. "
            "Remove filler words. Preserve meaning exactly."
        ),
        "casual": (
            "Rewrite the user's speech in relaxed, casual English. Fix "
            "grammar. Remove fillers. Keep it short and natural. Preserve "
            "meaning."
        ),
        "formal": (
            "Rewrite the user's speech in formal, business-appropriate "
            "English. Correct all grammar. Remove fillers and repetition. "
            "Preserve meaning precisely."
        ),
        "empathetic": (
            "Rewrite the user's speech in calm, empathetic, supportive "
            "English with correct grammar. Remove fillers. Preserve meaning "
            "and emotional tone."
        ),
    }

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("sample_rate", "output_sample_rate", "virtual_mic_sample_rate")
    @classmethod
    def _positive_sample_rate(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("sample_rate must be positive")
        return v

    @field_validator("accent_mode")
    @classmethod
    def _validate_accent_mode(cls, v: str) -> str:
        allowed = {"professional", "friendly", "casual", "formal", "empathetic"}
        v = v.strip().lower()
        if v not in allowed:
            raise ValueError(f"accent_mode must be one of {sorted(allowed)}")
        return v

    @field_validator("target_accent")
    @classmethod
    def _validate_target_accent(cls, v: str) -> str:
        allowed = {"american", "british", "australian", "indian", "neutral"}
        v = v.strip().lower()
        if v not in allowed:
            raise ValueError(f"target_accent must be one of {sorted(allowed)}")
        return v

    @field_validator("target_language", "source_accent")
    @classmethod
    def _clean_short_string(cls, v: str) -> str:
        v = v.strip().lower()
        if not v or len(v) > 32:
            raise ValueError(f"invalid value: {v!r}")
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"invalid characters in {v!r}")
        return v

    @field_validator("noise_gate_threshold_db", "noise_gate_hangover_ms")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("must be >= 0")
        return v

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        v = v.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return v

    @field_validator("voice_gender")
    @classmethod
    def _validate_voice_gender(cls, v: str) -> str:
        allowed = {"male", "female"}
        v = v.strip().lower()
        if v not in allowed:
            raise ValueError(f"voice_gender must be one of {sorted(allowed)}")
        return v

    @model_validator(mode="after")
    def _sync_voice_settings(self) -> "Settings":
        if self.voice_type and self.voice_type.strip().lower() in ("male", "female"):
            self.voice_gender = self.voice_type.strip().lower()
        return self

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------
    @property
    def effective_model(self) -> str:
        return self.gemini_live_model or self.gemini_model

    @property
    def effective_voice(self) -> str:
        # If an explicit voice override is set, check if it conflicts with an explicit gender choice
        if self.gemini_voice and self.gemini_voice.strip():
            v = self.gemini_voice.strip()
            # If user selected female but voice is one of the default male voices, respect female
            if self.voice_gender == "female" and v in ("Puck", "Charon", "Fenrir"):
                pass
            # If user selected male but voice is one of the default female voices, respect male
            elif self.voice_gender == "male" and v in ("Aoede", "Kore"):
                pass
            else:
                return v

        if self.enable_accent_conversion:
            gender_map = self.ACCENT_GENDER_VOICE_MAP.get(
                self.target_accent,
                self.ACCENT_GENDER_VOICE_MAP.get("neutral", {}),
            )
            mapped = gender_map.get(self.voice_gender)
            if mapped:
                return mapped

        return self.DEFAULT_GENDER_VOICES.get(self.voice_gender, "Puck")

    @staticmethod
    def _resolve_device_id(
        device: Union[int, str, None],
    ) -> Union[int, str, None]:
        if device is None:
            return None
        if isinstance(device, int):
            return device
        device_str = str(device).strip()
        if device_str.lower() in ("default", ""):
            return None
        if device_str.isdigit():
            return int(device_str)
        return device_str

    @property
    def resolved_input_device(self) -> Union[int, str, None]:
        return self._resolve_device_id(self.input_device)

    @property
    def resolved_output_device(self) -> Union[int, str, None]:
        return self._resolve_device_id(self.output_device)

    @property
    def resolved_virtual_mic_device(self) -> Union[int, str, None]:
        return self._resolve_device_id(self.virtual_mic_device)


settings = Settings()
__all__ = ["Settings", "settings"]
