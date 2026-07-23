import pytest

from cooperative_clearing.modules.node.domain.node_code import NodeCode


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("node-01", "node-01"),
        (" North-Region-2 ", "north-region-2"),
        ("abc", "abc"),
    ],
)
def test_node_code_normalizes_valid_values(raw: str, expected: str) -> None:
    assert str(NodeCode(raw)) == expected


@pytest.mark.parametrize("raw", ["ab", "1-node", "node--one", "node_one", "node one"])
def test_node_code_rejects_ambiguous_values(raw: str) -> None:
    with pytest.raises(ValueError, match="invalid node code"):
        NodeCode(raw)
