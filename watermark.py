"""
CODEMARK core: keyed, structure-level watermarking via AST equivalent-form choice.
v0.1 scope: augmented-assignment carriers only.
"""
import ast
import hashlib
import hmac


def hmac_bit(key: str, site_index: int) -> int:
    """Deterministic keyed bit for a given carrier site index."""
    digest = hmac.new(key.encode(), str(site_index).encode(), hashlib.sha256).digest()
    return digest[0] & 1


class AugAssignCarrier:
    """
    Represents one augmented-assignment carrier site.
    Form 0: x = x + e   (expanded)
    Form 1: x += e       (augmented)
    """
    SUPPORTED_OPS = {ast.Add: ast.Add, ast.Sub: ast.Sub, ast.Mult: ast.Mult}

    def __init__(self, node, node_type: str, parent, index_in_parent: int):
        self.node = node                    # the ast.Assign or ast.AugAssign node
        self.node_type = node_type          # "expanded" or "augmented"
        self.parent = parent                # containing body list owner
        self.index_in_parent = index_in_parent


def find_carriers(tree: ast.AST) -> list[AugAssignCarrier]:
    """
    Walk the AST and collect augmented-assignment carrier sites in source order.
    Matches:
      - Assign: `x = x + e` where target and left operand of BinOp are the same Name
      - AugAssign: `x += e` with op in {Add, Sub, Mult}
    """
    carriers = []

    class Visitor(ast.NodeVisitor):
        def visit_body(self, body_list):
            for i, stmt in enumerate(body_list):
                if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                    target = stmt.targets[0]
                    value = stmt.value
                    if (isinstance(target, ast.Name) and isinstance(value, ast.BinOp)
                            and type(value.op) in AugAssignCarrier.SUPPORTED_OPS
                            and isinstance(value.left, ast.Name)
                            and value.left.id == target.id):
                        carriers.append(AugAssignCarrier(stmt, "expanded", body_list, i))
                elif isinstance(stmt, ast.AugAssign) and type(stmt.op) in AugAssignCarrier.SUPPORTED_OPS:
                    carriers.append(AugAssignCarrier(stmt, "augmented", body_list, i))
                for child in ast.iter_child_nodes(stmt):
                    self.generic_visit(child)

        def generic_visit(self, node):
            for field, value in ast.iter_fields(node):
                if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                    self.visit_body(value)
                elif isinstance(value, ast.AST):
                    super().generic_visit(value)

    Visitor().visit_body(tree.body)
    return carriers


def carrier_state(carrier: AugAssignCarrier) -> int:
    """Current bit value of a carrier: 0 = expanded, 1 = augmented."""
    return 0 if carrier.node_type == "expanded" else 1


def flip_carrier(carrier: AugAssignCarrier) -> ast.AST:
    """Return a new node in the opposite form, preserving target/op/value."""
    if carrier.node_type == "expanded":
        target = carrier.node.targets[0]
        op = carrier.node.value.op
        value_expr = carrier.node.value.right
        new_node = ast.AugAssign(target=ast.Name(id=target.id, ctx=ast.Store()),
                                  op=op, value=value_expr)
    else:
        target_id = carrier.node.target.id
        op = carrier.node.op
        value_expr = carrier.node.value
        new_node = ast.Assign(
            targets=[ast.Name(id=target_id, ctx=ast.Store())],
            value=ast.BinOp(left=ast.Name(id=target_id, ctx=ast.Load()), op=op, right=value_expr)
        )
    return ast.copy_location(new_node, carrier.node)


def embed(source: str, key: str, payload_bits: list[int]) -> str:
    """
    Embed payload_bits (repeated across available carriers) into source.
    Returns watermarked source code as a string.
    """
    tree = ast.parse(source)
    carriers = find_carriers(tree)
    if not carriers:
        raise ValueError("No carrier sites found in source.")

    for j, carrier in enumerate(carriers):
        target_bit = payload_bits[j % len(payload_bits)] ^ hmac_bit(key, j)
        current_bit = carrier_state(carrier)
        if current_bit != target_bit:
            new_node = flip_carrier(carrier)
            carrier.parent[carrier.index_in_parent] = new_node

    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def detect(source: str, key: str, payload_len: int) -> tuple[list[int], int, int]:
    """
    Detect watermark. Returns (recovered_bits, matches, total_carriers).
    Blind recovery via majority vote per payload position.
    """
    tree = ast.parse(source)
    carriers = find_carriers(tree)

    if len(carriers) < payload_len:
        raise ValueError(
            f"Insufficient carriers ({len(carriers)}) for payload length ({payload_len}). "
            f"Need at least {payload_len} carriers for one full pass."
        )

    votes = [[0, 0] for _ in range(payload_len)]
    for j, carrier in enumerate(carriers):
        observed_bit = carrier_state(carrier) ^ hmac_bit(key, j)
        votes[j % payload_len][observed_bit] += 1

    recovered = [1 if v1 >= v0 else 0 for v0, v1 in votes]
    return recovered, len(carriers), len(carriers)

class KwargOrderCarrier:
    """
    Represents a function call with >=2 uniquely-named keyword arguments
    whose values are 'pure' (constants or names only — no side effects),
    excluding order-sensitive callees like dict().
    Form 0/1 encoded by whether kwargs are in original vs swapped adjacent order.
    """
    EXCLUDED_CALLEES = {"dict"}

    def __init__(self, node: ast.Call, parent, index_in_parent, field_name):
        self.node = node
        self.parent = parent
        self.index_in_parent = index_in_parent
        self.field_name = field_name  # for non-statement contexts, unused in v0.1


class EmptyCollectionCarrier:
    """
    {} / [] <-> dict() / list()
    Form 0: literal ({}, [])
    Form 1: constructor call (dict(), list())
    """
    def __init__(self, node, node_type: str, parent, index_in_parent):
        self.node = node
        self.node_type = node_type  # "literal" or "constructor"
        self.parent = parent
        self.index_in_parent = index_in_parent


def _is_pure_kwarg_value(value: ast.AST) -> bool:
    return isinstance(value, (ast.Constant, ast.Name))


def find_kwarg_carriers(body_list: list) -> list[KwargOrderCarrier]:
    carriers = []
    for i, stmt in enumerate(body_list):
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call) and len(node.keywords) >= 2:
                callee_name = getattr(node.func, "id", None)
                if callee_name in KwargOrderCarrier.EXCLUDED_CALLEES:
                    continue
                names = [kw.arg for kw in node.keywords]
                if None in names or len(set(names)) != len(names):
                    continue  # skip **kwargs or duplicate names
                if all(_is_pure_kwarg_value(kw.value) for kw in node.keywords):
                    carriers.append(KwargOrderCarrier(node, body_list, i, None))
    return carriers


def find_empty_collection_carriers(body_list: list) -> list[EmptyCollectionCarrier]:
    carriers = []
    for i, stmt in enumerate(body_list):
        for node in ast.walk(stmt):
            if isinstance(node, ast.Dict) and not node.keys:
                carriers.append(EmptyCollectionCarrier(node, "literal_dict", body_list, i))
            elif isinstance(node, ast.List) and not node.elts:
                carriers.append(EmptyCollectionCarrier(node, "literal_list", body_list, i))
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id == "dict" and not node.args and not node.keywords):
                carriers.append(EmptyCollectionCarrier(node, "constructor_dict", body_list, i))
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id == "list" and not node.args):
                carriers.append(EmptyCollectionCarrier(node, "constructor_list", body_list, i))
    return carriers