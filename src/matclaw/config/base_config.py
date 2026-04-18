from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, HttpUrl
from pydantic_settings import BaseSettings


class LoggingSettings(BaseModel):
    level: str = Field(default="INFO", description="Root log level.")
    use_json: bool = Field(default=True, description="Enable JSON structured logging.")


class MatlabSettings(BaseModel):
    enabled: bool = Field(default=True, description="Whether to start MATLAB engine.")
    startup_timeout_seconds: int = Field(default=60, description="MATLAB startup timeout.")
    session_name: Optional[str] = Field(default=None, description="Optional named MATLAB session.")
    show_figure_windows: bool = Field(
        default=False,
        description=(
            "If true, MATLAB figures are shown on screen (DefaultFigureVisible on; new engine starts "
            "without -nodesktop). If false (default), figures are hidden and only PNG/GIF capture runs — "
            "recommended for API servers. matlab -batch runs stay non-interactive."
        ),
    )


class MemorySettings(BaseModel):
    backend: str = Field(default="inmemory", description="Memory backend identifier.")
    persistence_path: str = Field(
        default=".matclaw_memory.json",
        description="Path for local JSON persistence (for simple backend).",
    )


class DaemonSettings(BaseModel):
    heartbeat_interval_seconds: float = Field(default=15.0, description="Heartbeat tick interval.")


class DebugAgentSettings(BaseModel):
    """Autonomous debug loop: when to apply fixes and where to find .m files."""

    matlab_root: str = Field(default="matlab", description="Root directory for MATLAB project .m files.")
    apply_fix_with_bak: bool = Field(
        default=True,
        description="If a fix succeeds, write source.m.bak and apply fix to source file.",
    )
    max_fix_attempts: int = Field(default=2, description="Max number of fix attempts per failure.")
    debug_llm_provider: str = Field(default="anthropic", description="LLM provider for debug reasoning.")
    debug_llm_model: str = Field(default="claude-3-5-sonnet-20241022", description="Model for debug reasoning.")
    debug_llm_api_key: Optional[str] = Field(default=None, description="Optional API key override for debug LLM.")
    debug_max_rounds: int = Field(default=3, description="Max LLM refinement rounds after heuristic attempt fails.")
    debug_sandbox: bool = Field(default=True, description="Test fixes in sandbox before applying to source.")


class FileDoctorSettings(BaseModel):
    """Proactive file analysis and fix pipeline."""

    max_file_size_bytes: int = Field(default=2_097_152, description="Max .m file size to read (2 MB).")
    allowed_suffixes: list[str] = Field(
        default_factory=lambda: [".m", ".slx", ".mat", ".csv", ".mlx"],
        description="File suffixes the file doctor is allowed to read.",
    )
    workspace_roots: list[str] = Field(
        default_factory=lambda: ["matlab", "data_in", "."],
        description="Directories the file doctor may access (relative to project root).",
    )


class LongTermMemorySettings(BaseModel):
    """Long-term knowledge retention: consolidation, archival, and cleanup."""

    enabled: bool = Field(default=True, description="Whether long-term memory features are active.")
    archive_after_days: int = Field(
        default=180,
        description="Move raw experiments older than this to archive DB.",
    )
    prune_artifacts_after_days: int = Field(
        default=90,
        description="Remove raw ChromaDB artifacts older than this if consolidated.",
    )
    auto_consolidate_every_n: int = Field(
        default=20,
        description="Auto-trigger LLM consolidation after this many experiments per skill.",
    )
    consolidation_llm_provider: str = Field(
        default="",
        description="LLM provider for consolidation (defaults to llm.provider if empty).",
    )
    consolidation_llm_model: str = Field(
        default="",
        description="LLM model for consolidation (defaults to llm.model if empty).",
    )


class WatchdogSettings(BaseModel):
    """File management: monitor folder for new .mat / .csv files."""

    enabled: bool = Field(default=True, description="Whether the file watcher is active.")
    watch_path: str = Field(
        default="data",
        description="Directory to watch for new .mat and .csv files.",
    )
    patterns: list[str] = Field(
        default_factory=lambda: ["*.mat", "*.csv"],
        description="Glob patterns for watched files.",
    )


class SentrySettings(BaseModel):
    """Proactive sentry: monitor /data_in and trigger RPI on new files."""

    enabled: bool = Field(default=False, description="Whether the sentry watchdog is active.")
    data_in_path: str = Field(default="data_in", description="Directory to watch for incoming files.")


class LabJournalSettings(BaseModel):
    """Auto-generated lab log (LAB_JOURNAL.md)."""

    enabled: bool = Field(default=True, description="Whether to append to lab journal on each run.")
    path: str = Field(default="LAB_JOURNAL.md", description="Path to the lab journal file.")


class TelegramSettings(BaseModel):
    """Telegram gateway for alerts and remote commands."""

    enabled: bool = Field(default=False, description="Whether the Telegram listener and alerts are active.")
    bot_token: Optional[str] = Field(default=None, description="Bot token (or set TELEGRAM_BOT_TOKEN / TELEGRAM_TOKEN).")
    chat_id: Optional[str] = Field(default=None, description="Optional default chat ID for alerts (or set TELEGRAM_CHAT_ID).")


