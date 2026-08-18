#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# Licensed under the Apache2.0. See LICENSE file in charm source for details.
"""Lightweight library for the vault-kv relation (no pydantic required).

Implements the requirer side of the vault-kv interface used by the Vault 2.0 charm.
"""

import json
import logging
import secrets
from typing import List, MutableMapping, Optional

import ops

# The unique Charmhub library identifier, never change it
LIBID = "591d6d2fb6a54853b4bb53ef16ef603a"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


class LogAdapter(logging.LoggerAdapter):
    """Adapter for the logger to prepend a prefix to all log lines."""

    prefix = "vault_kv"

    def process(self, msg: str, kwargs: MutableMapping) -> tuple[str, MutableMapping]:
        """Decide the format for the prepended text."""
        return f"[{self.prefix}] {msg}", kwargs


logger = LogAdapter(logging.getLogger(__name__), {})


class VaultKvGoneAwayEvent(ops.EventBase):
    """VaultKvGoneAwayEvent Event."""

    pass


class VaultKvConnectedEvent(ops.RelationEvent):
    """VaultKvConnectedEvent: fired when the vault-kv relation is joined."""

    def __init__(
        self, handle: ops.Handle, relation_id: int, relation_name: str, relation: ops.Relation
    ):
        super().__init__(handle, relation)
        self.relation_id = relation_id
        self.relation_name = relation_name

    def snapshot(self) -> dict:
        """Return snapshot data that should be persisted."""
        return dict(super().snapshot(), relation_id=self.relation_id, relation_name=self.relation_name)

    def restore(self, snapshot: dict) -> None:
        """Restore event from snapshot."""
        super().restore(snapshot)
        self.relation_id = snapshot["relation_id"]
        self.relation_name = snapshot["relation_name"]


class VaultKvReadyEvent(ops.RelationEvent):
    """VaultKvReadyEvent: fired when vault credentials are available."""

    def __init__(
        self, handle: ops.Handle, relation_id: int, relation_name: str, relation: ops.Relation
    ):
        super().__init__(handle, relation)
        self.relation_id = relation_id
        self.relation_name = relation_name

    def snapshot(self) -> dict:
        """Return snapshot data that should be persisted."""
        return dict(super().snapshot(), relation_id=self.relation_id, relation_name=self.relation_name)

    def restore(self, snapshot: dict) -> None:
        """Restore event from snapshot."""
        super().restore(snapshot)
        self.relation_id = snapshot["relation_id"]
        self.relation_name = snapshot["relation_name"]


class VaultKvRequireEvents(ops.ObjectEvents):
    """List of events that the Vault Kv requirer charm can leverage."""

    connected = ops.EventSource(VaultKvConnectedEvent)
    ready = ops.EventSource(VaultKvReadyEvent)
    gone_away = ops.EventSource(VaultKvGoneAwayEvent)


class VaultKvRequires(ops.Object):
    """Class to be instantiated by the requiring side of the vault-kv relation."""

    on = VaultKvRequireEvents()  # type: ignore

    def __init__(
        self,
        charm: ops.CharmBase,
        relation_name: str,
        mount_suffix: str,
    ) -> None:
        """Initialize VaultKvRequires.

        Args:
            charm: The charm instance.
            relation_name: Name of the vault-kv relation.
            mount_suffix: Suffix appended to the KV mount name.
        """
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.mount_suffix = mount_suffix
        self.framework.observe(
            self.charm.on[relation_name].relation_joined,
            self._handle_relation,
        )
        self.framework.observe(
            self.charm.on.config_changed,
            self._handle_relation,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed,
            self._on_vault_kv_relation_changed,
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken,
            self._on_vault_kv_relation_broken,
        )

    def _handle_relation(self, _: ops.EventBase) -> None:
        """Set mount_suffix and emit connected event."""
        relations = self.charm.model.relations.get(self.relation_name, [])
        if not relations:
            return
        for relation in relations:
            if self.charm.unit.is_leader():
                relation.data[self.charm.app]["mount_suffix"] = self.mount_suffix
            self.on.connected.emit(relation.id, relation.name, relation)

    def _on_vault_kv_relation_changed(self, event: ops.RelationChangedEvent) -> None:
        """Handle relation changed – emit ready when credentials are available."""
        if event.app is None:
            return
        if (
            self._is_provider_data_present(event.relation.data.get(event.app, {}))
            and self.get_unit_credentials(event.relation) is not None
        ):
            self.on.ready.emit(event.relation.id, event.relation.name, event.relation)

    def _on_vault_kv_relation_broken(self, event: ops.RelationBrokenEvent) -> None:
        """Handle relation broken."""
        self.on.gone_away.emit()

    def _is_provider_data_present(self, data: ops.RelationDataContent) -> bool:
        """Return True if vault_url, ca_certificate, mount, and credentials are present."""
        return bool(
            data.get("vault_url")
            and data.get("ca_certificate")
            and data.get("mount")
            and data.get("credentials")
        )

    def request_credentials(
        self, relation: ops.Relation, egress_subnet: List[str] | str, nonce: str
    ) -> None:
        """Write egress_subnet and nonce to unit relation data to request credentials.

        Args:
            relation: The relation object.
            egress_subnet: Egress subnets in CIDR notation (list or comma-separated string).
            nonce: Unique per-unit identifier (use secrets.token_hex(16)).
        """
        if isinstance(egress_subnet, list):
            egress_subnet = ",".join(egress_subnet)
        relation.data[self.charm.unit]["egress_subnet"] = egress_subnet
        relation.data[self.charm.unit]["nonce"] = nonce

    def get_vault_url(self, relation: ops.Relation) -> Optional[str]:
        """Return the Vault URL from the relation data."""
        if relation.app is None:
            return None
        return relation.data[relation.app].get("vault_url")

    def get_ca_certificate(self, relation: ops.Relation) -> Optional[str]:
        """Return the CA certificate from the relation data."""
        if relation.app is None:
            return None
        return relation.data[relation.app].get("ca_certificate")

    def get_mount(self, relation: ops.Relation) -> Optional[str]:
        """Return the KV mount path from the relation data."""
        if relation.app is None:
            return None
        return relation.data[relation.app].get("mount")

    def get_unit_credentials(self, relation: ops.Relation) -> Optional[str]:
        """Return the Juju secret ID for this unit's credentials, or None.

        Args:
            relation: The relation object.

        Returns:
            A Juju secret ID string, or None if credentials not yet provided.
        """
        nonce = relation.data[self.charm.unit].get("nonce")
        if nonce is None or relation.app is None:
            return None
        credentials_json = relation.data[relation.app].get("credentials", "{}")
        return json.loads(credentials_json).get(nonce)
