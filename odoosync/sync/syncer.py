import hashlib
import logging
import math
import socket
import time
from collections import defaultdict
from itertools import combinations
from typing import Dict, Iterable, List, Optional, Set, Tuple

import odoorpc

from urllib.error import HTTPError, URLError

from ..connection import OdooInstance
from ..core import PROGRESS_LEVEL, get_logger, set_level, set_muted_levels
from ..models import INTERNAL_RUNTIME_FIELDS, OdooModel

logger = get_logger(__name__)


class ProgressTracker:
    def __init__(self, model_name: str, total: int) -> None:
        self.model_name = model_name
        self.total = total
        self.completed = 0
        self._log_interval = 1 if total <= 0 else max(1, total // 10)
        self._log_extra = {"odoosync_progress": True}

    def advance(self, step: int = 1, context: Optional[str] = None) -> None:
        if self.total <= 0:
            return
        self.completed = min(self.total, self.completed + max(0, step))
        should_log = self.completed == self.total or self.completed % self._log_interval == 0
        if should_log:
            remaining = self.total - self.completed
            percent = (self.completed / self.total) * 100
            message = (
                f"Progress {self.model_name}: {self.completed}/{self.total} "
                f"({percent:.1f}% complete, remaining={remaining})"
            )
            if context:
                message = f"{message} | {context}"
            logger.log(PROGRESS_LEVEL, message, extra=self._log_extra.copy())


class ModelSyncer:
    """Syncer instance."""

    @staticmethod
    def _coerce_positive_int(value, default: int) -> int:
        try:
            parsed = int(value)
            if parsed <= 0:
                raise ValueError
            return parsed
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def _coerce_non_negative_float(value, default: float) -> float:
        try:
            parsed = float(value)
            if parsed < 0:
                raise ValueError
            return parsed
        except (TypeError, ValueError):
            return float(default)

    def _execute_with_retry(self, func, context: str):
        attempts = getattr(self, "rpc_retry_attempts", 1) or 1
        delay = getattr(self, "rpc_retry_delay", 0.0) or 0.0
        backoff = getattr(self, "rpc_retry_backoff", 1.0) or 1.0
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                return func()
            except (HTTPError, URLError, socket.timeout, TimeoutError, ConnectionError, ConnectionResetError, BrokenPipeError) as exc:
                last_error = exc
                if attempt >= attempts:
                    logger.error("RPC call failed after %s attempt(s) (%s): %s", attempts, context, exc)
                    raise
                sleep_for = delay * (backoff ** (attempt - 1))
                logger.warning(
                    "RPC call failed (%s); retrying in %.2fs (%s/%s)",
                    context,
                    sleep_for,
                    attempt,
                    attempts,
                )
                if sleep_for > 0:
                    time.sleep(sleep_for)
            except Exception:
                raise
        if last_error:
            raise last_error

    def __init__(self, _struct: dict, _timestamps: dict, options: Optional[dict] = None):
        self.options = _struct.get("options", {})
        self.dry_run = self.options.get("dry_run")
        self.debug = bool(self.options.get("debug"))
        self.sync_dependencies = bool(self.options.get("sync_dependencies"))
        self.force_sync = bool(self.options.get("force_sync"))
        self.rpc_retry_attempts = self._coerce_positive_int(self.options.get("rpc_retry_attempts"), default=3)
        self.rpc_retry_delay = self._coerce_non_negative_float(self.options.get("rpc_retry_delay"), default=2.0)
        self.rpc_retry_backoff = self._coerce_non_negative_float(self.options.get("rpc_retry_backoff"), default=1.5)

        self.auto_xmlid_lookup = bool(self.options.get("auto_xmlid_lookup", True))
        log_level_opt = self.options.get("log_level")
        level_applied = False
        if log_level_opt:
            level_name = str(log_level_opt).upper()
            level_value = getattr(logging, level_name, None)
            if isinstance(level_value, int):
                set_level(level_value)
                level_applied = True
            else:
                logger.warning("Unknown log_level %r; keeping default", log_level_opt)
        if not level_applied and self.debug:
            set_level(logging.DEBUG)

        mute_levels_opt = self.options.get("mute_log_levels") or self.options.get("muted_log_levels")
        if mute_levels_opt is not None:
            if isinstance(mute_levels_opt, (list, tuple)):
                set_muted_levels(mute_levels_opt)
            else:
                logger.warning("mute_log_levels must be a list of level names; got %r", mute_levels_opt)
                set_muted_levels([])
        else:
            set_muted_levels([])
        logger.info("-----------START-----------")
        logger.debug("Created ModelSyncer instance...")
        forward_id_map, reverse_id_map, xmlid_overrides = self._parse_record_id_mappings(_struct)
        self.record_id_map_forward = forward_id_map
        self.record_id_map_reverse = reverse_id_map
        self.record_id_xmlid_overrides = xmlid_overrides
        self.source_timestamp = _timestamps.get("source")
        self.dest_timestamp = _timestamps.get("target")
        default_netrc_path = self.options.get("netrc_path")
        source_netrc_path = self.options.get("source_netrc_path", default_netrc_path)
        target_netrc_path = self.options.get("target_netrc_path", default_netrc_path)
        self.source = OdooInstance(_struct.get("source", {}), source_netrc_path)
        self.dest = OdooInstance(_struct.get("target", {}), target_netrc_path)
        self.source_ir_fields = self.source.odoo.env["ir.model.fields"]
        self.models = [OdooModel(m) for m in _struct.get("models", {}) if not m.get("reverse")]
        self.models_by_name = {m.name: m for m in self.models}
        self.reverse_models = [OdooModel(m) for m in _struct.get("models", {}) if m.get("reverse")]
        self.reverse_models_by_name = {m.name: m for m in self.reverse_models}
        self.prefix = self.options.get("prefix", "__export_sfit__").rstrip(".")
        timeout = self.options.get("timeout", 600)
        self.source.odoo.config["timeout"] = timeout
        self.dest.odoo.config["timeout"] = timeout
        self._source_xmlid_cache: Dict[str, Dict[int, Optional[str]]] = defaultdict(dict)
        self._dest_xmlid_cache: Dict[str, Dict[Tuple[str, str], Optional[int]]] = defaultdict(dict)
        self._external_translations: Dict[str, Dict[int, int]] = defaultdict(dict)
        default_batch_size = 1000
        disable_batching = bool(self.options.get("disable_batching"))
        batch_size_option = self.options.get("batch_size")
        if disable_batching:
            self.batch_size = None
        elif batch_size_option is None:
            self.batch_size = default_batch_size
        else:
            try:
                parsed = int(batch_size_option)
                if parsed <= 0:
                    logger.warning(
                        "Batch size %s is non-positive; disabling batching and loading all records at once",
                        batch_size_option,
                    )
                    self.batch_size = None
                else:
                    self.batch_size = parsed
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid batch size %s provided; falling back to default of %s",
                    batch_size_option,
                    default_batch_size,
                )
                self.batch_size = default_batch_size
        if self.batch_size:
            logger.debug("Using batch size %s for record retrieval", self.batch_size)
        else:
            logger.debug("Batch size disabled; fetching records in a single request")

    def _parse_record_id_mappings(
        self, struct: dict
    ) -> Tuple[Dict[str, Dict[int, int]], Dict[str, Dict[int, int]], Dict[str, Dict[str, str]]]:
        mapping_cfg = struct.get("record_id_mappings") or {}
        if not isinstance(mapping_cfg, dict):
            logger.warning("Ignoring invalid `record_id_mappings` value; expected a mapping but got %r", mapping_cfg)
            mapping_cfg = {}

        forward_cfg = mapping_cfg.get("forward") if isinstance(mapping_cfg, dict) else {}
        reverse_cfg = mapping_cfg.get("reverse") if isinstance(mapping_cfg, dict) else {}
        xmlid_overrides_cfg = mapping_cfg.get("xmlid_overrides") if isinstance(mapping_cfg, dict) else {}

        if forward_cfg is not None and not isinstance(forward_cfg, dict):
            logger.warning(
                "Ignoring invalid `record_id_mappings.forward`; expected a mapping but got %r",
                forward_cfg,
            )
            forward_cfg = {}
        if reverse_cfg is not None and not isinstance(reverse_cfg, dict):
            logger.warning(
                "Ignoring invalid `record_id_mappings.reverse`; expected a mapping but got %r",
                reverse_cfg,
            )
            reverse_cfg = {}
        if xmlid_overrides_cfg is not None and not isinstance(xmlid_overrides_cfg, dict):
            logger.warning(
                "Ignoring invalid `record_id_mappings.xmlid_overrides`; expected a mapping but got %r",
                xmlid_overrides_cfg,
            )
            xmlid_overrides_cfg = {}

        legacy_forward = struct.get("manual_mapping")
        legacy_reverse = struct.get("reverse_manual_mapping")

        used_legacy_keys = False

        if forward_cfg is None:
            forward_cfg = legacy_forward or {}
            if legacy_forward is not None:
                used_legacy_keys = True
        elif legacy_forward:
            logger.warning(
                "Ignoring legacy `manual_mapping` because `record_id_mappings.forward` is provided."
            )

        if reverse_cfg is None:
            reverse_cfg = legacy_reverse or {}
            if legacy_reverse is not None:
                used_legacy_keys = True
        elif legacy_reverse:
            logger.warning(
                "Ignoring legacy `reverse_manual_mapping` because `record_id_mappings.reverse` is provided."
            )

        if used_legacy_keys:
            logger.warning(
                "Configuration keys `manual_mapping`/`reverse_manual_mapping` are deprecated. "
                "Use `record_id_mappings.forward`/`record_id_mappings.reverse` instead."
            )

        forward_map: Dict[str, Dict[int, int]] = {}
        for model_name, mapping in (forward_cfg or {}).items():
            if not isinstance(mapping, dict):
                logger.warning(
                    "Skipping forward record_id mapping for %s; expected a mapping but got %r",
                    model_name,
                    mapping,
                )
                continue
            forward_map[model_name] = dict(mapping or {})

        reverse_map: Dict[str, Dict[int, int]] = {}
        for model_name, mapping in (reverse_cfg or {}).items():
            if not isinstance(mapping, dict):
                logger.warning(
                    "Skipping reverse record_id mapping for %s; expected a mapping but got %r",
                    model_name,
                    mapping,
                )
                continue
            reverse_map[model_name] = dict(mapping or {})

        xmlid_overrides_map: Dict[str, Dict[str, str]] = {}
        for model_name, overrides in (xmlid_overrides_cfg or {}).items():
            if not isinstance(overrides, dict):
                logger.warning(
                    "Skipping xmlid overrides for %s; expected a mapping but got %r",
                    model_name,
                    overrides,
                )
                continue
            cleaned: Dict[str, str] = {}
            for source_xmlid, dest_xmlid in overrides.items():
                if not isinstance(source_xmlid, str) or not isinstance(dest_xmlid, str):
                    logger.warning(
                        "Ignoring non-string xmlid override %r -> %r for model %s",
                        source_xmlid,
                        dest_xmlid,
                        model_name,
                    )
                    continue
                cleaned[source_xmlid] = dest_xmlid
            if cleaned:
                xmlid_overrides_map[model_name] = cleaned

        return forward_map, reverse_map, xmlid_overrides_map

    def _apply_xmlid_override(self, model_name: str, xmlid: Optional[str]) -> Optional[str]:
        if not xmlid:
            return xmlid
        overrides = getattr(self, "record_id_xmlid_overrides", {}).get(model_name)
        if overrides:
            return overrides.get(xmlid, xmlid)
        return xmlid

    def _apply_xmlid_overrides_bulk(self, model_name: str, xmlids: Dict[int, Optional[str]]) -> Dict[int, Optional[str]]:
        if not xmlids:
            return xmlids
        overrides = getattr(self, "record_id_xmlid_overrides", {}).get(model_name)
        if not overrides:
            return xmlids
        adjusted: Dict[int, Optional[str]] = {}
        for source_id, xmlid in xmlids.items():
            if xmlid and xmlid in overrides:
                adjusted[source_id] = overrides[xmlid]
            else:
                adjusted[source_id] = xmlid
        return adjusted

    def _create_record_with_retry(
        self,
        odoo_instance: OdooInstance,
        model: OdooModel,
        payload: dict,
        source_id: int,
        add_dest_id_function,
        create_xmlid_function,
    ) -> Optional[int]:
        obj = odoo_instance.odoo.env[model.name]
        base_payload = dict(payload or {})
        try:
            dest_id = obj.create(base_payload)
            if isinstance(dest_id, list):
                dest_id = dest_id[0] if dest_id else None
            if dest_id:
                logger.info(str(dest_id))
                add_dest_id_function(model.name, source_id, dest_id)
                create_xmlid_function(model.name, source_id, dest_id)
            return dest_id
        except odoorpc.error.RPCError as exc:
            dest_id = self._retry_create_with_field_subsets(
                odoo_instance,
                model,
                base_payload,
                source_id,
                exc,
            )
            if dest_id:
                logger.info(str(dest_id))
                add_dest_id_function(model.name, source_id, dest_id)
                create_xmlid_function(model.name, source_id, dest_id)
            return dest_id

    def _retry_create_with_field_subsets(
        self,
        odoo_instance: OdooInstance,
        model: OdooModel,
        payload: Dict,
        source_id: int,
        initial_exc: odoorpc.error.RPCError,
    ) -> Optional[int]:
        self._record_retry_event(
            odoo_instance,
            model.name,
            source_id,
            (),
            f"initial failure: {initial_exc}",
            success=False,
        )

        retry_cfg = getattr(model, "retry_on_create", None)
        if not retry_cfg:
            return None

        candidate_fields = [field for field in retry_cfg.get("fields", []) if field in payload]
        if not candidate_fields:
            self._record_retry_event(
                odoo_instance,
                model.name,
                source_id,
                (),
                "retry fields not present in payload",
                success=False,
            )
            return None

        max_subset = retry_cfg.get("max_subset")
        if max_subset is None:
            max_subset = len(candidate_fields)
        max_subset = max(1, min(len(candidate_fields), max_subset))

        obj = odoo_instance.odoo.env[model.name]
        for subset_size in range(1, max_subset + 1):
            for subset in combinations(candidate_fields, subset_size):
                trimmed_payload = {key: value for key, value in payload.items() if key not in subset}
                try:
                    dest_id = obj.create(trimmed_payload)
                    if isinstance(dest_id, list):
                        dest_id = dest_id[0] if dest_id else None
                except odoorpc.error.RPCError as subset_exc:
                    self._record_retry_event(
                        odoo_instance,
                        model.name,
                        source_id,
                        subset,
                        str(subset_exc),
                        success=False,
                    )
                    continue
                self._record_retry_event(
                    odoo_instance,
                    model.name,
                    source_id,
                    subset,
                    f"success with dest_id={dest_id}",
                    success=True,
                    dest_id=dest_id,
                )
                return dest_id

        self._record_retry_event(
            odoo_instance,
            model.name,
            source_id,
            tuple(candidate_fields[:max_subset]),
            "exhausted retry combinations without success",
            success=False,
        )
        return None

    def _record_retry_event(
        self,
        odoo_instance: OdooInstance,
        model_name: str,
        source_id: int,
        removed_fields: Iterable[str],
        message: str,
        success: bool,
        dest_id: Optional[int] = None,
    ) -> None:
        removed_list = list(removed_fields) if removed_fields else []
        removed_repr = ", ".join(removed_list) if removed_list else "none"
        base_message = (
            f"Create {'succeeded' if success else 'failed'} for {model_name} (source_id={source_id}) "
            f"after dropping fields [{removed_repr}]"
        )
        if dest_id is not None:
            base_message = f"{base_message}; dest_id={dest_id}"
        if message:
            base_message = f"{base_message}: {message}"

        if success:
            logger.info(base_message)
            level = "INFO"
        else:
            logger.error(base_message)
            level = "ERROR"

        try:
            log_model = odoo_instance.odoo.env["ir.logging"]
            log_model.create({
                "name": "odoosync.retry",
                "type": "server",
                "level": level,
                "dbname": getattr(odoo_instance, "database", ""),
                "message": base_message,
                "path": "odoosync",
                "func": "_create_record_with_retry",
                "line": 0,
            })
        except Exception:
            logger.debug("Failed to push retry event to destination log", exc_info=True)

    def _get_xmlid(self, model_name, _id):
        return "{}_{}".format(model_name.replace(".", "_"), _id)

    def _ensure_xmlid(self, model_name: str, res_id: int, xmlid: str) -> None:
        """Create or update the xmlid so we always point at the latest record."""
        payload = {
            "model": model_name,
            "module": self.prefix,
            "name": xmlid,
            "res_id": res_id,
        }
        try:
            self.dest.ir_model_obj.create(payload)
            return
        except odoorpc.error.RPCError as exc:
            message = str(exc) if exc else ""
            if "ir_model_data_module_name_uniq_index" not in message:
                raise
            existing = self.dest.ir_model_obj.search([
                ("module", "=", self.prefix),
                ("name", "=", xmlid),
            ])
            if not existing:
                raise
            self.dest.ir_model_obj.write(existing, {"res_id": res_id})
            logger.info(
                "Re-linked existing xmlid %s.%s to %s[%s]",
                self.prefix,
                xmlid,
                model_name,
                res_id,
            )

    def create_xmlid(self, model_name, source_id, dest_id):
        """Create a link between source id and dest id."""
        xmlid = self._get_xmlid(model_name, source_id)
        self._ensure_xmlid(model_name, dest_id, xmlid)

    def create_reverse_xmlid(self, model_name, source_id, dest_id):
        """Create a link between dest id and source id."""
        xmlid = self._get_xmlid(model_name, dest_id)
        self._ensure_xmlid(model_name, source_id, xmlid)

    def _add_translations(self, loaded):
        """Create translation tables for source record id -> dest record id."""
        xmlids = []
        for model_name, _ids in loaded.items():
            for source_id in list(_ids):
                xmlids.append(self._get_xmlid(model_name, source_id))
        dest_external_ids = self.dest.ir_model_obj.search([
            ("name", "in", xmlids),
            ("module", "=", self.prefix),
        ])
        dest_external_records = self.dest.ir_model_obj.read(dest_external_ids, ["name", "model", "res_id"])
        for record in dest_external_records:
            try:
                source_id = int(str(record["name"].split(".")[-1]).split("_")[-1])
                self._add_dest_id(record["model"], source_id, record["res_id"])
            except ValueError:
                pass
        for model in self.models:
            logger.debug("Model %s with %s translations", model.name, len(model.trans))

    def _add_reverse_translations(self, loaded):
        """Create translation tables for dest record id -> source record id."""
        dest_external_ids = []
        for model_name, _ids in loaded.items():
            dest_external_ids += self.dest.ir_model_obj.search([
                ("res_id", "in", list(_ids)),
                ("model", "=", model_name),
                ("module", "=", self.prefix),
            ])
        dest_external_records = self.dest.ir_model_obj.read(dest_external_ids, ["name", "model", "res_id"])
        for record in dest_external_records:
            try:
                source_id = int(str(record["name"].split(".")[-1]).split("_")[-1])
                self._add_source_id(record["model"], record["res_id"], source_id)
            except ValueError:
                pass

    def _translate_to_dest_id(self, model_name, source_id, get_xmlid=False):
        xmlid = self._get_xmlid(model_name, source_id)
        record = self.dest.ir_model_obj.search([
            ("module", "=", self.prefix),
            ("name", "=", xmlid),
        ])
        if record:
            if get_xmlid:
                return record
            return self.dest.ir_model_obj.read(record, ["res_id"])[0]["res_id"]
        return None

    def _translate_to_source_id(self, model_name, dest_id, get_xmlid=False):
        record = self.dest.ir_model_obj.search([
            ("module", "=", self.prefix),
            ("res_id", "=", dest_id),
            ("model", "=", model_name),
        ])
        if record:
            if get_xmlid:
                return record
            xmlid = self.dest.ir_model_obj.read(record, ["name"])[0]["name"]
            try:
                source_id = int(xmlid.split(".")[-1].split("_")[-1])
            except ValueError:
                source_id = None
            return source_id
        return None

    def _find_dest_id(self, model_name, source_id):
        model = self.models_by_name.get(model_name)
        dest_id = self.record_id_map_forward.get(model_name, {}).get(source_id)
        if dest_id and model:
            model.trans[source_id] = dest_id
            model.translatable_ids.add(source_id)
        if not dest_id and model:
            dest_id = source_id and model.trans.get(source_id)
        if not dest_id:
            dest_id = self._external_translations.get(model_name, {}).get(source_id)
            if dest_id and model:
                model.trans[source_id] = dest_id
                model.translatable_ids.add(source_id)
        if not dest_id and self.auto_xmlid_lookup and source_id:
            xmlid = self._lookup_source_xmlid(model_name, source_id)
            xmlid = self._apply_xmlid_override(model_name, xmlid)
            if xmlid:
                dest_id = self._lookup_dest_by_xmlid(model_name, xmlid)
                if dest_id:
                    if model:
                        model.trans[source_id] = dest_id
                        model.translatable_ids.add(source_id)
                    self._external_translations.setdefault(model_name, {})[source_id] = dest_id
        logger.debug("source %s[%s] -> dest %s[%s]", model_name, source_id, model and model.name, dest_id)
        return dest_id

    def _find_source_id(self, model_name, dest_id):
        model = self.reverse_models_by_name.get(model_name)
        source_id = self.record_id_map_reverse.get(model_name, {}).get(dest_id)
        if not source_id:
            source_id = dest_id and model and model.trans.get(dest_id)
        logger.debug("dest %s[%s] -> source %s[%s]", model_name, dest_id, model and model.name, source_id)
        return source_id

    def _add_dest_id(self, model_name, source_id, dest_id):
        model = self.models_by_name.get(model_name)
        if model:
            model.trans[source_id] = dest_id
            model.translatable_ids.add(source_id)

    def _add_source_id(self, model_name, dest_id, source_id):
        model = self.reverse_models_by_name.get(model_name)
        if model:
            model.trans[dest_id] = source_id
            model.translatable_ids.add(dest_id)

    def _lookup_source_xmlid(self, model_name, source_id):
        cache = self._source_xmlid_cache[model_name]
        if source_id in cache:
            return cache[source_id]
        try:
            ir_model_data = self.source.odoo.env["ir.model.data"]
        except (AttributeError, KeyError):
            cache[source_id] = None
            return None
        record_ids = self._execute_with_retry(
            lambda: ir_model_data.search([
                ("model", "=", model_name),
                ("res_id", "=", source_id),
            ]),
            context=f"ir.model.data search {model_name}[{source_id}]",
        )
        xmlid = None
        if record_ids:
            data = self._execute_with_retry(
                lambda: ir_model_data.read(record_ids[:1], ["module", "name"]),
                context=f"ir.model.data read {model_name}[{source_id}]",
            )
            if data:
                module = data[0].get("module")
                name = data[0].get("name")
                if module and name:
                    xmlid = f"{module}.{name}"
        cache[source_id] = xmlid
        return xmlid

    def _lookup_dest_by_xmlid(self, model_name, xmlid):
        if not xmlid or "." not in xmlid:
            return None
        module, name = xmlid.split(".", 1)
        cache = self._dest_xmlid_cache[model_name]
        key = (module, name)
        if key in cache:
            return cache[key]
        try:
            ir_model_data = self.dest.ir_model_obj
        except AttributeError:
            cache[key] = None
            return None
        record_ids = self._execute_with_retry(
            lambda: ir_model_data.search([
                ("module", "=", module),
                ("name", "=", name),
                ("model", "=", model_name),
            ]),
            context=f"ir.model.data search dest {model_name} xmlid={xmlid}",
        )
        dest_id = None
        if record_ids:
            data = self._execute_with_retry(
                lambda: ir_model_data.read(record_ids[:1], ["res_id"]),
                context=f"ir.model.data read dest {model_name} xmlid={xmlid}",
            )
            if data:
                dest_id = data[0].get("res_id")
        cache[key] = dest_id
        return dest_id

    def _iter_batches(self, items: Iterable[int], size: Optional[int]) -> Iterable[List[int]]:
        sequence = list(items)
        if not sequence:
            return
        if not size or size <= 0:
            yield sequence
            return
        for index in range(0, len(sequence), size):
            yield sequence[index : index + size]

    def _get_effective_batch_size(self, model: Optional[OdooModel] = None) -> Optional[int]:
        if model and getattr(model, "has_batch_size_override", False):
            return getattr(model, "batch_size_override", None)
        return self.batch_size

    def _lookup_source_xmlids_bulk(self, model_name: str, source_ids: List[int]) -> Dict[int, Optional[str]]:
        cache = self._source_xmlid_cache[model_name]
        result: Dict[int, Optional[str]] = {}
        missing = [sid for sid in source_ids if sid not in cache]

        if missing:
            try:
                ir_model_data = self.source.odoo.env["ir.model.data"]
            except (AttributeError, KeyError, TypeError):
                for sid in missing:
                    cache[sid] = None
            else:
                chunk_size = getattr(self, "batch_size", None) or len(missing)
                for chunk in self._iter_batches(missing, chunk_size):
                    record_ids = self._execute_with_retry(
                        lambda: ir_model_data.search([
                            ("model", "=", model_name),
                            ("res_id", "in", chunk),
                        ]),
                        context=f"ir.model.data batch search {model_name}",
                    )
                    records = self._execute_with_retry(
                        lambda: ir_model_data.read(record_ids, ["res_id", "module", "name"]),
                        context=f"ir.model.data batch read {model_name}",
                    ) if record_ids else []
                    data_by_res = {rec.get("res_id"): rec for rec in records}
                    for source_id in chunk:
                        record = data_by_res.get(source_id)
                        module = record and record.get("module")
                        name = record and record.get("name")
                        if module and name:
                            cache[source_id] = f"{module}.{name}"
                        else:
                            cache.setdefault(source_id, None)

        for source_id in source_ids:
            result[source_id] = cache.get(source_id)
        return result

    def _lookup_dest_ids_by_xmlid_bulk(self, model_name: str, xmlids: Dict[int, str]) -> Dict[int, Optional[int]]:
        cache = self._dest_xmlid_cache[model_name]
        result: Dict[int, Optional[int]] = {}
        by_module: Dict[str, List[Tuple[int, str]]] = defaultdict(list)

        for source_id, xmlid in xmlids.items():
            if not xmlid or "." not in xmlid:
                result[source_id] = None
                continue
            module, name = xmlid.split(".", 1)
            key = (module, name)
            if key in cache:
                result[source_id] = cache[key]
            else:
                by_module[module].append((source_id, name))

        if by_module:
            try:
                model_data = self.dest.ir_model_obj
            except AttributeError:
                model_data = None

            if model_data:
                for module, entries in by_module.items():
                    names = [name for _, name in entries]
                    record_ids = self._execute_with_retry(
                        lambda: model_data.search([
                            ("module", "=", module),
                            ("name", "in", names),
                            ("model", "=", model_name),
                        ]),
                        context=f"ir.model.data search dest batch {model_name}",
                    )
                    records = self._execute_with_retry(
                        lambda: model_data.read(record_ids, ["module", "name", "res_id"]),
                        context=f"ir.model.data read dest batch {model_name}",
                    ) if record_ids else []
                    name_to_res = {rec.get("name"): rec.get("res_id") for rec in records}
                    for source_id, name in entries:
                        dest_id = name_to_res.get(name)
                        cache[(module, name)] = dest_id
                        result[source_id] = dest_id
                    # Ensure we cache negative lookups as well
                    for source_id, name in entries:
                        cache.setdefault((module, name), result.get(source_id))
            else:
                for module, entries in by_module.items():
                    for source_id, name in entries:
                        cache[(module, name)] = None
                        result[source_id] = None

        for source_id, xmlid in xmlids.items():
            if source_id in result:
                continue
            if not xmlid or "." not in xmlid:
                result[source_id] = None
                continue
            module, name = xmlid.split(".", 1)
            result[source_id] = cache.get((module, name))
        return result

    @property
    def manual_mapping(self) -> Dict[str, Dict[int, int]]:
        logger.warning(
            "Accessing ModelSyncer.manual_mapping is deprecated; use `record_id_map_forward` instead."
        )
        return self.record_id_map_forward

    @manual_mapping.setter
    def manual_mapping(self, value: Optional[dict]) -> None:
        logger.warning(
            "Assigning to ModelSyncer.manual_mapping is deprecated; use `record_id_map_forward` instead."
        )
        self.record_id_map_forward = dict(value or {})

    @property
    def reverse_manual_mapping(self) -> Dict[str, Dict[int, int]]:
        logger.warning(
            "Accessing ModelSyncer.reverse_manual_mapping is deprecated; use `record_id_map_reverse` instead."
        )
        return self.record_id_map_reverse

    @reverse_manual_mapping.setter
    def reverse_manual_mapping(self, value: Optional[dict]) -> None:
        logger.warning(
            "Assigning to ModelSyncer.reverse_manual_mapping is deprecated; use `record_id_map_reverse` instead."
        )
        self.record_id_map_reverse = dict(value or {})

    def _prefetch_destination_ids(self, model: OdooModel, records: List[dict]) -> None:
        if not records or not model.name:
            return
        if not hasattr(model, "trans") or not hasattr(model, "translatable_ids"):
            return

        manual_map = self.record_id_map_forward.get(model.name, {})
        existing_trans = model.trans
        unresolved: List[int] = []

        for record in records:
            source_id = record.get("id")
            if not source_id:
                continue
            dest_id = manual_map.get(source_id)
            if dest_id:
                existing_trans[source_id] = dest_id
                model.translatable_ids.add(source_id)
                continue
            dest_id = existing_trans.get(source_id)
            if dest_id:
                continue
            dest_id = self._external_translations.get(model.name, {}).get(source_id)
            if dest_id:
                existing_trans[source_id] = dest_id
                model.translatable_ids.add(source_id)
                continue
            unresolved.append(source_id)

        if not unresolved or not self.auto_xmlid_lookup:
            return

        xmlids = self._lookup_source_xmlids_bulk(model.name, unresolved)
        xmlids = self._apply_xmlid_overrides_bulk(model.name, xmlids)
        lookup_candidates = {sid: xmlid for sid, xmlid in xmlids.items() if xmlid}
        if not lookup_candidates:
            return

        dest_ids = self._lookup_dest_ids_by_xmlid_bulk(model.name, lookup_candidates)
        for source_id, dest_id in dest_ids.items():
            if dest_id:
                existing_trans[source_id] = dest_id
                model.translatable_ids.add(source_id)
                self._external_translations.setdefault(model.name, {})[source_id] = dest_id

    def _make_hash(self, vals: dict) -> str:
        _hash = hashlib.md5()
        for key, value in sorted((k, v) for k, v in vals.items() if k not in INTERNAL_RUNTIME_FIELDS):
            if key == "id":
                continue
            if isinstance(value, (list, tuple)):
                value = value[0]
            if not value:
                value = "___None"
            _hash.update(str(value).encode("utf-8"))
        return _hash.hexdigest()

    def _write_or_create_model_record(
        self,
        odoo,
        model,
        vals,
        source_id,
        find_dest_id_function,
        add_dest_id_function,
        create_xmlid_function,
        translate_function,
        noupdate: bool = False,
    ):
        dest_id = find_dest_id_function(model.name, source_id)
        logger.debug("cache %s->%s (%s)", source_id, dest_id, model.name)
        if not dest_id:
            dest_id = translate_function(model.name, source_id)
            logger.debug("trans %s->%s (%s)", source_id, dest_id, model.name)
            if dest_id:
                add_dest_id_function(model.name, source_id, dest_id)
        obj = odoo.odoo.env[model.name]
        if dest_id and not noupdate:
            old_vals = obj.read([dest_id], model.dest_fields)[0]
            if self._make_hash(old_vals) == self._make_hash(vals):
                logger.info("no change: not updating %s[%s] from %s", model.name, dest_id, source_id)
            else:
                logger.info("updating record %s[%s] from source %s", model.name, dest_id, source_id)
                try:
                    if not self.dry_run:
                        obj.write(dest_id, vals)
                except odoorpc.error.RPCError as exc:
                    logger.error("Writing %s[%s] failed: %s", model.name, dest_id, str(exc))
        if not dest_id:
            logger.info("creating record from source %s[%s]..", model.name, source_id)
            if not self.dry_run:
                dest_id = self._create_record_with_retry(
                    odoo,
                    model,
                    vals,
                    source_id,
                    add_dest_id_function,
                    create_xmlid_function,
                )

    def _sync_one_model(self, model: OdooModel) -> None:
        logger.debug("Totally %s %s records considered for sync...", len(model.records), model.name)

        if model.reverse:
            find_dest_id_function = self._find_source_id
            add_dest_id_function = self._add_source_id
            create_xmlid_function = self.create_reverse_xmlid
            translate_function = self._translate_to_source_id
            odoo_instance = self.source
        else:
            find_dest_id_function = self._find_dest_id
            add_dest_id_function = self._add_dest_id
            create_xmlid_function = self.create_xmlid
            translate_function = self._translate_to_dest_id
            odoo_instance = self.dest
            self._prefetch_destination_ids(model, model.records)
            
        # Inject Odoo instances for indirect mapping support
        model._source_odoo = self.source.odoo
        model._dest_odoo = self.dest.odoo
        obj = odoo_instance.odoo.env[model.name]
        model_batch_size = self._get_effective_batch_size(model)

        total_records = len(model.records)
        if model_batch_size:
            batches = math.ceil(total_records / model_batch_size) if total_records else 0
            batch_descriptor = model_batch_size
        else:
            batches = 1 if total_records else 0
            batch_descriptor = "all"
        logger.info(
            "Sync plan for %s: %s records across %s batch(es) (batch_size=%s)",
            model.name,
            total_records,
            batches,
            batch_descriptor,
        )
        progress = ProgressTracker(model.name, total_records)

        to_update: Dict[int, Dict[str, object]] = {}
        to_create = []
        dest_ids = []
        for record in model.records:
            source_id = record["id"]
            if bool(record.get("__sfit_dep")) and not self.sync_dependencies:
                logger.debug(
                    "Skipping dependency record %s[%s] (sync_dependencies disabled)",
                    model.name,
                    record.get("id"),
                )
                progress.advance(context=f"skipped dependency source_id={source_id}")
                continue
            dest_id = find_dest_id_function(model.name, source_id)
            if dest_id:
                mapped = model._map_fields(record, find_dest_id_function)
                dest_ids.append(dest_id)
                record_hash = self._make_hash(mapped)
                to_update[dest_id] = {
                    "source_id": source_id,
                    "hash": record_hash,
                    "mapped": mapped,
                    "record": record,
                }
                continue
            if bool(record.get("__sfit_dep")) or source_id not in model.translatable_ids:
                to_create.append((source_id, record))
            else:
                mapped = model._map_fields(record, find_dest_id_function)
                dest_id = find_dest_id_function(model.name, source_id)
                if dest_id:
                    dest_ids.append(dest_id)
                    record_hash = self._make_hash(mapped)
                    to_update[dest_id] = {
                        "source_id": source_id,
                        "hash": record_hash,
                        "mapped": mapped,
                        "record": record,
                    }
                else:
                    to_create.append((source_id, record))

        if to_create:
            logger.info("%s records to create", len(to_create))
        pending_create = {sid: rec for sid, rec in to_create}
        scheduled = set()
        creating_stack = set()
        creation_sequence: List[int] = []

        def _schedule_source_record(source_id):
            if source_id in scheduled:
                return
            record = pending_create.get(source_id)
            if not record:
                return
            if source_id in creating_stack:
                logger.warning("Circular dependency detected for %s[%s]", model.name, source_id)
                return
            creating_stack.add(source_id)
            for field, rel_model_name in model.many2onefields.items():
                if rel_model_name != model.name:
                    continue
                rel_value = record.get(field)
                rel_source_id = rel_value and rel_value[0]
                if rel_source_id and rel_source_id in pending_create:
                    _schedule_source_record(rel_source_id)
            creation_sequence.append(source_id)
            scheduled.add(source_id)
            creating_stack.remove(source_id)

        for source_id, _ in to_create:
            _schedule_source_record(source_id)

        def _flush_create_batch(batch_source_ids: List[int], batch_payload: List[dict]) -> List[int]:
            if not batch_source_ids or not batch_payload:
                return []
            logger.info("creating %s records from sources %s", len(batch_source_ids), batch_source_ids)
            dest_ids: List[int] = []
            created_sources_local: List[int] = []
            if not self.dry_run:
                try:
                    payload = batch_payload[0] if len(batch_payload) == 1 else batch_payload
                    created = obj.create(payload)
                    if isinstance(created, list):
                        dest_ids = created
                    else:
                        dest_ids = [created]
                except odoorpc.error.RPCError as exc:
                    logger.error("Creating %s failed (batch fallback): %s", model.name, str(exc))
                    for index, source_id in enumerate(batch_source_ids):
                        single_payload = batch_payload[index] if index < len(batch_payload) else {}
                        dest_id = self._create_record_with_retry(
                            odoo_instance,
                            model,
                            single_payload,
                            source_id,
                            add_dest_id_function,
                            create_xmlid_function,
                        )
                        if dest_id:
                            created_sources_local.append(source_id)
                        pending_create.pop(source_id, None)
                    progress.advance(len(batch_source_ids))
                    return created_sources_local
                if len(dest_ids) != len(batch_source_ids):
                    logger.warning(
                        "Mismatch between created IDs (%s) and source batch (%s) for %s",
                        len(dest_ids),
                        len(batch_source_ids),
                        model.name,
                    )
            created = created_sources_local
            for index, source_id in enumerate(batch_source_ids):
                dest_id = dest_ids[index] if index < len(dest_ids) else None
                if dest_id:
                    logger.info(str(dest_id))
                    add_dest_id_function(model.name, source_id, dest_id)
                    create_xmlid_function(model.name, source_id, dest_id)
                    created.append(source_id)
                pending_create.pop(source_id, None)
            progress.advance(len(batch_source_ids))
            return created

        batch_payload: List[dict] = []
        batch_sources: List[int] = []
        created_sources: Set[int] = set()
        for source_id in creation_sequence:
            record = pending_create.get(source_id)
            if not record:
                continue
            needs_flush = False
            for field, rel_model_name in model.many2onefields.items():
                if rel_model_name != model.name:
                    continue
                rel_value = record.get(field)
                rel_source_id = rel_value and rel_value[0]
                if rel_source_id and rel_source_id in pending_create and rel_source_id not in created_sources:
                    needs_flush = True
                    break
            if needs_flush and batch_sources:
                created_sources.update(_flush_create_batch(batch_sources, batch_payload))
                batch_sources = []
                batch_payload = []
            logger.info("creating record from source %s[%s]..", model.name, source_id)
            mapped = model._map_fields(record, find_dest_id_function)
            batch_sources.append(source_id)
            batch_payload.append(mapped)
            if model_batch_size and len(batch_payload) >= model_batch_size:
                created_sources.update(_flush_create_batch(batch_sources, batch_payload))
                batch_payload = []
                batch_sources = []
        if batch_payload:
            created_sources.update(_flush_create_batch(batch_sources, batch_payload))

        really_update = []
        if dest_ids:
            logger.info("%s records to update", len(to_update))
            logger.info("Checking hashes...")
            old_vals = obj.read(dest_ids, model.dest_fields)
            for vals in old_vals:
                dest_id = vals["id"]
                entry = to_update.get(dest_id)
                if not entry:
                    continue
                old_hash = self._make_hash(vals)
                source_id = entry["source_id"]
                new_hash = entry["hash"]
                new_vals = entry["mapped"]
                if old_hash != new_hash:
                    really_update.append((source_id, dest_id, new_vals))
                elif logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "No diff for %s[%s]; existing=%s, incoming=%s",
                        model.name,
                        dest_id,
                        vals,
                        new_vals,
                    )

            found_ids = {vals["id"] for vals in old_vals}
            missing_ids = [dest_id for dest_id in dest_ids if dest_id not in found_ids]
            for missing_id in missing_ids:
                entry = to_update.pop(missing_id, None)
                if not entry:
                    continue
                source_id = entry["source_id"]
                logger.warning(
                    "Destination %s[%s] missing on read; recreating from source %s",
                    model.name,
                    missing_id,
                    source_id,
                )
                payload = entry["mapped"]
                if payload is None:
                    payload = model._map_fields(entry["record"], find_dest_id_function)
                if self.dry_run:
                    continue
                new_dest_id = self._create_record_with_retry(
                    odoo_instance,
                    model,
                    payload,
                    source_id,
                    add_dest_id_function,
                    create_xmlid_function,
                )
                if not new_dest_id:
                    logger.error(
                        "Failed to recreate missing %s from source %s",
                        model.name,
                        source_id,
                    )

        if really_update:
            logger.info("%s records are changed", len(really_update))
        for source_id, dest_id, vals in really_update:
            logger.info("updating record %s[%s] from source %s", model.name, dest_id, source_id)
            try:
                if not self.dry_run:
                    obj.write(dest_id, vals)
            except odoorpc.error.RPCError as exc:
                logger.error("Writing %s[%s] failed: %s", model.name, dest_id, str(exc))

        if to_update:
            progress.advance(len(to_update))

        if total_records:
            logger.log(
                PROGRESS_LEVEL,
                "%s sync complete: %s/%s records processed",
                model.name,
                progress.completed,
                progress.total,
                extra={"odoosync_progress": True},
            )

    def _load_dependencies_of_records(self, odoo_instance, loaded, other_models, add_translations):
        count = sum(len(recs) for recs in loaded.values())
        if not count:
            return
        logger.info("Find dependencies for %s records...", count)
        dep_struct = defaultdict(set)
        ignore_struct = defaultdict(set)
        for model_name, records in loaded.items():
            model = other_models[model_name]
            for record in records:
                for field, rel_model_name in model.many2onefields.items():
                    rel_id = record.get(field) and record.get(field)[0]
                    rel_model = other_models.get(rel_model_name)
                    if not rel_model:
                        ignore_struct[rel_model_name].add(rel_id)
                    elif rel_id and rel_id in rel_model.record_ids:
                        continue
                    elif rel_id:
                        dep_struct[rel_model_name].add(rel_id)
                dependency_rel_fields = getattr(model, "dependency_rel_fields", {})
                for field, meta in dependency_rel_fields.items():
                    rel_model_name = meta.get("relation")
                    if not rel_model_name:
                        continue
                    values = record.get(field)
                    if not values:
                        continue
                    if isinstance(values, (list, tuple, set)):
                        candidate_ids = [vid for vid in values if isinstance(vid, int) and vid]
                    else:
                        continue
                    if not candidate_ids:
                        continue
                    rel_model = other_models.get(rel_model_name)
                    if not rel_model:
                        for rel_id in candidate_ids:
                            ignore_struct[rel_model_name].add(rel_id)
                        continue
                    for rel_id in candidate_ids:
                        if rel_id not in rel_model.record_ids:
                            dep_struct[rel_model_name].add(rel_id)
        newly_loaded = {}
        add_translations(dep_struct)
        for rel_model_name, ids in dep_struct.items():
            rel_model = other_models[rel_model_name]
            ids_to_load = ids - rel_model.translatable_ids
            recs = rel_model.load_recs(
                odoo_instance,
                list(ids_to_load),
                dep=True,
                chunk_size=self._get_effective_batch_size(rel_model),
            )
            newly_loaded[rel_model_name] = recs
        self._load_dependencies_of_records(odoo_instance, newly_loaded, other_models, add_translations)
        for rel_model_name, ids in ignore_struct.items():
            logger.debug("Ignoring %s%s", rel_model_name, str(ids))

    def _prepare_model_domain(self, model: OdooModel, since) -> List[Tuple]:
        if model.no_domain:
            return []
        domain = list(model.domain)
        if since and not self.force_sync:
            domain.append(("write_date", ">", since))
        return domain

    def prepare(self) -> None:
        syncs = [
            (
                self.source.odoo,
                self.dest.odoo,
                self.source_timestamp,
                self.models,
                False,
                self.record_id_map_forward,
                self.models_by_name,
                self._add_translations,
            ),
            (
                self.dest.odoo,
                self.source.odoo,
                self.dest_timestamp,
                self.reverse_models,
                True,
                self.record_id_map_reverse,
                self.reverse_models_by_name,
                self._add_reverse_translations,
            ),
        ]
        for (
            odoo_env,
            dest_odoo_env,
            since,
            models,
            reverse,
            mapping,
            models_by_name,
            add_translations,
        ) in syncs:
            logger.info("-----------PREPARE %sSYNC----------", "REVERSE " if reverse else "")

            if self.options.get("sync_modules") and not reverse:
                logger.info("Syncing modules...")
                source_module = odoo_env.env["ir.module.module"]
                dest_module = dest_odoo_env.env["ir.module.module"]
                source_module_ids = source_module.search([("state", "=", "installed")])
                source_module_names = source_module.read(source_module_ids, ["name"])
                source_module_names = [r["name"] for r in source_module_names]
                dest_modules_ids = dest_module.search([
                    ("state", "!=", "installed"),
                    ("name", "in", source_module_names),
                ])
                dest_modules = dest_module.browse(dest_modules_ids)
                logger.info(
                    "Installing in dest modules %s...",
                    [r["name"] for r in dest_modules.read(["name"])],
                )
                try:
                    if not self.dry_run:
                        dest_modules.button_immediate_install()
                except odoorpc.error.RPCError as exc:
                    logger.error("Module installation failed: %s", str(exc))
                    raise

            for model in models:
                logger.info("Determine fields for model %s...", model.name)
                model.determine_fields(
                    odoo_env,
                    dest_odoo_env,
                    models,
                    mapping,
                    allow_external_m2o=self.auto_xmlid_lookup,
                )

            for model in models:
                if model.no_domain:
                    continue
                domain = self._prepare_model_domain(model, since)
                logger.info("Searching: %s %s", model.name, domain)
                odoo_env.context = model.context
                model_batch_size = self._get_effective_batch_size(model)
                limit = model_batch_size if model_batch_size else None
                offset = 0
                while True:
                    search_kwargs = {}
                    if limit:
                        search_kwargs.update({"offset": offset, "limit": limit})
                    ids = odoo_env.env[model.name].search(domain or [], **search_kwargs)
                    if not ids:
                        break
                    logger.debug(
                        "Found %s records for %s (offset %s)",
                        len(ids),
                        model.name,
                        offset,
                    )
                    model.load_recs(odoo_env, ids, chunk_size=model_batch_size)
                    if not limit:
                        break
                    offset += len(ids)

            translations = {m.name: m.record_ids for m in models}
            add_translations(translations)

            if self.sync_dependencies:
                loaded = {m.name: m.records for m in models}
                self._load_dependencies_of_records(odoo_env, loaded, models_by_name, add_translations)

            for model in models:
                model.sort_parents_before_children()

    def sync(self) -> None:
        logger.info("-----------NORMAL SYNC-----------")
        for model in self.models:
            self._sync_one_model(model)
        logger.info("-----------REVERSE SYNC----------")
        for model in self.reverse_models:
            self._sync_one_model(model)
        logger.info("-----------END-----------")

    def get_new_timestamps(self) -> Optional[dict]:
        if not self.dry_run:
            return {"source": self.source.timestamp, "target": self.dest.timestamp}
        return None


__all__ = ["ModelSyncer"]
