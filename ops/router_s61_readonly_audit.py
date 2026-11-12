"""Read-only audit of the recovered ORIGINAL S61 and Gateway startup.
No imports of app.py, Source access, SQL, network, service or native effects.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path


def audit_gateway(gateway):
    app = gateway / "app.py"
    ports = gateway / "deployment_event_ports.py"
    paths = (app, ports, gateway / "run_bindings.py", gateway / "source_holder_ports.py")
    pins = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    tree = ast.parse(app.read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "create_app"]
    launch = []
    for call in calls:
        for kw in call.keywords:
            if kw.arg == "run_bindings":
                launch.append(ast.unparse(kw.value))
    defaults = {}
    for n in ast.walk(ast.parse(ports.read_text())):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "deployment_run_bindings":
            for arg, default in zip(n.args.kwonlyargs, n.args.kw_defaults):
                if arg.arg == "enabled":
                    defaults["enabled"] = ast.literal_eval(default)
    # Static evidence only: a call is not proof of installed runtime capabilities.
    disabled_call = "deployment_run_bindings()" in launch and defaults.get("enabled") is False
    return {"pins": pins, "startup_run_bindings": launch,
            "deployment_default_enabled": defaults.get("enabled"),
            "static_default_off": disabled_call, "runtime_slot_verified": False}


def audit_manifest(root):
    manifest = json.loads((root / "HANDOFF-MANIFEST.json").read_text())
    for entry in manifest["files"]:
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("UNSAFE_MANIFEST_PATH")
        data = (root / relative).read_bytes()
        if (len(data) != entry["bytes"]
                or hashlib.sha256(data).hexdigest() != entry["sha256"]):
            raise ValueError("ORIGINAL_FILE_PIN_MISMATCH")
    return {"verified_files": len(manifest["files"]), "all_pins_match": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", type=Path)
    parser.add_argument("--s61", type=Path)
    args = parser.parse_args()
    result = {"kind": "router-s61-readonly-restoration-audit-v1",
              "dispatch_attempted": False, "source_read_attempted": False,
              "runtime_started": False, "g0": "UNKNOWN", "gate1": "DENY"}
    if args.gateway:
        result["gateway_static"] = audit_gateway(args.gateway)
    if args.s61:
        result["s61_archive"] = audit_manifest(args.s61)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

