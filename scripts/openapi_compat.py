#!/usr/bin/env python3
"""Fail-closed OpenAPI compatibility gate for offline releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Literal

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
REQUEST = "request"
RESPONSE = "response"
Position = Literal["request", "response"]


class ContractError(RuntimeError):
    """The supplied contract cannot be checked safely."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_document(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"Cannot read OpenAPI document {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"OpenAPI document must be an object: {path}")
    if not isinstance(value.get("openapi"), str) or not isinstance(value.get("paths"), dict):
        raise ContractError(f"OpenAPI document is missing openapi/paths: {path}")
    return value


def object_value(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


class CompatibilityChecker:
    def __init__(self, baseline: dict[str, Any], current: dict[str, Any]) -> None:
        self.baseline = baseline
        self.current = current
        self.issues: list[dict[str, str]] = []

    def add(self, code: str, location: str, detail: str) -> None:
        self.issues.append({"code": code, "location": location, "detail": detail})

    @staticmethod
    def resolve(document: dict[str, Any], value: object) -> object:
        if not isinstance(value, dict) or set(value) != {"$ref"}:
            return value
        reference = value.get("$ref")
        if not isinstance(reference, str) or not reference.startswith("#/"):
            raise ContractError(f"Only local OpenAPI references are supported: {reference!r}")
        current: object = document
        for token in reference[2:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, dict) or token not in current:
                raise ContractError(f"OpenAPI reference does not resolve: {reference}")
            current = current[token]
        return current

    def resolved_object(
        self, document: dict[str, Any], value: object, label: str
    ) -> dict[str, Any]:
        return object_value(self.resolve(document, value), label)

    @staticmethod
    def parameter_key(parameter: dict[str, Any]) -> tuple[str, str]:
        location = parameter.get("in")
        name = parameter.get("name")
        if not isinstance(location, str) or not isinstance(name, str):
            raise ContractError("Every OpenAPI parameter must have string name and in")
        return location, name

    def parameters(
        self,
        document: dict[str, Any],
        path_item: dict[str, Any],
        operation: dict[str, Any],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for owner in (path_item, operation):
            raw = owner.get("parameters", [])
            if not isinstance(raw, list):
                raise ContractError("OpenAPI parameters must be an array")
            for item in raw:
                parameter = self.resolved_object(document, item, "parameter")
                result[self.parameter_key(parameter)] = parameter
        return result

    @staticmethod
    def schema_types(schema: dict[str, Any]) -> set[str]:
        raw = schema.get("type")
        if isinstance(raw, str):
            return {raw}
        if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
            return set(raw)
        return set()

    def compare_schema(
        self,
        baseline_value: object,
        current_value: object,
        location: str,
        position: Position,
        seen: set[tuple[str, str, Position]] | None = None,
    ) -> None:
        values_are_references = (isinstance(baseline_value, dict) and "$ref" in baseline_value) or (
            isinstance(current_value, dict) and "$ref" in current_value
        )
        if baseline_value == current_value and not values_are_references:
            return
        seen = set() if seen is None else seen
        baseline_ref = baseline_value.get("$ref") if isinstance(baseline_value, dict) else None
        current_ref = current_value.get("$ref") if isinstance(current_value, dict) else None
        if isinstance(baseline_ref, str) or isinstance(current_ref, str):
            key = (str(baseline_ref), str(current_ref), position)
            if key in seen:
                return
            seen.add(key)

        baseline = self.resolved_object(
            self.baseline,
            baseline_value,
            f"{location} baseline schema",
        )
        current = self.resolved_object(
            self.current,
            current_value,
            f"{location} current schema",
        )
        if baseline == current:
            return

        old_types = self.schema_types(baseline)
        new_types = self.schema_types(current)
        if old_types or new_types:
            compatible_types = (
                old_types <= new_types if position == REQUEST else new_types <= old_types
            )
            if not compatible_types:
                self.add(
                    "schema_type_changed",
                    location,
                    f"{position} types changed from {sorted(old_types)} to {sorted(new_types)}",
                )

        old_enum = baseline.get("enum")
        new_enum = current.get("enum")
        if isinstance(old_enum, list) and isinstance(new_enum, list):
            old_values = {json.dumps(item, sort_keys=True) for item in old_enum}
            new_values = {json.dumps(item, sort_keys=True) for item in new_enum}
            compatible_enum = (
                old_values <= new_values if position == REQUEST else new_values <= old_values
            )
            if not compatible_enum:
                code = "request_enum_narrowed" if position == REQUEST else "response_enum_expanded"
                self.add(
                    code,
                    location,
                    f"{position} enum changed incompatibly",
                )
        elif old_enum != new_enum and (old_enum is not None or new_enum is not None):
            self.add(
                "schema_enum_changed",
                location,
                f"{position} enum constraint changed",
            )

        old_properties = baseline.get("properties", {})
        new_properties = current.get("properties", {})
        if not isinstance(old_properties, dict) or not isinstance(new_properties, dict):
            raise ContractError(f"Schema properties must be objects at {location}")
        for name in sorted(old_properties.keys() - new_properties.keys()):
            self.add(
                "schema_property_removed",
                f"{location}/properties/{name}",
                "property was removed",
            )

        old_required = baseline.get("required", [])
        new_required = current.get("required", [])
        if not isinstance(old_required, list) or not isinstance(new_required, list):
            raise ContractError(f"Schema required must be an array at {location}")
        old_required_set = {str(item) for item in old_required}
        new_required_set = {str(item) for item in new_required}
        if position == REQUEST:
            for name in sorted(new_required_set - old_required_set):
                self.add(
                    "request_property_became_required",
                    f"{location}/required/{name}",
                    "new clients must send this property",
                )
        else:
            for name in sorted(old_required_set - new_required_set):
                self.add(
                    "response_property_no_longer_required",
                    f"{location}/required/{name}",
                    "clients can no longer rely on this property",
                )

        for name in sorted(old_properties.keys() & new_properties.keys()):
            self.compare_schema(
                old_properties[name],
                new_properties[name],
                f"{location}/properties/{name}",
                position,
                seen,
            )

        if "items" in baseline:
            if "items" not in current:
                self.add(
                    "array_items_removed",
                    f"{location}/items",
                    "array item schema was removed",
                )
            else:
                self.compare_schema(
                    baseline["items"],
                    current["items"],
                    f"{location}/items",
                    position,
                    seen,
                )

        constraints = (
            (
                "minimum",
                lambda old, new: new > old,
                lambda old, new: new < old,
            ),
            (
                "exclusiveMinimum",
                lambda old, new: new > old,
                lambda old, new: new < old,
            ),
            (
                "minLength",
                lambda old, new: new > old,
                lambda old, new: new < old,
            ),
            (
                "minItems",
                lambda old, new: new > old,
                lambda old, new: new < old,
            ),
            (
                "maximum",
                lambda old, new: new < old,
                lambda old, new: new > old,
            ),
            (
                "exclusiveMaximum",
                lambda old, new: new < old,
                lambda old, new: new > old,
            ),
            (
                "maxLength",
                lambda old, new: new < old,
                lambda old, new: new > old,
            ),
            (
                "maxItems",
                lambda old, new: new < old,
                lambda old, new: new > old,
            ),
        )
        for keyword, request_breaks_when, response_breaks_when in constraints:
            old = baseline.get(keyword)
            new = current.get(keyword)
            if isinstance(old, (int, float)) and isinstance(new, (int, float)):
                predicate = request_breaks_when if position == REQUEST else response_breaks_when
                if predicate(old, new):
                    self.add(
                        "schema_constraint_tightened",
                        f"{location}/{keyword}",
                        f"{keyword} changed from {old} to {new}",
                    )

        for keyword in (
            "allOf",
            "anyOf",
            "oneOf",
            "not",
            "discriminator",
        ):
            if baseline.get(keyword) != current.get(keyword):
                self.add(
                    "schema_composition_changed",
                    f"{location}/{keyword}",
                    f"{position} schema composition changed",
                )

    def compare_content(
        self,
        baseline: object,
        current: object,
        location: str,
        position: Position,
    ) -> None:
        old_content = object_value(baseline, f"{location} baseline content")
        new_content = object_value(current, f"{location} current content")
        for media_type in sorted(old_content.keys() - new_content.keys()):
            self.add(
                "media_type_removed",
                f"{location}/{media_type}",
                "media type was removed",
            )
        for media_type in sorted(old_content.keys() & new_content.keys()):
            old_media = object_value(
                old_content[media_type],
                f"{location}/{media_type} baseline",
            )
            new_media = object_value(
                new_content[media_type],
                f"{location}/{media_type} current",
            )
            if "schema" in old_media:
                if "schema" not in new_media:
                    self.add(
                        "schema_removed",
                        f"{location}/{media_type}/schema",
                        "schema was removed",
                    )
                else:
                    self.compare_schema(
                        old_media["schema"],
                        new_media["schema"],
                        f"{location}/{media_type}/schema",
                        position,
                    )

    def compare_operation(
        self,
        path: str,
        method: str,
        baseline_path: dict[str, Any],
        current_path: dict[str, Any],
        baseline_operation: dict[str, Any],
        current_operation: dict[str, Any],
    ) -> None:
        location = f"paths/{path}/{method}"
        old_operation_id = baseline_operation.get("operationId")
        new_operation_id = current_operation.get("operationId")
        if old_operation_id != new_operation_id:
            self.add(
                "operation_id_changed",
                f"{location}/operationId",
                f"changed from {old_operation_id!r} to {new_operation_id!r}",
            )

        old_parameters = self.parameters(self.baseline, baseline_path, baseline_operation)
        new_parameters = self.parameters(self.current, current_path, current_operation)
        for key in sorted(old_parameters.keys() - new_parameters.keys()):
            self.add(
                "parameter_removed",
                f"{location}/parameters/{key[0]}/{key[1]}",
                "accepted parameter was removed",
            )
        for key in sorted(new_parameters.keys() - old_parameters.keys()):
            if new_parameters[key].get("required") is True:
                self.add(
                    "required_parameter_added",
                    f"{location}/parameters/{key[0]}/{key[1]}",
                    "new required parameter was added",
                )
        for key in sorted(old_parameters.keys() & new_parameters.keys()):
            old_parameter = old_parameters[key]
            new_parameter = new_parameters[key]
            if old_parameter.get("required") is not True and new_parameter.get("required") is True:
                self.add(
                    "parameter_became_required",
                    f"{location}/parameters/{key[0]}/{key[1]}",
                    "optional parameter became required",
                )
            if "schema" in old_parameter and "schema" in new_parameter:
                self.compare_schema(
                    old_parameter["schema"],
                    new_parameter["schema"],
                    f"{location}/parameters/{key[0]}/{key[1]}/schema",
                    REQUEST,
                )

        old_body = baseline_operation.get("requestBody")
        new_body = current_operation.get("requestBody")
        if old_body is None and new_body is not None:
            resolved = self.resolved_object(
                self.current,
                new_body,
                f"{location} request body",
            )
            if resolved.get("required") is True:
                self.add(
                    "required_request_body_added",
                    f"{location}/requestBody",
                    "required request body was added",
                )
        elif old_body is not None and new_body is None:
            self.add(
                "request_body_removed",
                f"{location}/requestBody",
                "accepted request body was removed",
            )
        elif old_body is not None and new_body is not None:
            old_resolved = self.resolved_object(
                self.baseline,
                old_body,
                f"{location} baseline request body",
            )
            new_resolved = self.resolved_object(
                self.current,
                new_body,
                f"{location} current request body",
            )
            if old_resolved.get("required") is not True and new_resolved.get("required") is True:
                self.add(
                    "request_body_became_required",
                    f"{location}/requestBody",
                    "optional request body became required",
                )
            self.compare_content(
                old_resolved.get("content", {}),
                new_resolved.get("content", {}),
                f"{location}/requestBody/content",
                REQUEST,
            )

        old_responses = object_value(
            baseline_operation.get("responses", {}),
            f"{location} baseline responses",
        )
        new_responses = object_value(
            current_operation.get("responses", {}),
            f"{location} current responses",
        )
        for status in sorted(old_responses.keys() - new_responses.keys()):
            self.add(
                "response_removed",
                f"{location}/responses/{status}",
                "documented response was removed",
            )
        for status in sorted(old_responses.keys() & new_responses.keys()):
            old_response = self.resolved_object(
                self.baseline,
                old_responses[status],
                f"{location} baseline response {status}",
            )
            new_response = self.resolved_object(
                self.current,
                new_responses[status],
                f"{location} current response {status}",
            )
            self.compare_content(
                old_response.get("content", {}),
                new_response.get("content", {}),
                f"{location}/responses/{status}/content",
                RESPONSE,
            )

    def compare(self) -> list[dict[str, str]]:
        baseline_paths = object_value(self.baseline.get("paths"), "baseline paths")
        current_paths = object_value(self.current.get("paths"), "current paths")
        for path in sorted(baseline_paths.keys() - current_paths.keys()):
            self.add(
                "path_removed",
                f"paths/{path}",
                "path was removed",
            )
        for path in sorted(baseline_paths.keys() & current_paths.keys()):
            old_path = self.resolved_object(
                self.baseline,
                baseline_paths[path],
                f"baseline path {path}",
            )
            new_path = self.resolved_object(
                self.current,
                current_paths[path],
                f"current path {path}",
            )
            old_methods = {key for key in old_path if key.lower() in HTTP_METHODS}
            new_methods = {key for key in new_path if key.lower() in HTTP_METHODS}
            for method in sorted(old_methods - new_methods):
                self.add(
                    "operation_removed",
                    f"paths/{path}/{method}",
                    "operation was removed",
                )
            for method in sorted(old_methods & new_methods):
                old_operation = object_value(
                    old_path[method],
                    f"baseline operation {path} {method}",
                )
                new_operation = object_value(
                    new_path[method],
                    f"current operation {path} {method}",
                )
                self.compare_operation(
                    path,
                    method,
                    old_path,
                    new_path,
                    old_operation,
                    new_operation,
                )
        return self.issues


def count_operations(document: dict[str, Any]) -> int:
    paths = object_value(document.get("paths"), "paths")
    return sum(
        1
        for path_item in paths.values()
        if isinstance(path_item, dict)
        for method in path_item
        if method.lower() in HTTP_METHODS
    )


def build_report(
    baseline_path: Path,
    current_path: Path,
    mirror_path: Path | None,
) -> dict[str, Any]:
    baseline = load_document(baseline_path)
    current = load_document(current_path)
    issues = CompatibilityChecker(baseline, current).compare()
    mirror_matches = None
    mirror_digest = None
    if mirror_path is not None:
        mirror_digest = sha256_file(mirror_path)
        mirror_matches = current_path.read_bytes() == mirror_path.read_bytes()
        if not mirror_matches:
            issues.append(
                {
                    "code": "contract_mirror_mismatch",
                    "location": str(mirror_path),
                    "detail": (
                        "frontend OpenAPI mirror differs byte-for-byte from backend contract"
                    ),
                }
            )
    issues.sort(
        key=lambda item: (
            item["location"],
            item["code"],
            item["detail"],
        )
    )
    baseline_info = object_value(baseline.get("info", {}), "baseline info")
    current_info = object_value(current.get("info", {}), "current info")
    return {
        "format": "cooperative-clearing-openapi-compatibility-v1",
        "status": "compatible" if not issues else "incompatible",
        "baseline": {
            "path": str(baseline_path),
            "sha256": sha256_file(baseline_path),
            "version": baseline_info.get("version"),
            "operations": count_operations(baseline),
        },
        "current": {
            "path": str(current_path),
            "sha256": sha256_file(current_path),
            "version": current_info.get("version"),
            "operations": count_operations(current),
        },
        "mirror": (
            None
            if mirror_path is None
            else {
                "path": str(mirror_path),
                "sha256": mirror_digest,
                "exact_match": mirror_matches,
            }
        ),
        "issue_count": len(issues),
        "issues": issues,
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--baseline", type=Path, required=True)
    value.add_argument("--current", type=Path, required=True)
    value.add_argument("--mirror", type=Path)
    value.add_argument("--report", type=Path)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = build_report(
            args.baseline,
            args.current,
            args.mirror,
        )
        rendered = (
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(rendered, encoding="utf-8")
        sys.stdout.write(rendered)
        return 0 if report["status"] == "compatible" else 2
    except ContractError as exc:
        print(
            f"OpenAPI compatibility check failed: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
