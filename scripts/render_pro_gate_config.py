#!/usr/bin/env python
"""Render one protected Pro-gate Wrangler environment without logging values."""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import tomllib
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENTS = frozenset({"staging", "production"})
VAR_NAMES = (
    "POLAR_ORG_ID",
    "LOCAL_PRO_BENEFIT_ID",
    "PRO_CLOUD_BENEFIT_ID",
    "ENTITLEMENT_SIGNING_KID",
    "ENTITLEMENT_SIGNING_PUBLIC_KEY",
    "ARTIFACT_KEY",
    "ARTIFACT_FILENAME",
    "ARTIFACT_SHA256",
)


class ConfigError(ValueError):
    """Protected deployment configuration is absent or malformed."""


def _required(values: dict[str, str], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ConfigError(f"{name} is missing or malformed")
    if len(value) > 1_024 or any(ord(char) < 0x20 for char in value):
        raise ConfigError(f"{name} is missing or malformed")
    return value


def _validate(values: dict[str, str]) -> dict[str, str]:
    clean = {name: _required(values, name) for name in (*VAR_NAMES, "R2_BUCKET_NAME")}
    for name in ("POLAR_ORG_ID", "LOCAL_PRO_BENEFIT_ID", "PRO_CLOUD_BENEFIT_ID"):
        try:
            parsed = uuid.UUID(clean[name])
        except ValueError as exc:
            raise ConfigError(f"{name} must be a UUID") from exc
        if str(parsed) != clean[name].lower():
            raise ConfigError(f"{name} must be a canonical UUID")
    if clean["LOCAL_PRO_BENEFIT_ID"] == clean["PRO_CLOUD_BENEFIT_ID"]:
        raise ConfigError("Local Pro and Pro Cloud benefits must be distinct")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", clean["ENTITLEMENT_SIGNING_KID"]) is None:
        raise ConfigError("ENTITLEMENT_SIGNING_KID is malformed")
    encoded_public_key = clean["ENTITLEMENT_SIGNING_PUBLIC_KEY"]
    try:
        public_key = base64.urlsafe_b64decode(
            encoded_public_key + "=" * (-len(encoded_public_key) % 4)
        )
    except Exception as exc:  # noqa: BLE001 - protected config validation
        raise ConfigError("ENTITLEMENT_SIGNING_PUBLIC_KEY is malformed") from exc
    if (
        len(public_key) != 32
        or base64.urlsafe_b64encode(public_key).rstrip(b"=").decode("ascii")
        != encoded_public_key
    ):
        raise ConfigError("ENTITLEMENT_SIGNING_PUBLIC_KEY is malformed")
    filename = clean["ARTIFACT_FILENAME"]
    if (
        len(filename) > 128
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.zip", filename, re.IGNORECASE) is None
    ):
        raise ConfigError("ARTIFACT_FILENAME is malformed")
    digest = clean["ARTIFACT_SHA256"].lower()
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ConfigError("ARTIFACT_SHA256 must be raw SHA-256")
    clean["ARTIFACT_SHA256"] = digest
    parts = clean["ARTIFACT_KEY"].split("/")
    if (
        len(parts) < 4
        or any(not part or part in {".", ".."} for part in parts)
        or digest not in parts
        or parts[-1] != filename
        or not any(re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", part) for part in parts)
    ):
        raise ConfigError("ARTIFACT_KEY is not immutable and hash-bearing")
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", clean["R2_BUCKET_NAME"]) is None:
        raise ConfigError("R2_BUCKET_NAME is malformed")
    return clean


def _replace_section_values(
    lines: list[str],
    section: str,
    replacements: dict[str, str],
) -> None:
    headers = (f"[{section}]", f"[[{section}]]")
    header_index = next(
        (index for index, line in enumerate(lines) if line.strip() in headers),
        None,
    )
    if header_index is None:
        raise ConfigError(f"missing protected section {section}")
    header = lines[header_index].strip()
    start = header_index + 1
    end = next(
        (index for index in range(start, len(lines)) if lines[index].lstrip().startswith("[")),
        len(lines),
    )
    found: set[str] = set()
    for index in range(start, end):
        match = re.fullmatch(r"\s*([A-Za-z0-9_]+)\s*=.*", lines[index])
        if match and match.group(1) in replacements:
            name = match.group(1)
            if name in found:
                raise ConfigError(f"duplicate {name} in {header}")
            lines[index] = f"  {name} = {json.dumps(replacements[name])}"
            found.add(name)
    missing = sorted(set(replacements) - found)
    if missing:
        raise ConfigError(f"missing protected variables in {header}: {', '.join(missing)}")


def render_config(source_text: str, environment: str, values: dict[str, str]) -> str:
    if environment not in ENVIRONMENTS:
        raise ConfigError("environment must be staging or production")
    tomllib.loads(source_text)
    clean = _validate(values)
    lines = source_text.splitlines()
    _replace_section_values(
        lines,
        f"env.{environment}.vars",
        {name: clean[name] for name in VAR_NAMES},
    )
    _replace_section_values(
        lines,
        f"env.{environment}.r2_buckets",
        {"bucket_name": clean["R2_BUCKET_NAME"]},
    )
    rendered = "\n".join(lines) + "\n"
    parsed = tomllib.loads(rendered)
    target = parsed["env"][environment]
    for name in VAR_NAMES:
        if target["vars"].get(name) != clean[name]:
            raise ConfigError(f"rendered {name} did not round-trip")
    buckets = target.get("r2_buckets", [])
    if len(buckets) != 1 or buckets[0].get("bucket_name") != clean["R2_BUCKET_NAME"]:
        raise ConfigError("rendered R2 binding did not round-trip")
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", required=True, choices=sorted(ENVIRONMENTS))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents:
        raise ConfigError("protected config output must be outside the repository")
    values = {name: os.environ.get(name, "") for name in (*VAR_NAMES, "R2_BUCKET_NAME")}
    rendered = render_config(args.source.read_text(encoding="utf-8"), args.environment, values)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)
    try:
        os.chmod(output, 0o600)
    except OSError:
        pass
    print(f"rendered protected {args.environment} Worker config")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
