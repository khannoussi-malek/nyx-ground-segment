"""OpenAPI document: FastAPI paths plus the interface models as components (GS-EXT-04)."""

from collections.abc import Sequence
from typing import Any

from fastapi.openapi.utils import get_openapi
from pydantic.json_schema import models_json_schema
from starlette.routing import BaseRoute

from gs.common.interfaces import INTERFACE_MODELS

REF_TEMPLATE = "#/components/schemas/{model}"


def build_openapi(routes: Sequence[BaseRoute]) -> dict[str, Any]:
    # one schema per model: UtcTime's json serializer would otherwise split -Input/-Output
    doc = get_openapi(
        title="Nyx ground segment",
        version="0.2.0",
        routes=list(routes),
        separate_input_output_schemas=False,
    )
    _, defs = models_json_schema(
        [(m, "validation") for m in INTERFACE_MODELS], ref_template=REF_TEMPLATE
    )
    # defs["$defs"] holds the models and their nested defs, hoisted flat (D-EXT-16)
    schemas = {**doc.get("components", {}).get("schemas", {}), **defs["$defs"]}
    return {
        **doc,
        "components": {**doc.get("components", {}), "schemas": schemas},
        "x-generated": "make gen - do not edit",
    }
