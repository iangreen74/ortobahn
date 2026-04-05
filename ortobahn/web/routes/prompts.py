"""Web routes for prompt management UI."""

from __future__ import annotations

import datetime
from typing import Any

from flask import Blueprint, jsonify, render_template_string, request
from pydantic import BaseModel, Field, ValidationError

from ortobahn.prompts import PromptManager, PromptStatus, PromptTemplate

prompts_bp = Blueprint("prompts", __name__, url_prefix="/api/prompts")
prompt_manager = PromptManager()


class CreatePromptRequest(BaseModel):
    """Request to create a new prompt."""

    name: str = Field(..., min_length=1, max_length=255)
    template: str = Field(..., min_length=1)
    created_by: str | None = None
    comment: str | None = None


class RenderPromptRequest(BaseModel):
    """Request to render a prompt with context."""

    context: dict[str, Any] = Field(default_factory=dict)


@prompts_bp.route("/", methods=["GET"])
def list_prompts() -> tuple[Any, int]:
    """List all prompts or filter by name."""
    name = request.args.get("name")
    prompts = prompt_manager.list_prompts(name=name)
    return jsonify([p.model_dump(mode="json") for p in prompts]), 200


@prompts_bp.route("/", methods=["POST"])
def create_prompt() -> tuple[Any, int]:
    """Create a new prompt version."""
    try:
        req = CreatePromptRequest(**request.json)
    except ValidationError as e:
        return jsonify({"error": "Invalid request", "details": e.errors()}), 400

    # Calculate next version
    existing = prompt_manager.list_prompts(name=req.name)
    next_version = max([p.version for p in existing], default=0) + 1

    prompt = PromptTemplate(
        name=req.name,
        version=next_version,
        template=req.template,
        created_by=req.created_by,
        comment=req.comment,
        status=PromptStatus.DRAFT,
    )

    try:
        prompt_id = prompt_manager.create_prompt(prompt)
        return jsonify({"id": prompt_id, "version": next_version}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@prompts_bp.route("/<name>", methods=["GET"])
def get_prompt(name: str) -> tuple[Any, int]:
    """Get a specific prompt version."""
    version = request.args.get("version", type=int)
    prompt = prompt_manager.get_prompt(name, version=version)

    if not prompt:
        return jsonify({"error": "Prompt not found"}), 404

    return jsonify(prompt.model_dump(mode="json")), 200


@prompts_bp.route("/<name>/render", methods=["POST"])
def render_prompt(name: str) -> tuple[Any, int]:
    """Render a prompt with provided context (preview)."""
    try:
        req = RenderPromptRequest(**request.json)
        version = request.args.get("version", type=int)
    except ValidationError as e:
        return jsonify({"error": "Invalid request", "details": e.errors()}), 400

    try:
        rendered = prompt_manager.render_prompt(name, req.context, version=version)
        return jsonify({"rendered": rendered}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": f"Render failed: {e}"}), 400


@prompts_bp.route("/<name>/activate", methods=["POST"])
def activate_prompt(name: str) -> tuple[Any, int]:
    """Activate a specific prompt version."""
    version = request.json.get("version") if request.json else None
    if not version:
        return jsonify({"error": "Version required"}), 400

    try:
        prompt_manager.activate_prompt(name, version)
        return jsonify({"message": "Prompt activated"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@prompts_bp.route("/ui", methods=["GET"])
def prompt_ui() -> str:
    """Serve prompt management UI for non-technical users."""
    html = """
<!DOCTYPE html>
<html>
<head>
    <title>Prompt Management</title>
    <style>
        :root { --primary: #007bff; --spacing: 1rem; --border-radius: 4px; }
        body { font-family: system-ui; margin: 0; padding: var(--spacing); }
        .container { max-width: 1200px; margin: 0 auto; }
        .form-group { margin-bottom: var(--spacing); }
        label { display: block; font-weight: bold; margin-bottom: 0.25rem; }
        input, textarea { width: 100%; padding: 0.5rem; border: 1px solid #ccc; border-radius: var(--border-radius); }
        textarea { min-height: 150px; font-family: monospace; }
        button { background: var(--primary); color: white; border: none; padding: 0.5rem 1rem; border-radius: var(--border-radius); cursor: pointer; }
        .preview { background: #f5f5f5; padding: var(--spacing); border-radius: var(--border-radius); margin-top: var(--spacing); }
        .version-list { list-style: none; padding: 0; }
        .version-item { padding: 0.5rem; border: 1px solid #ddd; margin-bottom: 0.5rem; border-radius: var(--border-radius); }
    </style>
</head>
<body>
    <div class="container">
        <h1>Prompt Management</h1>
        <div class="form-group"><label>Name:</label><input id="name" type="text" /></div>
        <div class="form-group"><label>Template:</label><textarea id="template"></textarea></div>
        <div class="form-group"><label>Comment:</label><input id="comment" type="text" /></div>
        <button onclick="savePrompt()">Save Draft</button>
        <button onclick="previewPrompt()">Preview</button>
        <div class="preview" id="preview"></div>
        <h2>Versions</h2>
        <ul class="version-list" id="versions"></ul>
    </div>
    <script>
        async function savePrompt() {
            const res = await fetch('/api/prompts/', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: document.getElementById('name').value, template: document.getElementById('template').value, comment: document.getElementById('comment').value})
            });
            alert(res.ok ? 'Saved!' : 'Error');
        }
        async function previewPrompt() {
            const name = document.getElementById('name').value;
            const res = await fetch(`/api/prompts/${name}/render`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({context: {}})});
            const data = await res.json();
            document.getElementById('preview').textContent = data.rendered || data.error;
        }
    </script>
</body>
</html>
    """
    return html
