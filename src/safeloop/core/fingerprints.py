"""Content hashing (SHA-256) and AST structural fingerprints.

AST fingerprints are a supplementary stagnation signal only. They do NOT prove that two
programs are semantically equivalent (or different).
"""
import ast
import hashlib


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _Normalizer(ast.NodeTransformer):
    """Strip names/constants so renamed or re-valued code yields the same structure."""

    def visit_Name(self, node: ast.Name) -> ast.AST:
        return ast.copy_location(ast.Name(id="_", ctx=node.ctx), node)

    def visit_arg(self, node: ast.arg) -> ast.AST:
        return ast.copy_location(ast.arg(arg="_"), node)

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        return ast.copy_location(ast.Constant(value=type(node.value).__name__), node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        self.generic_visit(node)
        node.name = "_"
        node.decorator_list = []
        return node


def structural_fingerprint(source: str) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    # Drop docstrings/comments effects: dump normalized tree without attributes.
    norm = _Normalizer().visit(tree)
    return sha256_hex(ast.dump(norm, annotate_fields=False, include_attributes=False))
