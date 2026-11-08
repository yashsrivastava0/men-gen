"""ReplayProvider: serves recorded cassettes. A missing cassette FAILS; it never goes live."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from mde_contracts.gateway import CallMode
from mde_core.errors import CassetteMissing, ConfigError
from mde_llm_gateway.providers.base import ProviderRequest, ProviderResponse
from mde_llm_gateway.providers.cassette import (
    DEFAULT_CASSETTE_DIR,
    cassette_key,
    cassette_path,
    read_cassette,
    response_from_cassette,
)


class ReplayProvider:
    name = "replay"
    billable = False

    def __init__(self, cassette_dir: str | Path | None = None) -> None:
        self.mode: CallMode = "replay"
        self.cassette_dir = Path(cassette_dir) if cassette_dir is not None else DEFAULT_CASSETTE_DIR

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        key = cassette_key(request)
        path = cassette_path(self.cassette_dir, request.spec.id, request.spec.version, key)
        if not path.is_file():
            raise CassetteMissing(
                f"no cassette for {request.spec.id} v{request.spec.version} "
                f"(key {key[:16]}...) under {self.cassette_dir}. Replay mode never calls the "
                "API: record cassettes with a live batch (lead only) or switch to mock mode.",
                details={"spec_id": request.spec.id, "key": key, "path": str(path)},
            )
        try:
            cassette = read_cassette(path)
        except (ValidationError, ValueError) as exc:
            raise ConfigError(f"cassette {path} is unreadable: {exc}") from exc
        return response_from_cassette(cassette)
