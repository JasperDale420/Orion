"""
Cross-repo contract test: Orion's GatewayTradingClient.create_order  vs
Gateway's POST /api/v1/alpaca/orders FastAPI signature.

REGRESSION GUARD for the 2026-05-22 incident (Orion commit 51699c8):
    Gateway declares all order fields as bare-typed parameters (no Body
    annotation), so FastAPI treats them as query parameters. Orion's client
    was sending them in the JSON body -> every live order submission
    returned 422 with loc:["query","symbol"] / msg:"Field required".
    Nothing in CI caught it.

This test:
    1. Parses Gateway's `create_order` FastAPI signature directly from
       trading.py (AST-based, so it works even when Gateway's transitive
       dependencies aren't installed in Orion's venv).
    2. Mocks `httpx.AsyncClient.request`, calls
       `GatewayTradingClient.create_order`, and captures the actual kwargs
       (`params=`, `json=`) of the outgoing HTTP request.
    3. Cross-checks: every parameter Gateway declares as query MUST be sent
       in params; every parameter Gateway declares as body MUST be sent in
       json; fields Orion sends MUST exist on the Gateway endpoint.

Set DATA_GATEWAY_REPO=/path/to/Data-Gateway if the repo is not checked out
side-by-side with Orion. Set ORION_CONTRACT_TESTS_REQUIRE_GATEWAY=1 in CI to
make a missing Gateway repo a hard failure instead of a skip.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


# ─────────────────────────────────────────────────────────────────────────
# AST extractor for FastAPI endpoint signatures.
#
# Implemented FastAPI parameter-classification rules:
#   default = Depends(...)                 -> dependency (excluded)
#   default = Query(...) / Body(...) / ... -> explicit kind
#   no default + bare primitive annotation -> query (FastAPI default)
#   default = <literal> + primitive ann.   -> optional query
# ─────────────────────────────────────────────────────────────────────────

FASTAPI_MARKER_TO_KIND = {
    "Query": "query",
    "Body": "body",
    "Path": "path",
    "Header": "header",
    "Cookie": "cookie",
    "Form": "body",
    "File": "body",
}


@dataclass
class ParamSpec:
    name: str
    kind: str
    required: bool
    annotation_src: str | None = None


@dataclass
class EndpointSpec:
    method: str
    path: str
    function_name: str
    params: list[ParamSpec] = field(default_factory=list)

    def by_kind(self, kind: str) -> list[ParamSpec]:
        return [p for p in self.params if p.kind == kind]

    def required_by_kind(self, kind: str) -> list[ParamSpec]:
        return [p for p in self.params if p.kind == kind and p.required]


def _default_is_depends(default: ast.expr | None) -> bool:
    if not isinstance(default, ast.Call):
        return False
    func = default.func
    if isinstance(func, ast.Name):
        return func.id == "Depends"
    if isinstance(func, ast.Attribute):
        return func.attr == "Depends"
    return False


def _default_marker_name(default: ast.expr | None) -> str | None:
    if not isinstance(default, ast.Call):
        return None
    func = default.func
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    else:
        return None
    return name if name in FASTAPI_MARKER_TO_KIND else None


def _ast_unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def _decorator_to_endpoint(deco: ast.expr, func_name: str) -> EndpointSpec | None:
    if not isinstance(deco, ast.Call):
        return None
    func = deco.func
    if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)):
        return None
    if func.value.id != "router":
        return None
    if func.attr not in {"get", "post", "put", "delete", "patch"}:
        return None
    if not deco.args:
        return None
    arg0 = deco.args[0]
    if not (isinstance(arg0, ast.Constant) and isinstance(arg0.value, str)):
        return None
    return EndpointSpec(method=func.attr.upper(), path=arg0.value, function_name=func_name)


def _collect_params(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    path_param_names: set[str],
) -> list[ParamSpec]:
    args = func_node.args
    # Pad positional defaults so each positional arg lines up with its default
    # (or None when there is no default at all).
    pos_defaults = [None] * (len(args.args) - len(args.defaults)) + list(args.defaults)
    positional = list(zip(args.args, pos_defaults, strict=False))
    kwonly = list(zip(args.kwonlyargs, args.kw_defaults, strict=False))

    out: list[ParamSpec] = []
    for arg, default in positional + kwonly:
        if arg.arg in {"self", "cls"}:
            continue
        ann_src = _ast_unparse(arg.annotation)
        required = default is None

        if _default_is_depends(default):
            out.append(ParamSpec(arg.arg, "dependency", required=False, annotation_src=ann_src))
            continue

        marker = _default_marker_name(default)
        if marker is not None:
            out.append(ParamSpec(arg.arg, FASTAPI_MARKER_TO_KIND[marker], required=required, annotation_src=ann_src))
            continue

        # Bare annotation + literal-or-no default -> path if name appears in
        # the route path template, otherwise query (FastAPI's primitive default).
        kind = "path" if arg.arg in path_param_names else "query"
        out.append(ParamSpec(arg.arg, kind, required=required, annotation_src=ann_src))
    return out


def _path_param_names(path: str) -> set[str]:
    # FastAPI path params look like `/orders/{order_id}` — extract bare names.
    out: set[str] = set()
    i = 0
    while i < len(path):
        if path[i] == "{":
            j = path.find("}", i)
            if j == -1:
                break
            token = path[i + 1 : j].split(":", 1)[0].strip()
            if token:
                out.add(token)
            i = j + 1
        else:
            i += 1
    return out


def load_endpoint_spec(source_path: Path, *, function_name: str) -> EndpointSpec:
    """Parse `source_path` and return the EndpointSpec for `function_name`."""
    tree = ast.parse(source_path.read_text(), filename=str(source_path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != function_name:
            continue
        for deco in node.decorator_list:
            spec = _decorator_to_endpoint(deco, node.name)
            if spec is None:
                continue
            spec.params = _collect_params(node, path_param_names=_path_param_names(spec.path))
            return spec
    raise RuntimeError(
        f"Could not locate FastAPI endpoint `{function_name}` (with a @router.* decorator) in {source_path}"
    )


# ─────────────────────────────────────────────────────────────────────────
# Gateway repo discovery
# ─────────────────────────────────────────────────────────────────────────


def _find_gateway_repo() -> Path | None:
    env = os.environ.get("DATA_GATEWAY_REPO")
    if env:
        p = Path(env).expanduser().resolve()
        return p if (p / "gateway" / "api" / "alpaca" / "trading.py").exists() else None
    # Side-by-side checkout: /Users/.../Empire/{Orion, Data-Gateway}
    candidate = Path(__file__).resolve().parents[3] / "Data-Gateway"
    return candidate if (candidate / "gateway" / "api" / "alpaca" / "trading.py").exists() else None


GATEWAY_REPO: Path | None = _find_gateway_repo()
_REQUIRE_GATEWAY = os.environ.get("ORION_CONTRACT_TESTS_REQUIRE_GATEWAY", "0") == "1"


def _missing_gateway_message() -> str:
    return (
        "\nData-Gateway repo not found.\n"
        "  Set DATA_GATEWAY_REPO=/path/to/Data-Gateway, or check it out side-by-side\n"
        "  with Orion at <Empire>/Data-Gateway.\n"
        "\n"
        "This contract test catches signature drift between Orion's GatewayTradingClient\n"
        "and Gateway's FastAPI router. Without Data-Gateway available the contract\n"
        "cannot be verified. Set ORION_CONTRACT_TESTS_REQUIRE_GATEWAY=1 in CI to make\n"
        "this a hard failure instead of a skip."
    )


# ─────────────────────────────────────────────────────────────────────────
# The contract test
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_create_order_field_locations_match_gateway_signature() -> None:
    if GATEWAY_REPO is None:
        msg = _missing_gateway_message()
        if _REQUIRE_GATEWAY:
            pytest.fail(msg, pytrace=False)
        pytest.skip(msg)

    trading_py = GATEWAY_REPO / "gateway" / "api" / "alpaca" / "trading.py"
    spec = load_endpoint_spec(trading_py, function_name="create_order")

    declared_query = {p.name for p in spec.by_kind("query")}
    declared_query_required = {p.name for p in spec.required_by_kind("query")}
    declared_body = {p.name for p in spec.by_kind("body")}
    declared_path = {p.name for p in spec.by_kind("path")}

    assert spec.method == "POST", (
        f"Gateway create_order is now HTTP {spec.method}, not POST. "
        f"Orion's GatewayTradingClient.create_order needs to be updated."
    )
    assert spec.path == "/orders", (
        f"Gateway create_order route path is {spec.path!r}, not '/orders'. "
        f"The router mounts at /api/v1/alpaca + path; Orion's client targets "
        f"the joined path."
    )

    from orion.clients.gateway_trading_client import GatewayTradingClient

    captured: dict[str, Any] = {}

    async def fake_request(self, method: str, url: str, **kwargs: Any) -> Any:
        captured["method"] = method
        captured["url"] = str(url)
        captured["params"] = dict(kwargs.get("params") or {})
        captured["json"] = kwargs.get("json")

        class _FakeResponse:
            status_code = 200
            text = ""

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, Any]:
                return {"success": True, "data": {"id": "stub-order-id"}}

        return _FakeResponse()

    client = GatewayTradingClient(base_url="http://gateway.test", api_key="test-key")

    with patch("httpx.AsyncClient.request", new=fake_request):
        await client.create_order(
            symbol="AAPL",
            qty=1.0,
            side="buy",
            order_type="limit",
            time_in_force="day",
            limit_price=150.0,
            client_order_id="orion_test_123",
        )

    await client.close()

    assert captured.get("method") == "POST", (
        f"Orion sent HTTP {captured.get('method')!r}, expected POST. Captured: {captured}"
    )
    assert captured.get("url", "").endswith("/api/v1/alpaca/orders"), (
        f"Orion targeted {captured.get('url')!r}, expected path ending /api/v1/alpaca/orders. Captured: {captured}"
    )

    actual_query = set(captured.get("params", {}).keys())
    body = captured.get("json")
    actual_body = set(body.keys()) if isinstance(body, dict) else set()

    # ── CONTRACT CHECK 1: every required Gateway query param is sent ────
    missing_required = declared_query_required - actual_query
    assert not missing_required, (
        "CONTRACT VIOLATION: Orion did NOT send required Gateway query params: "
        f"{sorted(missing_required)}.\n"
        f"  Gateway requires (query): {sorted(declared_query_required)}\n"
        f"  Orion sent (query):       {sorted(actual_query)}\n"
        f"  Orion sent (body):        {sorted(actual_body)}"
    )

    # ── CONTRACT CHECK 2: the 2026-05-22 BUG PATTERN ────────────────────
    body_should_be_query = actual_body & declared_query
    assert not body_should_be_query, (
        "CONTRACT VIOLATION — 2026-05-22 BUG PATTERN.\n"
        f"  Orion sent these fields in the JSON BODY: {sorted(body_should_be_query)}\n"
        "  Gateway declares them as QUERY parameters in its FastAPI signature.\n"
        "  This regresses Orion commit 51699c8 and would cause 100% of live order\n"
        "  submissions to return 422 with loc:['query', <field>] / 'Field required'.\n"
        "  Fix: pass params= instead of json_body= in GatewayTradingClient.create_order."
    )

    # ── CONTRACT CHECK 3: inverse (query that should be body) ───────────
    query_should_be_body = actual_query & declared_body
    assert not query_should_be_body, (
        "CONTRACT VIOLATION:\n"
        f"  Orion sent these fields in the QUERY string: {sorted(query_should_be_body)}\n"
        "  Gateway declares them as BODY parameters."
    )

    # ── CONTRACT CHECK 4: no unknown fields ─────────────────────────────
    known = declared_query | declared_body | declared_path
    unknown_query = actual_query - known
    unknown_body = actual_body - known
    assert not unknown_query, (
        f"Orion sent unknown query fields {sorted(unknown_query)} that Gateway's "
        f"create_order endpoint does not declare.\n"
        f"  Known Gateway params: {sorted(known)}\n"
        f"  Either Gateway dropped these and Orion has dead fields, or there is a typo."
    )
    assert not unknown_body, (
        f"Orion sent unknown body fields {sorted(unknown_body)} that Gateway's "
        f"create_order endpoint does not declare.\n"
        f"  Known Gateway params: {sorted(known)}"
    )


# ─────────────────────────────────────────────────────────────────────────
# Meta-test: prove the AST extractor classifies Gateway's create_order
# signature the way FastAPI would, even when the file is parsed cold (no
# imports executed). This protects the contract test itself from rotting.
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_ast_extractor_classifies_create_order_signature_correctly() -> None:
    if GATEWAY_REPO is None:
        msg = _missing_gateway_message()
        if _REQUIRE_GATEWAY:
            pytest.fail(msg, pytrace=False)
        pytest.skip(msg)

    trading_py = GATEWAY_REPO / "gateway" / "api" / "alpaca" / "trading.py"
    spec = load_endpoint_spec(trading_py, function_name="create_order")

    by_name = {p.name: p for p in spec.params}

    # Required (no default) -> query
    for name in ("symbol", "side"):
        assert name in by_name, f"Expected param `{name}` on Gateway create_order"
        assert by_name[name].kind == "query", (
            f"Expected `{name}` to be classified as query, got {by_name[name].kind}. "
            f"FastAPI defaults bare primitives to query — has Gateway added a Body() marker?"
        )
        assert by_name[name].required, f"Expected `{name}` to be required"

    # Dependencies must be excluded from the user-facing contract.
    for dep_name in ("client", "registry"):
        if dep_name in by_name:
            assert by_name[dep_name].kind == "dependency", (
                f"Expected `{dep_name}` to be a Depends(...) dependency, got {by_name[dep_name].kind}"
            )
