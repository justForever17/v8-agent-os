from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResearchAcquisitionSchedule:
    """Bound the coupled search/read phase without weakening evidence gates."""

    max_parallel_shards: int = 3
    max_search_providers_per_shard: int = 2
    max_alternate_provider_attempts_per_run: int = 1
    max_read_attempts_per_shard: int = 4
    target_accepted_reads_per_shard: int = 2
    duplicate_cache_wait_seconds: float = 0.05
    min_alternate_provider_budget_seconds: float = 3.0


DEFAULT_RESEARCH_ACQUISITION_SCHEDULE = ResearchAcquisitionSchedule()
