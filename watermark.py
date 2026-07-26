"""
CODEMARK core: keyed, structure-level watermarking via AST equivalent-form choice.
v0.1 scope: augmented-assignment + empty-collection carriers.
"""
import ast
import hashlib
import hmac
from typing import Protocol


def hmac_bit(key: str, site_index: int) -> int:
    digest = hmac.new(key.encode(), str(site_index).encode(), hashlib.sha256).digest()
    return digest[0] & 1


class Carrier(Protocol):
    def state(self) -> int: ...
    def flip(self) -> ast.AST: ...
    def position(self) -> tuple[int, int]: ...


def _iter_with_parent(tree: ast.AST):
    """
    Yield (child_node, parent_node, field_name, index) for every AST node.
    index is None for single-value fields, an int for list-valued fields.
    This is the source of truth for how to correctly replace any node in place.
    """
    for parent in ast.walk(tree):
        for field_name, value in ast.iter_fields(parent):
            if isinstance(value, list):
                for idx, item in enumerate(value):
                    if isinstance(item, ast.AST):
                        yield item, parent, field_name, idx
            elif isinstance(value, ast.AST):
                yield value, parent, field_name, None


def _apply_replacement(carrier, new_node: ast.AST) -> None:
    """Replace a carrier's node with new_node at its correct field/index."""
    if carrier.index is None:
        setattr(carrier.parent_node, carrier.field_name, new_node)
    else:
        getattr(carrier.parent_node, carrier.field_name)[carrier.index] = new_node


class AugAssignCarrier:
    SUPPORTED_OPS = {ast.Add: ast.Add, ast.Sub: ast.Sub, ast.Mult: ast.Mult}

    def __init__(self, node, node_type: str, parent_node, field_name: str, index):
        self.node = node
        self.node_type = node_type  # "expanded" or "augmented"
        self.parent_node = parent_node
        self.field_name = field_name
        self.index = index

    def state(self) -> int:
        return 0 if self.node_type == "expanded" else 1

    def position(self) -> tuple[int, int]:
        return (self.node.lineno, self.node.col_offset)

    def flip(self) -> ast.AST:
        if self.node_type == "expanded":
            target = self.node.targets[0]
            op = self.node.value.op
            value_expr = self.node.value.right
            new_node = ast.AugAssign(target=ast.Name(id=target.id, ctx=ast.Store()),
                                      op=op, value=value_expr)
        else:
            target_id = self.node.target.id
            op = self.node.op
            value_expr = self.node.value
            new_node = ast.Assign(
                targets=[ast.Name(id=target_id, ctx=ast.Store())],
                value=ast.BinOp(left=ast.Name(id=target_id, ctx=ast.Load()), op=op, right=value_expr)
            )
        return ast.copy_location(new_node, self.node)


class EmptyCollectionCarrier:
    def __init__(self, node, node_type: str, parent_node, field_name: str, index):
        self.node = node
        self.node_type = node_type  # literal_dict, literal_list, constructor_dict, constructor_list
        self.parent_node = parent_node
        self.field_name = field_name
        self.index = index

    def state(self) -> int:
        return 0 if self.node_type.startswith("literal") else 1

    def position(self) -> tuple[int, int]:
        return (self.node.lineno, self.node.col_offset)

    def flip(self) -> ast.AST:
        is_dict = "dict" in self.node_type
        if self.node_type.startswith("literal"):
            new_node = ast.Call(func=ast.Name(id="dict" if is_dict else "list", ctx=ast.Load()),
                                 args=[], keywords=[])
        else:
            new_node = ast.Dict(keys=[], values=[]) if is_dict else ast.List(elts=[], ctx=ast.Load())
        return ast.copy_location(new_node, self.node)


def find_carriers(tree: ast.AST) -> list:
    """Discover all carrier types with correct parent tracking, sorted by source position."""
    aug_carriers = []
    empty_carriers = []

    for child, parent, field_name, index in _iter_with_parent(tree):
        if isinstance(child, ast.Assign) and len(child.targets) == 1:
            target = child.targets[0]
            value = child.value
            if (isinstance(target, ast.Name) and isinstance(value, ast.BinOp)
                    and type(value.op) in AugAssignCarrier.SUPPORTED_OPS
                    and isinstance(value.left, ast.Name)
                    and value.left.id == target.id):
                aug_carriers.append(AugAssignCarrier(child, "expanded", parent, field_name, index))
        elif isinstance(child, ast.AugAssign) and type(child.op) in AugAssignCarrier.SUPPORTED_OPS:
            aug_carriers.append(AugAssignCarrier(child, "augmented", parent, field_name, index))
        elif isinstance(child, ast.Dict) and not child.keys:
            empty_carriers.append(EmptyCollectionCarrier(child, "literal_dict", parent, field_name, index))
        elif isinstance(child, ast.List) and not child.elts:
            empty_carriers.append(EmptyCollectionCarrier(child, "literal_list", parent, field_name, index))
        elif (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
              and child.func.id == "dict" and not child.args and not child.keywords):
            empty_carriers.append(EmptyCollectionCarrier(child, "constructor_dict", parent, field_name, index))
        elif (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
              and child.func.id == "list" and not child.args):
            empty_carriers.append(EmptyCollectionCarrier(child, "constructor_list", parent, field_name, index))

    all_carriers = aug_carriers + empty_carriers
    all_carriers.sort(key=lambda c: c.position())
    return all_carriers


def embed(source: str, key: str, payload_bits: list[int]) -> str:
    tree = ast.parse(source)
    carriers = find_carriers(tree)
    if not carriers:
        raise ValueError("No carrier sites found in source.")

    for j, carrier in enumerate(carriers):
        target_bit = payload_bits[j % len(payload_bits)] ^ hmac_bit(key, j)
        if carrier.state() != target_bit:
            new_node = carrier.flip()
            _apply_replacement(carrier, new_node)

    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def detect(source: str, key: str, payload_len: int) -> tuple[list[int], int, int]:
    tree = ast.parse(source)
    carriers = find_carriers(tree)

    if len(carriers) < payload_len:
        raise ValueError(
            f"Insufficient carriers ({len(carriers)}) for payload length ({payload_len})."
        )

    votes = [[0, 0] for _ in range(payload_len)]
    for j, carrier in enumerate(carriers):
        observed_bit = carrier.state() ^ hmac_bit(key, j)
        votes[j % payload_len][observed_bit] += 1

    recovered = [1 if v1 >= v0 else 0 for v0, v1 in votes]
    return recovered, len(carriers), len(carriers)