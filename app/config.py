"""Central config. Live API model name and audio settings live here only —
never hardcode them in session/tool logic, per project hard constraints."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    gemini_api_key: str = ""

    live_model_name: str = "gemini-3.8-live"
    live_input_sample_rate_hz: int = 16000
    live_output_sample_rate_hz: int = 24000
    live_input_mime_type: str = "audio/pcm;rate=16000"

    # Plain (non-Live) text model: used by the Phase 3 text-tier eval for
    # the agent itself, the simulated caller, and the LLM judge. The Live
    # model above only supports AUDIO output (confirmed against the real
    # API), so text-tier eval can't reuse it.
    text_model_name: str = "gemini-3.8-flash"

    database_url: str = "postgresql://clinic:clinic@localhost:5432/clinic"

    # Set on Cloud Run only: when present, app/db.py connects via the Unix
    # socket Cloud Run mounts for Cloud SQL (/cloudsql/<this value>) instead
    # of the TCP DATABASE_URL above -- Cloud SQL isn't reachable by normal
    # hostname from Cloud Run, only through that socket or a VPC connector.
    cloud_sql_connection_name: str = ""
    db_user: str = "clinic"
    db_password: str = ""
    db_name: str = "clinic"

    require_confirmation: bool = True

    # Vertex AI mode: used instead of an AI Studio API key when the key's
    # project has billing attached (switched to this after an AI Studio
    # Prepay balance got charged unexpectedly). Auth is via
    # `gcloud auth application-default login`, not an API key.
    # The two locations differ because the SAME models are only reachable
    # at different Vertex regions depending on call type -- confirmed by
    # testing directly: the Live model 404s at "global" and the text model
    # 404s at "us-central1".
    use_vertex: bool = False
    vertex_project: str = ""
    vertex_location_live: str = "us-central1"
    vertex_location_text: str = "global"

    # Lightweight gate on the admin dashboard (HTTP Basic, username "admin").
    # Not a real auth system -- see docs/privacy.md's "Known gaps". Empty
    # string disables the dashboard entirely rather than leaving it open.
    admin_password: str = ""


settings = Settings()
