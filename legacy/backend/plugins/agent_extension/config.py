from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSkillSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='AGENT_SKILL_', extra='ignore')

    data_dir: Path = Path('data/agent-skills')
    upload_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    extracted_bytes: int = Field(default=30 * 1024 * 1024, gt=0)
    file_count: int = Field(default=200, gt=0)
    skill_md_bytes: int = Field(default=128 * 1024, gt=0)
    context_bytes: int = Field(default=32 * 1024, gt=0)
    max_selected: int = Field(default=5, gt=0, le=20)
    draft_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    cleanup_limit: int = Field(default=100, ge=1, le=1000)