class HITLSettings(BaseModel):
    """Human-in-the-loop: pause long-running runs and ask for confirmation."""

    enabled: bool = Field(default=True, description="Whether to require approval for long runs.")
    threshold_seconds: float = Field(default=600.0, description="Estimate above this triggers 'Proceed? [Yes/No]'.")


_VALID_LLM_PROVIDERS = frozenset({"nvidia", "google", "anthropic", "openai-compatible"})

class LLMSettings(BaseModel):
    """LLM for NL routing and vision: Anthropic, Google (Gemini), or NVIDIA (Nemotron)."""

    provider: str = Field(
        default="nvidia",
        description="One of: anthropic, google, nvidia, openai-compatible.",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="API key (or set ANTHROPIC_API_KEY / GOOGLE_API_KEY / NVIDIA_API_KEY).",
    )
    model: str = Field(
        default="nvidia/nvidia-nemotron-nano-9b-v2",
        description="Model name (e.g. nvidia/nvidia-nemotron-nano-9b-v2, gemini-2.0-flash).",
    )
    base_url: Optional[str] = Field(
        default=None,
        description="Base URL for openai-compatible providers (e.g. http://localhost:11434/v1).",
    )


class VisionSettings(BaseModel):
    """Vision analyst: analyze .png plots via Anthropic, Google, or NVIDIA API."""

    enabled: bool = Field(default=True, description="Whether to analyze plots before sending.")
    provider: str = Field(default="google", description="One of: anthropic, google, nvidia.")
    api_key: Optional[str] = Field(default=None, description="API key (or set ANTHROPIC_API_KEY / GOOGLE_API_KEY / NVIDIA_API_KEY).")
    model: str = Field(default="gemini-2.0-flash", description="Model name for vision (NVIDIA VLMs use different model IDs).")


class SyncSettings(BaseModel):
    enabled: bool = Field(default=False)
    mode: str = Field(default="rsync")
    rsync_source: str = Field(default="")
    rsync_dest: str = Field(default="data_in")
    cloud_path: str = Field(default="")


class AgenticSettings(BaseModel):
    """Autonomous agentic loop — iterative tool-use with observe-think-act cycle."""

    enabled: bool = Field(default=True, description="Whether agentic mode is available.")
    max_iterations: int = Field(default=10, description="Max tool-call iterations per request.")
    max_tokens_per_step: int = Field(default=4096, description="Max tokens per LLM call in the loop.")


class ProductionSettings(BaseModel):
    """
    Production / SLO-oriented limits and memory injection for agentic runs.
    Env: MATCLAW_PRODUCTION__* (nested).
    """

    memory_inject_enabled: bool = Field(
        default=True,
        description="Inject Chroma query snippets into agentic user context when long-term memory is enabled.",
    )
    memory_n_results: int = Field(default=5, ge=1, le=20, description="Chunks to retrieve for memory injection.")
    memory_max_chars: int = Field(default=4000, ge=500, le=32000, description="Max chars of memory context to prepend.")
    store_agentic_episodes: bool = Field(
        default=True,
        description="Store a compact artifact after each agentic run for future retrieval.",
    )
    max_agentic_wall_seconds: float = Field(
        default=600.0,
        ge=30.0,
        description="Hard wall-clock budget (seconds) for one agentic run; the loop stops when exceeded.",
    )
    agentic_max_concurrent: int = Field(
        default=4,
        ge=1,
        le=128,
        description="Max concurrent agentic streams per process (fairness / overload protection).",
    )
    soft_cost_cap_usd_per_task: float = Field(
        default=0.0,
        ge=0.0,
        description="Soft ceiling (USD) on estimated spend per task; 0 disables. Requires token usage from the provider and non-zero rates below.",
    )
    usd_per_1k_prompt_tokens: float = Field(
        default=0.0,
        ge=0.0,
        description="Your fully-loaded $/1k prompt tokens for soft-cap math (0 = still report tokens, no USD estimate).",
    )
    usd_per_1k_completion_tokens: float = Field(
        default=0.0,
        ge=0.0,
        description="$/1k completion tokens for soft-cap math (0 = still report tokens, no USD estimate).",
    )


class MatClawSettings(BaseSettings):
    """
    Typed application configuration, loaded from environment variables where present.
    """

    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    matlab: MatlabSettings = Field(default_factory=MatlabSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    daemon: DaemonSettings = Field(default_factory=DaemonSettings)
    debug: DebugAgentSettings = Field(default_factory=DebugAgentSettings)
    watchdog: WatchdogSettings = Field(default_factory=WatchdogSettings)
    sentry: SentrySettings = Field(default_factory=SentrySettings)
    lab_journal: LabJournalSettings = Field(default_factory=LabJournalSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    hitl: HITLSettings = Field(default_factory=HITLSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    vision: VisionSettings = Field(default_factory=VisionSettings)
    file_doctor: FileDoctorSettings = Field(default_factory=FileDoctorSettings)
    long_term_memory: LongTermMemorySettings = Field(default_factory=LongTermMemorySettings)
    sync: SyncSettings = Field(default_factory=SyncSettings)
    agentic: AgenticSettings = Field(default_factory=AgenticSettings)
    production: ProductionSettings = Field(default_factory=ProductionSettings)

    class Config:
        env_prefix = "MATCLAW_"
        env_nested_delimiter = "__"
        env_file = ".env"
        extra = "ignore"

