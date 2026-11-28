"""OpenAPI 3.1 document for implemented API routes."""

from __future__ import annotations

from typing import Any


def build_openapi_document() -> dict[str, Any]:
    error_schema = {
        "type": "object",
        "required": ["error"],
        "properties": {
            "error": {
                "type": "object",
                "required": ["code", "message", "details", "request_id"],
                "properties": {
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                    "details": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/ErrorDetail"},
                    },
                    "request_id": {"type": "string", "format": "uuid"},
                },
            }
        },
    }
    return {
        "openapi": "3.1.0",
        "info": {"title": "Thistlewarden API", "version": "1.0.0"},
        "paths": {
            "/api/v1/configuration/export": {
                "get": {
                    "operationId": "exportConfiguration",
                    "responses": {"200": {"description": "Secret-free configuration"}},
                }
            },
            "/api/v1/configuration/import": {
                "post": {
                    "operationId": "importConfiguration",
                    "responses": {"200": {"description": "Import plan"}},
                }
            },
            "/api/v1/health": {
                "get": {
                    "operationId": "getApiHealth",
                    "summary": "Return API liveness",
                    "responses": {
                        "200": {
                            "description": "Service is alive",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Health"}
                                }
                            },
                        }
                    },
                }
            },
            "/api/v1/settings": {
                "get": {
                    "operationId": "getSettings",
                    "summary": "Return safe resolved operational settings",
                    "responses": {
                        "200": {
                            "description": "Resolved settings and source metadata",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["settings"],
                                        "properties": {
                                            "settings": {
                                                "type": "array",
                                                "items": {"$ref": "#/components/schemas/Setting"},
                                            }
                                        },
                                    }
                                }
                            },
                        },
                        "500": {"$ref": "#/components/responses/Error"},
                    },
                }
            },
            "/api/v1/endpoints": {
                "get": {
                    "operationId": "listEndpoints",
                    "summary": "List provider endpoints",
                    "responses": {"200": {"description": "Masked endpoint list"}},
                },
                "post": {
                    "operationId": "createEndpoint",
                    "summary": "Create provider endpoint",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/EndpointInput"}
                            }
                        },
                    },
                    "responses": {
                        "201": {"description": "Endpoint created"},
                        "400": {"$ref": "#/components/responses/Error"},
                        "409": {"$ref": "#/components/responses/Error"},
                    },
                },
            },
            "/api/v1/endpoints/{endpoint_id}": {
                "parameters": [
                    {
                        "name": "endpoint_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "get": {
                    "operationId": "getEndpoint",
                    "summary": "Get masked provider endpoint",
                    "responses": {
                        "200": {"description": "Endpoint"},
                        "404": {"$ref": "#/components/responses/Error"},
                    },
                },
                "patch": {
                    "operationId": "updateEndpoint",
                    "summary": "Update provider endpoint",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": {
                        "200": {"description": "Updated endpoint"},
                        "400": {"$ref": "#/components/responses/Error"},
                        "404": {"$ref": "#/components/responses/Error"},
                        "409": {"$ref": "#/components/responses/Error"},
                    },
                },
                "delete": {
                    "operationId": "deleteEndpoint",
                    "summary": "Delete unreferenced endpoint",
                    "responses": {
                        "204": {"description": "Endpoint deleted"},
                        "404": {"$ref": "#/components/responses/Error"},
                        "409": {"$ref": "#/components/responses/Error"},
                    },
                },
            },
            "/api/v1/endpoints/{endpoint_id}/model-discovery": {
                "post": {
                    "operationId": "discoverModels",
                    "summary": "Preview authenticated provider model discovery",
                    "responses": {"200": {"description": "Discovery preview"}},
                }
            },
            "/api/v1/endpoints/{endpoint_id}/model-discovery/import": {
                "post": {
                    "operationId": "importDiscoveredModels",
                    "summary": "Selectively import discovered model profiles",
                    "responses": {"201": {"description": "Imported profiles"}},
                }
            },
            "/api/v1/endpoints/{endpoint_id}/models": {
                "parameters": [
                    {
                        "name": "endpoint_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "get": {
                    "operationId": "listModelProfiles",
                    "summary": "List model profiles for endpoint",
                    "responses": {
                        "200": {"description": "Model profile list"},
                        "404": {"$ref": "#/components/responses/Error"},
                    },
                },
                "post": {
                    "operationId": "createModelProfile",
                    "summary": "Create model profile",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ModelProfileInput"}
                            }
                        },
                    },
                    "responses": {
                        "201": {"description": "Model profile created"},
                        "400": {"$ref": "#/components/responses/Error"},
                        "404": {"$ref": "#/components/responses/Error"},
                        "409": {"$ref": "#/components/responses/Error"},
                    },
                },
            },
            "/api/v1/models/{profile_id}": {
                "parameters": [
                    {
                        "name": "profile_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "get": {
                    "operationId": "getModelProfile",
                    "summary": "Get model profile",
                    "responses": {
                        "200": {"description": "Model profile"},
                        "404": {"$ref": "#/components/responses/Error"},
                    },
                },
                "patch": {
                    "operationId": "updateModelProfile",
                    "summary": "Update model profile",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "responses": {
                        "200": {"description": "Updated model profile"},
                        "400": {"$ref": "#/components/responses/Error"},
                        "404": {"$ref": "#/components/responses/Error"},
                        "409": {"$ref": "#/components/responses/Error"},
                    },
                },
                "delete": {
                    "operationId": "deleteModelProfile",
                    "summary": "Delete unreferenced model profile",
                    "responses": {
                        "204": {"description": "Model profile deleted"},
                        "404": {"$ref": "#/components/responses/Error"},
                        "409": {"$ref": "#/components/responses/Error"},
                    },
                },
            },
            "/api/v1/judges": {
                "get": {
                    "operationId": "listJudges",
                    "responses": {"200": {"description": "User-created judge definitions"}},
                },
                "post": {
                    "operationId": "createJudge",
                    "responses": {
                        "201": {"description": "Judge draft created"},
                        "400": {"$ref": "#/components/responses/Error"},
                    },
                },
            },
            "/api/v1/judges/{definition_id}": {
                "get": {
                    "operationId": "getJudge",
                    "responses": {
                        "200": {"description": "Judge definition"},
                        "404": {"$ref": "#/components/responses/Error"},
                    },
                }
            },
            "/api/v1/judges/{definition_id}/draft": {
                "patch": {
                    "operationId": "updateJudgeDraft",
                    "responses": {
                        "200": {"description": "Judge draft updated"},
                        "400": {"$ref": "#/components/responses/Error"},
                        "404": {"$ref": "#/components/responses/Error"},
                        "409": {"$ref": "#/components/responses/Error"},
                    },
                }
            },
            "/api/v1/judges/{definition_id}/publish": {
                "post": {
                    "operationId": "publishJudge",
                    "responses": {
                        "201": {"description": "Immutable judge version published"},
                        "404": {"$ref": "#/components/responses/Error"},
                        "409": {"$ref": "#/components/responses/Error"},
                    },
                }
            },
            "/api/v1/judges/{definition_id}/versions/{version}": {
                "get": {
                    "operationId": "getJudgeVersion",
                    "responses": {
                        "200": {"description": "Immutable judge version"},
                        "404": {"$ref": "#/components/responses/Error"},
                    },
                }
            },
            "/api/v1/judge-profiles": {
                "get": {
                    "operationId": "listJudgeProfiles",
                    "responses": {"200": {"description": "Judge profiles"}},
                },
                "post": {
                    "operationId": "createJudgeProfile",
                    "responses": {"201": {"description": "Judge profile created"}},
                },
            },
            "/api/v1/judge-profiles/{profile_id}": {
                "get": {
                    "operationId": "getJudgeProfile",
                    "responses": {"200": {"description": "Judge profile"}},
                },
                "patch": {
                    "operationId": "updateJudgeProfile",
                    "responses": {"200": {"description": "Judge profile updated"}},
                },
                "delete": {
                    "operationId": "deleteJudgeProfile",
                    "responses": {"204": {"description": "Judge profile deleted"}},
                },
            },
            "/api/v1/models/{profile_id}/cost-calculations": {
                "parameters": [
                    {
                        "name": "profile_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "post": {
                    "operationId": "createCostCalculation",
                    "summary": "Calculate and persist EUR cost",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/CostCalculationInput"}
                            }
                        },
                    },
                    "responses": {
                        "201": {"description": "Cost calculation created"},
                        "400": {"$ref": "#/components/responses/Error"},
                        "404": {"$ref": "#/components/responses/Error"},
                    },
                },
            },
            "/api/v1/cost-calculations/{calculation_id}": {
                "parameters": [
                    {
                        "name": "calculation_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "get": {
                    "operationId": "getCostCalculation",
                    "summary": "Get auditable cost calculation",
                    "responses": {
                        "200": {"description": "Cost calculation"},
                        "404": {"$ref": "#/components/responses/Error"},
                    },
                },
            },
            "/api/v1/runs": {
                "post": {
                    "operationId": "createRun",
                    "responses": {"201": {"description": "Run created"}},
                }
            },
            "/api/v1/runs/{run_id}": {
                "get": {"operationId": "getRun", "responses": {"200": {"description": "Run"}}},
                "delete": {
                    "operationId": "deleteRun",
                    "responses": {"204": {"description": "Deleted"}},
                },
            },
            "/api/v1/runs/{run_id}/export": {
                "get": {
                    "operationId": "exportRun",
                    "description": (
                        "Download a bounded-memory JSON, CSV, or HTML export. "
                        "Synchronous exports support at most 50,000 run jobs."
                    ),
                    "responses": {"200": {"description": "Run export"}},
                }
            },
            "/api/v1/runs/retention-cleanup": {
                "post": {
                    "operationId": "cleanupRuns",
                    "responses": {"200": {"description": "Cleanup result"}},
                }
            },
            "/api/v1/runs/{run_id}/quality-gates": {
                "get": {
                    "operationId": "getRunQualityGates",
                    "responses": {"200": {"description": "Gate results"}},
                }
            },
            "/api/v1/runs/{run_id}/aggregates": {
                "get": {
                    "operationId": "getRunAggregates",
                    "responses": {
                        "200": {
                            "description": (
                                "Model aggregates including error_rate, guardrails, "
                                "provider_errors, processing_errors, unknown_errors, "
                                "and guardrail_rate"
                            )
                        }
                    },
                }
            },
            "/api/v1/runs/{run_id}/comparison-candidates": {
                "get": {
                    "operationId": "getRunComparisonCandidates",
                    "responses": {"200": {"description": "Completed baseline candidates"}},
                }
            },
            "/api/v1/runs/{run_id}/compare/{baseline_id}": {
                "get": {
                    "operationId": "compareRuns",
                    "responses": {"200": {"description": "Snapshot-based comparison"}},
                }
            },
            "/api/v1/runs/{run_id}/compare/{baseline_id}/export": {
                "get": {
                    "operationId": "exportRunComparison",
                    "responses": {"200": {"description": "Deterministic comparison report"}},
                }
            },
            "/api/v1/runs/{run_id}/cancel": {
                "post": {
                    "operationId": "cancelRun",
                    "responses": {"200": {"description": "Cancelled run"}},
                }
            },
            "/api/v1/runs/{run_id}/rerun-failures": {
                "get": {
                    "operationId": "previewModelsWithErrorsRerun",
                    "summary": (
                        "Review current-configuration scope before rerunning models with errors"
                    ),
                    "responses": {
                        "200": {"description": "Scope, call counts, and stale-review fingerprint"}
                    },
                },
                "post": {
                    "operationId": "rerunModelsWithErrors",
                    "summary": (
                        "Create reviewed current-configuration rerun; requires preview fingerprint"
                    ),
                    "responses": {"201": {"description": "New run"}},
                },
            },
            "/api/v1/golden-sets": {
                "get": {
                    "operationId": "listGoldenSets",
                    "responses": {"200": {"description": "Golden sets"}},
                },
                "post": {
                    "operationId": "createGoldenSet",
                    "responses": {"201": {"description": "Created"}},
                },
            },
            "/api/v1/golden-sets/import": {
                "post": {
                    "operationId": "importGoldenSet",
                    "summary": "Preview or atomically import JSON/CSV golden set",
                    "responses": {
                        "200": {"description": "Valid preview"},
                        "201": {"description": "Imported"},
                    },
                }
            },
            "/api/v1/golden-sets/{set_id}": {
                "parameters": [
                    {
                        "name": "set_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "get": {
                    "operationId": "getGoldenSet",
                    "responses": {"200": {"description": "Golden set"}},
                },
                "patch": {
                    "operationId": "updateGoldenSet",
                    "responses": {"200": {"description": "Updated"}},
                },
                "delete": {
                    "operationId": "deleteGoldenSet",
                    "responses": {"204": {"description": "Deleted"}},
                },
            },
            "/api/v1/golden-sets/{set_id}/export": {
                "parameters": [
                    {
                        "name": "set_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "get": {
                    "operationId": "exportGoldenSet",
                    "summary": "Download canonical JSON or CSV",
                    "responses": {"200": {"description": "Export file"}},
                },
            },
            "/api/v1/golden-sets/{set_id}/quality-gates": {
                "get": {
                    "operationId": "getQualityGateDefaults",
                    "responses": {"200": {"description": "Gate defaults"}},
                },
                "put": {
                    "operationId": "setQualityGateDefaults",
                    "responses": {"200": {"description": "Gate defaults updated"}},
                },
            },
            "/api/v1/golden-sets/{set_id}/items": {
                "parameters": [
                    {
                        "name": "set_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "get": {
                    "operationId": "listGoldenSetItems",
                    "responses": {"200": {"description": "Items"}},
                },
                "post": {
                    "operationId": "createGoldenSetItem",
                    "responses": {"201": {"description": "Created"}},
                },
            },
            "/api/v1/golden-set-items/{item_id}": {
                "parameters": [
                    {
                        "name": "item_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"},
                    }
                ],
                "get": {
                    "operationId": "getGoldenSetItem",
                    "responses": {"200": {"description": "Item"}},
                },
                "patch": {
                    "operationId": "updateGoldenSetItem",
                    "responses": {"200": {"description": "Updated"}},
                },
                "delete": {
                    "operationId": "deleteGoldenSetItem",
                    "responses": {"204": {"description": "Deleted"}},
                },
            },
            "/api/v1/openapi.json": {
                "get": {
                    "operationId": "getOpenApiDocument",
                    "summary": "Return OpenAPI document",
                    "responses": {"200": {"description": "OpenAPI 3.1 document"}},
                }
            },
        },
        "components": {
            "schemas": {
                "Error": error_schema,
                "ErrorDetail": {
                    "type": "object",
                    "required": ["field", "message"],
                    "properties": {
                        "field": {"type": "string"},
                        "message": {"type": "string"},
                    },
                },
                "Health": {
                    "type": "object",
                    "required": ["service", "status", "version"],
                    "properties": {
                        "service": {"type": "string"},
                        "status": {"type": "string", "const": "ok"},
                        "version": {"type": "string"},
                    },
                },
                "HeaderInput": {
                    "type": "object",
                    "required": ["name", "value"],
                    "properties": {
                        "name": {"type": "string", "maxLength": 256},
                        "value": {"type": "string", "maxLength": 16384},
                        "secret": {"type": "boolean", "default": False},
                    },
                },
                "EndpointInput": {
                    "type": "object",
                    "required": ["name", "completions_url"],
                    "properties": {
                        "name": {"type": "string", "maxLength": 200},
                        "api_format": {
                            "type": "string",
                            "enum": ["openai", "anthropic"],
                            "default": "openai",
                        },
                        "completions_url": {"type": "string", "format": "uri"},
                        "models_url": {"type": ["string", "null"], "format": "uri"},
                        "auth_type": {
                            "type": "string",
                            "enum": ["api_key", "oauth_client_credentials"],
                            "default": "api_key",
                        },
                        "api_key": {"type": ["string", "null"], "writeOnly": True},
                        "api_key_header": {"type": "string", "default": "Authorization"},
                        "api_key_prefix": {"type": "string", "maxLength": 100},
                        "oauth_token_url": {"type": ["string", "null"], "format": "uri"},
                        "oauth_client_id": {"type": ["string", "null"], "maxLength": 16384},
                        "oauth_client_secret": {"type": ["string", "null"], "writeOnly": True},
                        "oauth_scope": {"type": ["string", "null"], "maxLength": 16384},
                        "extra_headers": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/HeaderInput"},
                        },
                        "timeout_seconds": {"type": "integer", "minimum": 1},
                        "max_retries": {"type": "integer", "minimum": 0},
                        "requests_per_minute": {"type": ["integer", "null"], "minimum": 1},
                        "tokens_per_minute": {"type": ["integer", "null"], "minimum": 1},
                        "max_concurrency": {"type": ["integer", "null"], "minimum": 1},
                        "active": {"type": "boolean"},
                        "trusted_internal_http": {"type": "boolean"},
                        "security_warning_confirmed": {"type": "boolean"},
                    },
                },
                "PricingInput": {
                    "type": "object",
                    "properties": {
                        "input_per_million": {
                            "type": "string",
                            "pattern": "^[0-9]+(?:\\.[0-9]+)?$",
                        },
                        "output_per_million": {
                            "type": "string",
                            "pattern": "^[0-9]+(?:\\.[0-9]+)?$",
                        },
                        "context_threshold": {"type": ["integer", "null"], "minimum": 1},
                        "high_context_input_per_million": {"type": ["string", "null"]},
                        "high_context_output_per_million": {"type": ["string", "null"]},
                    },
                },
                "ModelProfileInput": {
                    "type": "object",
                    "required": ["model_id", "display_name"],
                    "properties": {
                        "model_id": {"type": "string", "maxLength": 500},
                        "display_name": {"type": "string", "maxLength": 200},
                        "description": {"type": ["string", "null"]},
                        "temperature": {"type": ["string", "null"]},
                        "top_p": {"type": ["string", "null"]},
                        "max_tokens": {"type": ["integer", "null"], "minimum": 1},
                        "max_tokens_field_path": {"type": "string", "default": "max_tokens"},
                        "reasoning_field_path": {"type": ["string", "null"]},
                        "reasoning_value": {"type": ["string", "null"]},
                        "send_reasoning_off": {"type": "boolean"},
                        "custom_parameters": {"type": "object"},
                        "pricing": {"$ref": "#/components/schemas/PricingInput"},
                        "active": {"type": "boolean"},
                    },
                },
                "TokenUsage": {
                    "type": "object",
                    "properties": {
                        "input_tokens": {"type": ["integer", "null"], "minimum": 0},
                        "output_tokens": {"type": ["integer", "null"], "minimum": 0},
                        "cached_input_tokens": {"type": ["integer", "null"], "minimum": 0},
                        "reasoning_tokens": {"type": ["integer", "null"], "minimum": 0},
                    },
                },
                "CostCalculationInput": {
                    "type": "object",
                    "required": ["usage", "chargeable"],
                    "properties": {
                        "usage": {"$ref": "#/components/schemas/TokenUsage"},
                        "chargeable": {"type": "boolean"},
                    },
                },
                "Setting": {
                    "type": "object",
                    "required": ["name", "source", "read_only", "secret", "value"],
                    "properties": {
                        "name": {"type": "string"},
                        "source": {
                            "type": "string",
                            "enum": ["default", "database", "environment"],
                        },
                        "read_only": {"type": "boolean"},
                        "secret": {"type": "boolean"},
                        "value": {},
                    },
                },
            },
            "responses": {
                "Error": {
                    "description": "Uniform API error",
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
                    },
                }
            },
        },
    }
