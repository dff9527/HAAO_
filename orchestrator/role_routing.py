from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.db.sqlite import SettingsRepository


RoleRouteValue = str | list[str]


DEFAULT_ROLE_ROUTING: dict[str, RoleRouteValue] = {
    "tech_lead": "claude-tech-lead",
    "dev_team": "qwen3-coder-next",
    "gatekeeper": "gemma-4-26b-a4b",
    "escalation_target": "claude-tech-lead",
}


@dataclass
class RoleRoutingStore:
    routing: dict[str, RoleRouteValue] = field(default_factory=lambda: dict(DEFAULT_ROLE_ROUTING))
    _settings_repository: SettingsRepository | None = field(default=None, repr=False)

    def bind_settings_repository(self, repository: SettingsRepository) -> None:
        self._settings_repository = repository
        self.routing = repository.get_role_routing(DEFAULT_ROLE_ROUTING)

    def get(self) -> dict[str, RoleRouteValue]:
        return dict(self.routing)

    def update(self, values: dict[str, RoleRouteValue]) -> dict[str, RoleRouteValue]:
        for role, model in values.items():
            if not role.strip():
                raise ValueError("Role name cannot be empty")
            cleaned = _clean_route_value(model)
            if not cleaned:
                raise ValueError(f"Model for role {role!r} cannot be empty")
            self.routing[role] = cleaned
        if self._settings_repository is not None:
            self._settings_repository.set_role_routing(self.routing)
        return self.get()


role_routing_store = RoleRoutingStore()


def _clean_route_value(value: RoleRouteValue) -> RoleRouteValue | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    cleaned_items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return cleaned_items or None
