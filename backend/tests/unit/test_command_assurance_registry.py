import ast
from pathlib import Path

from cooperative_clearing.modules.journal.domain.assurance import CRITICAL_EVENT_TYPES

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src" / "cooperative_clearing"
ASSURED_EVENT_WRAPPERS = frozenset(
    {"_append_reputation_event", "_append_participant_address_event"}
)


def _literal_strings(node: ast.AST) -> set[str]:
    return {
        value.value
        for value in ast.walk(node)
        if isinstance(value, ast.Constant) and isinstance(value.value, str)
    }


def test_every_registered_critical_event_call_site_supplies_assurance() -> None:
    discovered: set[str] = set()
    missing: list[str] = []
    assured_wrappers: set[str] = set()

    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for function in (
            item
            for item in ast.walk(tree)
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name in ASSURED_EVENT_WRAPPERS
        ):
            for call in ast.walk(function):
                if not isinstance(call, ast.Call):
                    continue
                if not isinstance(call.func, ast.Attribute) or call.func.attr != "append":
                    continue
                event_keyword = next(
                    (item for item in call.keywords if item.arg == "event_type"),
                    None,
                )
                assurance_keyword = next(
                    (item for item in call.keywords if item.arg == "assurance"),
                    None,
                )
                if (
                    event_keyword is not None
                    and isinstance(event_keyword.value, ast.Name)
                    and event_keyword.value.id == "event_type"
                    and assurance_keyword is not None
                ):
                    assured_wrappers.add(function.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in ASSURED_EVENT_WRAPPERS
            ):
                wrapper_event = next(
                    (item for item in node.keywords if item.arg == "event_type"),
                    None,
                )
                if wrapper_event is not None:
                    discovered.update(
                        _literal_strings(wrapper_event.value) & CRITICAL_EVENT_TYPES
                    )
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "append":
                continue
            event_keyword = next(
                (item for item in node.keywords if item.arg == "event_type"),
                None,
            )
            if event_keyword is None:
                continue
            critical = _literal_strings(event_keyword.value) & CRITICAL_EVENT_TYPES
            if not critical:
                continue
            discovered.update(critical)
            assurance_keyword = next(
                (item for item in node.keywords if item.arg == "assurance"),
                None,
            )
            if assurance_keyword is None or (
                isinstance(assurance_keyword.value, ast.Constant)
                and assurance_keyword.value.value is None
            ):
                for event_type in sorted(critical):
                    missing.append(f"{event_type} at {path.relative_to(SOURCE_ROOT)}:{node.lineno}")

    assert not missing, "Critical call sites without assurance:\n" + "\n".join(missing)
    assert assured_wrappers == ASSURED_EVENT_WRAPPERS
    assert discovered == CRITICAL_EVENT_TYPES
