#!/usr/bin/env python3
"""知识库 PR 闸门：schema、引用完整性、区间语法、id 稳定性、体量。

    python3.12 scripts/validate.py [--quiet]

为什么不用 jsonschema 库：CI 与开发机上装它要额外一步，而这里要查的规则是固定的几条；
手写检查反而能给出「哪个文件哪一行、错在哪」的中文报错。

需要 Python ≥ 3.11（`tomllib`）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS = ROOT / "products"
ADVISORIES = ROOT / "advisories"
BULK = ROOT / "bulk"

PACKAGE_TYPES = {
    "deb", "rpm", "apk", "brew", "app", "npm", "pypi",
    "go", "cargo", "maven", "nuget", "gem", "unknown",
}
ID_KINDS = {"purl", "cpe", "swid", "vendor_product_id"}
SOURCES = {"curated", "osv", "nvd", "distro", "vendor"}
LIFECYCLE = {"supported", "maintenance", "eol", "unknown"}
MAX_FILE_BYTES = 100 * 1024 * 1024      # GitHub 硬限
WARN_FILE_BYTES = 20 * 1024 * 1024      # 分片建议上限

errors: list[str] = []
warnings: list[str] = []


def err(where: str, msg: str) -> None:
    errors.append(f"{where}: {msg}")


def warn(where: str, msg: str) -> None:
    warnings.append(f"{where}: {msg}")


def load_toml(path: Path):
    try:
        import tomllib  # noqa: PLC0415  (Python ≥ 3.11)
    except ModuleNotFoundError:  # pragma: no cover
        sys.exit(
            "需要 Python ≥ 3.11（tomllib）。\n"
            "  macOS: brew install python@3.12 && python3.12 scripts/validate.py\n"
            "  CI 用 ubuntu-24.04 自带 Python 3.12。"
        )
    with path.open("rb") as handle:
        try:
            return tomllib.load(handle)
        except Exception as exc:  # noqa: BLE001
            err(str(path.relative_to(ROOT)), f"TOML 解析失败：{exc}")
            return None


def normalize_purl(purl: str) -> str:
    """去 `@版本`、去 `?qualifiers`、小写 —— id 派生的唯一口径。"""
    return purl.split("@", 1)[0].split("?", 1)[0].strip().lower()


def product_id_for(purl: str) -> str:
    return "sha256:" + hashlib.sha256(normalize_purl(purl).encode()).hexdigest()[:32]


def check_product(path: Path, doc: dict, ids: dict[str, Path]) -> None:
    where = str(path.relative_to(ROOT))
    for key in ("product_id", "canonical_name", "package_type", "external_ids"):
        if key not in doc:
            err(where, f"缺字段 `{key}`")
            return

    pid = doc["product_id"]
    if not (isinstance(pid, str) and pid.startswith("sha256:") and len(pid) == len("sha256:") + 32):
        err(where, f"product_id 形状不对：{pid!r}（应为 sha256:+32 位 hex）")

    pkg_type = doc["package_type"]
    if pkg_type not in PACKAGE_TYPES:
        err(where, f"package_type 不在词表内：{pkg_type!r}")

    lifecycle = doc.get("lifecycle_state")
    if lifecycle is not None and lifecycle not in LIFECYCLE:
        err(where, f"lifecycle_state 不在词表内：{lifecycle!r}")

    ext = doc["external_ids"]
    if not isinstance(ext, list) or not ext:
        err(where, "external_ids 必须是非空数组")
        return
    purls = []
    for item in ext:
        if not isinstance(item, dict) or "kind" not in item or "value" not in item:
            err(where, "external_ids 每项必须有 kind / value")
            continue
        if item["kind"] not in ID_KINDS:
            err(where, f"external_ids.kind 不在词表内：{item['kind']!r}")
        if item["kind"] == "purl":
            purls.append(item["value"])

    # id 稳定性：用**第一个 purl** 重算，必须与文件里声明的一致
    if purls:
        expected = product_id_for(purls[0])
        if expected != pid:
            err(
                where,
                f"product_id 与 purl 派生值不一致：文件 {pid}，按 {normalize_purl(purls[0])} 应为 {expected}",
            )
    else:
        warn(where, "没有 purl，只有 CPE/SWID：id 派生走退化口径（vendor|name），请人工复核")

    if pid in ids:
        err(where, f"product_id 与 {ids[pid].relative_to(ROOT)} 重复：{pid}")
    else:
        ids[pid] = path


def check_advisory(path: Path, doc: dict, product_ids: set[str], seen: dict[str, Path]) -> None:
    where = str(path.relative_to(ROOT))
    for key in ("osv_id", "product_id", "ecosystem", "affected", "source", "references"):
        if key not in doc:
            err(where, f"缺字段 `{key}`")
            return

    osv_id = doc["osv_id"]
    if osv_id in seen:
        err(where, f"osv_id 与 {seen[osv_id].relative_to(ROOT)} 重复：{osv_id}")
    else:
        seen[osv_id] = path

    if doc["source"] not in SOURCES:
        err(where, f"source 不在词表内：{doc['source']!r}")

    pid = doc["product_id"]
    if pid not in product_ids:
        err(where, f"product_id 指向不存在的产品：{pid}")

    ecosystem = doc["ecosystem"]
    if not isinstance(ecosystem, str) or not ecosystem.strip():
        err(where, "ecosystem 不能为空")

    affected = doc["affected"]
    if not isinstance(affected, list) or not affected:
        err(where, "affected 必须是非空数组")
    else:
        for index, span in enumerate(affected):
            if not isinstance(span, dict):
                err(where, f"affected[{index}] 不是表")
                continue
            keys = {"introduced", "fixed", "last_affected"} & span.keys()
            if not keys:
                err(where, f"affected[{index}] 至少要有一个 introduced / fixed / last_affected")
            for key in keys:
                value = span[key]
                if not isinstance(value, str) or not value.strip():
                    err(where, f"affected[{index}].{key} 不能为空")

    refs = doc["references"]
    if not isinstance(refs, list) or not refs:
        err(where, "references 不能为空（只存链接，不转存全文）")


def check_bulk() -> int:
    count = 0
    for path in sorted(BULK.rglob("*.jsonl")):
        where = str(path.relative_to(ROOT))
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            err(where, f"单文件 {size // 1024 // 1024} MB 超过 GitHub 100 MB 硬限")
        elif size > WARN_FILE_BYTES:
            warn(where, f"单文件 {size // 1024 // 1024} MB，建议再分片")
        with path.open(encoding="utf-8") as handle:
            for lineno, line in enumerate(handle, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    err(where, f"第 {lineno} 行不是合法 JSON：{exc}")
                    continue
                if not isinstance(record, dict) or "id" not in record:
                    err(where, f"第 {lineno} 行缺 `id`")
                count += 1
    return count


def main() -> int:
    kb = load_toml(ROOT / "kb.toml")
    if kb is None:
        return 1
    if "schema_version" not in kb:
        err("kb.toml", "缺 schema_version")
    if "kb_version" not in kb:
        err("kb.toml", "缺 kb_version")

    product_ids: dict[str, Path] = {}
    for path in sorted(PRODUCTS.rglob("*.toml")):
        doc = load_toml(path)
        if doc is not None:
            check_product(path, doc, product_ids)

    advisory_ids: dict[str, Path] = {}
    known = set(product_ids)
    for path in sorted(ADVISORIES.rglob("*.toml")):
        doc = load_toml(path)
        if doc is not None:
            check_advisory(path, doc, known, advisory_ids)

    bulk_records = check_bulk()

    print(f"products   {len(product_ids)}")
    print(f"advisories {len(advisory_ids)}（curated）")
    print(f"bulk       {bulk_records} 行")
    if warnings:
        print("\n警告：")
        for item in warnings:
            print(f"  - {item}")
    if errors:
        print("\n错误：", file=sys.stderr)
        for item in errors:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
