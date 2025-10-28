from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

from ..core import get_logger

logger = get_logger(__name__)

INTERNAL_RUNTIME_FIELDS: Tuple[str, ...] = ("__sfit_dep",)

_VALUE_MAPPING_UNSET = object()

DEFAULT_EXCLUDED_FIELDS: List[str] = [
    "id",
    "__last_update",
    "create_date",
    "create_uid",
    "write_date",
    "write_uid",
]


class OdooModel:
    """Abstraction of an Odoo model."""

    def __init__(self, model_dict: dict):
        self.name: Optional[str] = model_dict.get("model")
        self.fields: List[str] = []
        self.many2onefields: Dict[str, str] = {}
        self.external_relation_fields: Set[str] = set()
        self.dest_fields: List[str] = []
        self.field_specs: Dict[str, dict] = {}
        self.records: List[dict] = []
        self.record_ids: Set[int] = set()
        self.domain = model_dict.get("domain", [])
        self.no_domain = model_dict.get("no_domain")
        self.context = model_dict.get("context", {})
        self.excluded_fields = set(model_dict.get("excluded_fields", [])).union(set(DEFAULT_EXCLUDED_FIELDS))
        self.included_fields = set(model_dict.get("included_fields", []))
        self.reverse = bool(model_dict.get("reverse"))
        self.trans: Dict[int, int] = {}
        self.translatable_ids: Set[int] = set()
        self.field_mappings: Dict[str, str] = self._load_field_mappings(model_dict)
        self.value_mappings = self._normalize_value_mappings(model_dict.get("value_mappings"))
        self.retry_on_create = self._parse_retry_on_create(model_dict.get("retry_on_create"))

    def load_recs(self, odoo, _ids: Iterable[int], dep: bool = False, chunk_size: Optional[int] = None) -> List[dict]:
        """Loads records into this model."""
        loaded: List[dict] = []
        id_list = list(_ids or [])
        if not id_list:
            return loaded

        if chunk_size and chunk_size > 0:
            logger.info(
                "Reading %s %s records from server in batches of up to %s...",
                len(id_list),
                self.name,
                chunk_size,
            )
            batches = [id_list[i : i + chunk_size] for i in range(0, len(id_list), chunk_size)]
        else:
            logger.info("Reading %s %s records from server...", len(id_list), self.name)
            batches = [id_list]

        source_obj = odoo.env[self.name]
        for batch in batches:
            try:
                records = source_obj.read(batch, self.fields)
            except Exception as exc:  # noqa: BLE001 - surface remote RPC errors
                logger.error(
                    "Failed to read batch containing %s records for %s: %s",
                    len(batch),
                    self.name,
                    exc,
                )
                continue

            if dep:
                for record in records:
                    record.update({"__sfit_dep": True})
            new_records = []
            for record in records:
                record_id = record.get("id")
                if record_id in self.record_ids:
                    logger.debug("Skipping duplicate %s[%s] already loaded", self.name, record_id)
                    continue
                new_records.append(record)
                self.record_ids.add(record_id)
            if new_records:
                self.records.extend(new_records)
                loaded.extend(new_records)
        return loaded

    def sort_parents_before_children(self) -> None:
        """Sort records so that parents are before children."""
        records = self.records
        if records and "parent_id" in records[0].keys():
            logger.info("Sorting %s according to parent-child hierarchy...", self.name)

            def _sort(todo, ids_done):
                done = []
                more_ids_done = []
                still_todo = []
                for _record in todo:
                    parent_id = _record.get("parent_id")
                    if not parent_id or parent_id in ids_done:
                        done.append(_record)
                        more_ids_done.append(_record["id"])
                    else:
                        still_todo.append(_record)
                if more_ids_done:
                    done.extend(_sort(still_todo, ids_done + more_ids_done))
                else:
                    done.extend(still_todo)
                return done

            sorted_records = _sort(records, [])
        else:
            sorted_records = records
        self.records = sorted_records

    def _load_field_mappings(self, model_dict: dict) -> Dict[str, str]:
        field_mappings = model_dict.get("field_mappings")
        legacy_field_mapping = model_dict.get("field_mapping")
        used_legacy_key = False

        if field_mappings is None:
            field_mappings = legacy_field_mapping or {}
            if legacy_field_mapping is not None:
                used_legacy_key = True
        elif legacy_field_mapping:
            logger.warning(
                "Ignoring legacy `field_mapping` for %s because `field_mappings` is provided.",
                self.name or "<unknown>",
            )

        if used_legacy_key:
            logger.warning(
                "Configuration key `field_mapping` for %s is deprecated; use `field_mappings` instead.",
                self.name or "<unknown>",
            )

        if field_mappings and not isinstance(field_mappings, dict):
            logger.warning(
                "Ignoring invalid field mappings for %s; expected a mapping but got %r.",
                self.name or "<unknown>",
                field_mappings,
            )
            return {}

        return dict(field_mappings or {})

    def _parse_retry_on_create(self, config: Optional[dict]) -> Optional[dict]:
        if not config:
            return None
        if not isinstance(config, dict):
            logger.warning(
                "Ignoring invalid retry_on_create configuration for %s; expected a mapping but got %r.",
                self.name or "<unknown>",
                config,
            )
            return None

        raw_fields = config.get("fields")
        if not isinstance(raw_fields, (list, tuple)):
            logger.warning(
                "Ignoring retry_on_create for %s; `fields` must be a list of field names.",
                self.name or "<unknown>",
            )
            return None

        fields: List[str] = []
        for entry in raw_fields:
            if isinstance(entry, str) and entry:
                fields.append(entry)
            else:
                logger.warning(
                    "Skipping non-string retry field %r for %s.",
                    entry,
                    self.name or "<unknown>",
                )

        if not fields:
            return None

        max_subset = config.get("max_subset")
        if max_subset is not None:
            try:
                max_subset = int(max_subset)
                if max_subset <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid max_subset %r for retry_on_create on %s; falling back to len(fields).",
                    config.get("max_subset"),
                    self.name or "<unknown>",
                )
                max_subset = None

        result = {
            "fields": fields,
        }
        if max_subset is not None:
            result["max_subset"] = max_subset
        return result

    def determine_fields(
        self,
        odoo,
        dest_odoo,
        other_models: List["OdooModel"],
        mapping: Dict[str, dict],
        allow_external_m2o: bool = False,
    ) -> None:
        """Determine which fields to sync for this model."""
        logger.debug("Determining which fields to sync for %s", self.name)
        source_ir_fields = odoo.env["ir.model.fields"]
        dest_ir_fields = dest_odoo.env["ir.model.fields"]
        fields_domain = [("model", "=", self.name)]
        if self.included_fields:
            fields_domain.append(("name", "in", list(self.included_fields)))

        self.fields = []
        self.dest_fields = []
        self.many2onefields = {}
        self.external_relation_fields = set()
        self.field_specs = {}

        field_ids = source_ir_fields.search(fields_domain)
        fields = source_ir_fields.read(field_ids, [])

        dest_fields_domain = [("model", "=", self.name)]
        dest_field_ids = dest_ir_fields.search(dest_fields_domain)
        dest_fields = dest_ir_fields.read(dest_field_ids, ["name", "ttype", "relation"])
        dest_field_by_name = {r["name"]: r for r in dest_fields}
        dest_field_names = set(dest_field_by_name.keys())

        other_model_names = {m.name for m in other_models}
        other_model_names = set(mapping.keys()).union(other_model_names)

        for field in fields:
            name = field.get("name")
            relation = field.get("relation")
            ttype = field.get("ttype")
            readonly = field.get("readonly")

            if readonly:
                continue
            if name in self.excluded_fields:
                continue
            if relation and ttype not in ("many2one",) and name not in self.field_mappings:
                continue

            target_name = self.field_mappings.get(name, name)

            if target_name not in dest_field_names:
                if target_name != name:
                    logger.warning(
                        "Field mapping %s[%s] -> %s skipped: destination field missing",
                        self.name,
                        name,
                        target_name,
                    )
                else:
                    logger.warning(
                        "Field %s[%s] does not exist on destination, consider mapping to another field",
                        self.name,
                        name,
                    )
                continue

            dest_field_info = dest_field_by_name[target_name]
            dest_ttype = dest_field_info.get("ttype")
            dest_relation = dest_field_info.get("relation")

            if relation and ttype == "many2one":
                if relation not in other_model_names and not allow_external_m2o:
                    continue
                self.many2onefields[name] = relation
                if relation not in other_model_names and allow_external_m2o:
                    self.external_relation_fields.add(name)

            self.fields.append(name)
            if target_name not in self.dest_fields:
                self.dest_fields.append(target_name)
            self.field_specs[name] = {
                "dest_field": target_name,
                "source_type": ttype,
                "source_relation": relation,
                "dest_type": dest_ttype,
                "dest_relation": dest_relation,
            }

        if "id" not in self.fields:
            self.fields.append("id")
        if "id" not in self.dest_fields:
            self.dest_fields.insert(0, "id")
        logger.debug("Source fields: %s", self.fields)
        logger.debug("Destination fields: %s", self.dest_fields)

    def _map_fields(self, data: dict, find_dest_id_function):
        mapped = {}
        for source_field in self.fields:
            if source_field in INTERNAL_RUNTIME_FIELDS or source_field == "id":
                continue
            if source_field not in data:
                continue
            if source_field.startswith("__sfit_"):
                continue

            spec = self.field_specs.get(source_field)
            dest_field = spec.get("dest_field") if spec else source_field
            value = data.get(source_field)

            preprocessed_value = self._apply_value_mapping(source_field, value)
            converted, ok, message = self._convert_field_value(
                source_field, dest_field, spec, preprocessed_value, find_dest_id_function
            )

            if ok:
                if message:
                    logger.warning(message)
                mapped[dest_field] = converted
            else:
                logger.warning(
                    "Skipping field mapping %s[%s] -> %s: %s",
                    self.name,
                    source_field,
                    dest_field,
                    message,
                )
        return mapped

    def _convert_field_value(self, source_field, dest_field, spec, value, find_dest_id_function):
        if spec is None:
            return value, True, None

        source_type = spec.get("source_type")
        dest_type = spec.get("dest_type")
        source_relation = spec.get("source_relation")
        dest_relation = spec.get("dest_relation")

        if value in (None, False):
            return None, True, None

        if source_type == dest_type and dest_type != "many2one":
            return value, True, None

        if dest_type == "many2one":
            if source_type != "many2one":
                return None, False, f"cannot convert {source_type} to many2one"
            source_rel_id = None
            if isinstance(value, (list, tuple)) and value:
                source_rel_id = value[0]
            elif isinstance(value, int):
                source_rel_id = value
            if not source_rel_id:
                return None, True, None
            rel_model = dest_relation or source_relation
            if not rel_model:
                return None, False, "missing relation metadata for many2one field"
            dest_id = find_dest_id_function(rel_model, source_rel_id)
            if not dest_id:
                return None, True, f"Mapping failed: consider adding manual mapping for record {rel_model}[{source_rel_id}]"
            return dest_id, True, None

        if source_type == "many2one" and dest_type in {"char", "text", "html", "selection"}:
            if isinstance(value, (list, tuple)) and len(value) > 1:
                return value[1], True, None
            return None, True, None

        if dest_type in {"char", "text", "html", "selection"}:
            if source_type in {"char", "text", "html", "selection", "date", "datetime"}:
                return str(value) if value is not None else None, True, None
            if source_type in {"integer"}:
                return str(int(value)), True, None
            if source_type in {"float", "monetary"}:
                return str(float(value)), True, None
            if source_type == "boolean":
                return "True" if bool(value) else "False", True, None
            return None, False, f"cannot convert {source_type} to {dest_type}"

        if dest_type == "boolean":
            if source_type == "boolean":
                return bool(value), True, None
            if source_type in {"integer", "float", "monetary"}:
                return bool(value), True, None
            if source_type in {"char", "text", "html", "selection"}:
                text = str(value).strip().lower()
                if text in ("1", "true", "t", "yes", "y"):
                    return True, True, None
                if text in ("0", "false", "f", "no", "n", ""):
                    return False, True, None
                return None, False, f"cannot convert string '{value}' to boolean"
            return None, False, f"cannot convert {source_type} to boolean"

        if dest_type == "integer":
            if source_type == "integer":
                return int(value), True, None
            if source_type in {"float", "monetary"}:
                return int(value), True, None
            if source_type == "boolean":
                return 1 if bool(value) else 0, True, None
            if source_type in {"char", "text", "html", "selection"}:
                try:
                    return int(float(str(value).strip() or 0)), True, None
                except ValueError:
                    return None, False, f"cannot parse '{value}' as integer"
            return None, False, f"cannot convert {source_type} to integer"

        if dest_type in {"float", "monetary"}:
            if source_type in {"float", "monetary"}:
                return float(value), True, None
            if source_type == "integer":
                return float(value), True, None
            if source_type == "boolean":
                return 1.0 if bool(value) else 0.0, True, None
            if source_type in {"char", "text", "html", "selection"}:
                try:
                    return float(str(value).strip()) if str(value).strip() else 0.0, True, None
                except ValueError:
                    return None, False, f"cannot parse '{value}' as float"
            return None, False, f"cannot convert {source_type} to float"

        if dest_type in {"date", "datetime"}:
            if source_type in {"date", "datetime", "char", "text", "html", "selection"}:
                return str(value), True, None
            return None, False, f"cannot convert {source_type} to {dest_type}"

        if dest_type in {"one2many", "many2many"}:
            if source_type == dest_type:
                return value, True, None
            return None, False, f"cannot convert {source_type} to {dest_type}"

        if source_type == dest_type:
            return value, True, None

        return None, False, f"cannot convert {source_type} to {dest_type}"

    def _normalize_value_mappings(self, raw_mappings: Optional[dict]) -> Dict[str, dict]:
        normalized: Dict[str, dict] = {}
        if not raw_mappings:
            return normalized
        for field_name, config in raw_mappings.items():
            if isinstance(config, dict):
                # Extract flags
                case_insensitive = bool(config.get("case_insensitive") or config.get("__case_insensitive__"))
                default = _VALUE_MAPPING_UNSET
                if "default" in config:
                    default = config["default"]
                elif "__default__" in config:
                    default = config["__default__"]

                values = config.get("values")
                if values is None:
                    reserved_keys = {"values", "default", "__default__", "case_insensitive", "__case_insensitive__"}
                    values = {k: v for k, v in config.items() if k not in reserved_keys}

                value_map: Dict[object, object] = {}
                if isinstance(values, dict):
                    for raw_key, mapped_value in values.items():
                        key = raw_key
                        if case_insensitive and isinstance(raw_key, str):
                            key = raw_key.lower()
                        value_map[key] = mapped_value

                normalized[field_name] = {
                    "values": value_map,
                    "default": default,
                    "case_insensitive": case_insensitive,
                }
            else:
                normalized[field_name] = {"constant": config}
        return normalized

    def _apply_value_mapping(self, source_field: str, value):
        mapping_config = self.value_mappings.get(source_field)
        if not mapping_config:
            return value

        if "constant" in mapping_config:
            return mapping_config["constant"]

        case_insensitive = mapping_config.get("case_insensitive", False)
        lookup_value = value
        if case_insensitive and isinstance(value, str):
            lookup_value = value.lower()

        values_map: Dict[object, object] = mapping_config.get("values", {})
        if lookup_value in values_map:
            return values_map[lookup_value]

        default_value = mapping_config.get("default", _VALUE_MAPPING_UNSET)
        if default_value is not _VALUE_MAPPING_UNSET:
            return default_value

        return value


__all__ = ["OdooModel", "INTERNAL_RUNTIME_FIELDS", "DEFAULT_EXCLUDED_FIELDS"]
