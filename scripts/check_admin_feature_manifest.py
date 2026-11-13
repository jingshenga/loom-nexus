#!/usr/bin/env python3
"""Fail the release when a registered admin feature loses UI, API, permission, or migration."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT / "scripts/admin-feature-manifest.json").read_text())
router_text = (ROOT / "core/frontend/src/router/index.js").read_text()
main_text = (ROOT / "core/app/main.py").read_text()
errors = []

for feature in manifest.get("features", []):
    route = feature["route"]
    component = Path(feature["view"])
    if not (ROOT / component).is_file():
        errors.append(f"{feature['name']}: missing view {component}")
    leaf = "" if route == "/admin" else route.rsplit("/", 1)[-1]
    if not re.search(rf'path:\s*["\']{re.escape(leaf)}["\']', router_text):
        errors.append(f"{feature['name']}: route is not registered: {route}")
    if component.name not in router_text:
        errors.append(f"{feature['name']}: route does not load {component.name}")

    module_path = ROOT / "core/app/api" / feature["api_module"]
    if not module_path.is_file():
        errors.append(f"{feature['name']}: missing API module {module_path.name}")
        continue
    module = module_path.read_text()
    module_name = module_path.stem
    if f"{module_name}.router" not in main_text:
        errors.append(f"{feature['name']}: router is not included by app.main")
    prefix_match = re.search(r'prefix\s*=\s*["\']([^"\']+)', module)
    if not prefix_match:
        errors.append(f"{feature['name']}: API prefix is missing")
        continue
    prefix = prefix_match.group(1).rstrip("/")
    routes = set()
    for method, suffix in re.findall(r'@router\.(get|post|put|patch|delete)\(\s*["\']([^"\']*)', module):
        routes.add((method.upper(), prefix + (suffix if suffix.startswith("/") else ("/" + suffix if suffix else ""))))
    for api_method, api_path in feature["api"]:
        if (api_method, api_path) not in routes:
            errors.append(f"{feature['name']}: missing {api_method} {api_path}")
    if not re.search(r"dependencies\s*=\s*\[\s*Depends\(require_admin\)\s*\]", module):
        errors.append(f"{feature['name']}: router-level admin authorization is missing")
    for migration in feature["migration"]:
        if not (ROOT / "scripts/migrations" / migration).is_file():
            errors.append(f"{feature['name']}: missing migration {migration}")

if errors:
    raise SystemExit("ADMIN FEATURE MANIFEST FAILED:\n- " + "\n- ".join(errors))
print(f"ADMIN FEATURE MANIFEST OK ({len(manifest['features'])} features)")
