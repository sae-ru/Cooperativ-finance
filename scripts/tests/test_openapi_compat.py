from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "openapi_compat.py"
SPEC = importlib.util.spec_from_file_location("openapi_compat", SCRIPT)
assert SPEC and SPEC.loader
openapi_compat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(openapi_compat)


def contract() -> dict[str, object]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "Contract", "version": "1.0.0"},
        "paths": {
            "/items/{item_id}": {
                "get": {
                    "operationId": "get_item",
                    "parameters": [
                        {
                            "name": "item_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "mode",
                            "in": "query",
                            "required": False,
                            "schema": {
                                "type": "string",
                                "enum": ["A", "B"],
                            },
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": ("#/components/schemas/Item")}
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Item": {
                    "type": "object",
                    "required": ["id", "status"],
                    "properties": {
                        "id": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["OPEN", "CLOSED"],
                        },
                    },
                }
            }
        },
    }


class OpenApiCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="openapi-compat-")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, name: str, value: object) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(value, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def report(
        self,
        current: dict[str, object],
        *,
        mirror: bool = True,
    ) -> dict[str, object]:
        baseline_path = self.write("baseline.json", contract())
        current_path = self.write("current.json", current)
        mirror_path = self.write("mirror.json", current) if mirror else None
        return openapi_compat.build_report(
            baseline_path,
            current_path,
            mirror_path,
        )

    def test_identical_contract_and_mirror_are_compatible(self) -> None:
        report = self.report(contract())
        self.assertEqual(report["status"], "compatible")
        self.assertEqual(report["issue_count"], 0)
        self.assertTrue(report["mirror"]["exact_match"])

    def test_removed_operation_is_rejected(self) -> None:
        current = contract()
        current["paths"]["/items/{item_id}"].pop("get")
        report = self.report(current)
        self.assertEqual(report["status"], "incompatible")
        self.assertIn(
            "operation_removed",
            {item["code"] for item in report["issues"]},
        )

    def test_added_required_parameter_is_rejected(self) -> None:
        current = contract()
        current["paths"]["/items/{item_id}"]["get"]["parameters"].append(
            {
                "name": "scope",
                "in": "query",
                "required": True,
                "schema": {"type": "string"},
            }
        )
        report = self.report(current)
        self.assertIn(
            "required_parameter_added",
            {item["code"] for item in report["issues"]},
        )

    def test_narrowed_request_enum_is_rejected(self) -> None:
        current = contract()
        current["paths"]["/items/{item_id}"]["get"]["parameters"][1]["schema"]["enum"] = ["A"]
        report = self.report(current)
        self.assertIn(
            "request_enum_narrowed",
            {item["code"] for item in report["issues"]},
        )

    def test_weakened_response_requirement_is_rejected(self) -> None:
        current = contract()
        current["components"]["schemas"]["Item"]["required"] = ["id"]
        report = self.report(current)
        self.assertIn(
            "response_property_no_longer_required",
            {item["code"] for item in report["issues"]},
        )

    def test_frontend_mirror_mismatch_is_rejected(self) -> None:
        baseline_path = self.write("baseline.json", contract())
        current_path = self.write("current.json", contract())
        mirror = copy.deepcopy(contract())
        mirror["info"]["title"] = "Different bytes"
        mirror_path = self.write("mirror.json", mirror)
        report = openapi_compat.build_report(
            baseline_path,
            current_path,
            mirror_path,
        )
        self.assertIn(
            "contract_mirror_mismatch",
            {item["code"] for item in report["issues"]},
        )

    def test_invalid_reference_fails_closed(self) -> None:
        current = contract()
        current["paths"]["/items/{item_id}"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"] = {"$ref": "#/components/schemas/Missing"}
        baseline_path = self.write("baseline.json", contract())
        current_path = self.write("current.json", current)
        with self.assertRaises(openapi_compat.ContractError):
            openapi_compat.build_report(
                baseline_path,
                current_path,
                None,
            )


if __name__ == "__main__":
    unittest.main()
