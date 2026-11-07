#!/usr/bin/env python3
"""知识库 PR 闸门：产品身份、识别判据、部署画像、公告、bulk 分片。

    python3.12 scripts/validate.py [--quiet]

三层分工（对应 `wist-design/doc/design/foundation/software-identity.md`）：

    products/    产品固有：权威名 + 功能类别 + 它在各生态里「叫什么」（识别判据）
    profiles/    部署画像：认出之后「怎么管」（随装法/环境变）
    advisories/  人工修正与供应商公告（curated）
    bulk/        上游镜像分片（机器生成）

为什么不用 jsonschema 库：CI 与开发机上装它要额外一步，而这里要查的规则是固定的几条；
手写检查反而能给出「哪个文件哪一行、错在哪」的中文报错。

需要 Python ≥ 3.11（`tomllib`）。
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRODUCTS = ROOT / "products"
PROFILES = ROOT / "profiles"
ADVISORIES = ROOT / "advisories"
BULK = ROOT / "bulk"

PACKAGE_TYPES = {
    "deb", "rpm", "apk", "brew", "app", "npm", "pypi",
    "go", "cargo", "maven", "nuget", "gem",
    # purl 自带的两个 type，实测必需：
    #   generic   tarball / 静态二进制（kafka、elasticsearch、openjdk）
    #   oci       只能从镜像/二进制认出的服务（warp-parse、victoria-metrics）
    "generic", "oci", "unknown",
}
ID_KINDS = {"purl", "cpe", "swid", "vendor_product_id"}
SOURCES = {"curated", "osv", "nvd", "distro", "vendor"}
LIFECYCLE = {"supported", "maintenance", "eol", "unknown"}
MAX_FILE_BYTES = 100 * 1024 * 1024      # GitHub 硬限
WARN_FILE_BYTES = 20 * 1024 * 1024      # 分片建议上限

# 功能类别（**多值**）：一个软件可以同时是 search 和 database。
# 闭集是硬约束：它会进查询条件，开放取值会让历史查询不可比。
CATEGORIES = {
    # 每个值都必须绑定一个「不同的动作或查询」；没有绑定关系的值不进闭集。
    # 2026-11-07 收缩：删 monitoring（并入 database）、security（与 vendor 重复）、
    # gateway（与 web-server 重合）；新增 application（业务应用：无包记录、
    # 版本来自镜像 tag、升级=滚动、只有 stdout 面）。
    "database", "cache", "message-queue", "search", "web-server",
    "runtime", "pipeline", "application", "unknown",
}
# `kind` 是**另一套维度**：它是「什么东西」。与 categories 正交。
KINDS = {"daemon", "runtime", "agent", "library", "cli", "kernel-module"}
# 部署形态：一个产品可以有多个 profile（同样的软件，装法不同，治理也不同）。
PROFILE_FORMS = {"default", "deb", "rpm", "apk", "tarball", "container", "macos", "k8s"}
BIND_SCOPES = {"public", "internal", "loopback", "none"}
UPGRADE_MODES = {"restart", "reload", "rolling", "none", "unknown"}
VERSION_PROBE_KINDS = {"package", "binary", "image_tag", "path_regex", "none"}
# 判据种类（扁平化：旧版的 binaries/units/containers 改为 path/unit/container）
RECOGNIZE_KINDS = {"dpkg", "apk", "rpm", "path", "unit", "container", "image"}
# 匹配方式：image 默认子串，但 `wist-atlas` 会命中 `wist-atlas-web` —— 需要 suffix。
MATCH_MODES = {"substring", "suffix"}
# 判据强度：决定「这条命中能不能单独作数」，以及多条命中时谁优先。
STRENGTHS = {"strong", "medium", "weak"}

# 出处：这条判据你凭什么这么写。它决定规则失效时该查哪。
#   measured    至少一条 recognize 判据在真实环境命中过（不表示每条都验过）
#   vendor-doc  官方文档/仓库写明，但没在真实环境验过 —— 必须带 source_url
#   inferred    按惯例推的，未核实 —— 失败时先怀疑规则，别先怀疑采集
PROVENANCE = {"measured", "vendor-doc", "inferred"}
VERIFIED_ON_RE = re.compile(r"^[a-z0-9._-]+@\d{4}-\d{2}-\d{2}$")
UNVERIFIED_RE = re.compile(
    r"^(recognize(\.(dpkg|apk|rpm|path|unit|container|image))?"
    r"|version_probe|governance\.[a-z_]+|observability\.[a-z_]+)$"
)
STALE_DAYS = 90

errors: list[str] = []
warnings: list[str] = []
provenance_count: dict[str, int] = {}
inferred_products: list[str] = []
unverified_products: list[tuple[str, list[str]]] = []
unclassified: list[str] = []
bundle_refs: list[tuple[Path, str, str | None]] = []


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
            "  macOS: brew install python3@3.12 && python3.12 scripts/validate.py\n"
            "  CI 用 ubuntu-24.04 自带 Python 3.12。"
        )
    with path.open("rb") as handle:
        try:
            return tomllib.load(handle)
        except Exception as exc:  # noqa: BLE001
            err(str(path.relative_to(ROOT)), f"TOML 解析失败：{exc}")
            return None


def normalize_purl(purl: str) -> str:
    """去 `@版本`、去 `?qualifiers`、小写 —— 只用于别名去重比对。

    它**不再**派生产品 id：purl 只回答「它在某个生态里叫什么」，
    不回答「这是哪个产品」（redis-server / redis / Redis 是同一个东西）。
    """
    return purl.split("@", 1)[0].split("?", 1)[0].strip().lower()


CANONICAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]*/[a-z0-9][a-z0-9._+-]*$")


def product_id_for(canonical_id: str) -> str:
    """权威名的唯一来源：`<vendor>/<name>` → `sha256:<32 hex>`。"""
    return "sha256:" + hashlib.sha256(canonical_id.strip().lower().encode()).hexdigest()[:32]


# ── products/ ────────────────────────────────────────────────────────────────


def check_product(path: Path, doc: dict, ids: dict[str, Path],
                  purl_owner: dict[str, tuple[str, Path]],
                  seen_criteria: dict[tuple[str, str], tuple[str, Path]]) -> None:
    where = str(path.relative_to(ROOT))
    for key in ("product_id", "canonical_id", "canonical_name", "package_type", "external_ids"):
        if key not in doc:
            err(where, f"缺字段 `{key}`")
            return

    pid = doc["product_id"]
    if not (isinstance(pid, str) and pid.startswith("sha256:") and len(pid) == len("sha256:") + 32):
        err(where, f"product_id 形状不对：{pid!r}（应为 sha256:+32 位 hex）")

    cid = doc["canonical_id"]
    if not isinstance(cid, str) or not CANONICAL_ID_RE.match(cid):
        err(where, f"canonical_id 格式不对：{cid!r}（应为小写 `<vendor>/<name>`，仅 a-z0-9 . _ + -）")
    else:
        expected = product_id_for(cid)
        if expected != pid:
            err(where, f"product_id 与 canonical_id 派生值不一致：文件 {pid}，按 {cid} 应为 {expected}")

    if doc["package_type"] not in PACKAGE_TYPES:
        err(where, f"package_type 不在词表内：{doc['package_type']!r}")

    lifecycle = doc.get("lifecycle_state")
    if lifecycle is not None and lifecycle not in LIFECYCLE:
        err(where, f"lifecycle_state 不在词表内：{lifecycle!r}")

    # 功能类别：**多值**（elasticsearch 同时是 search 和 database）。
    cats = doc.get("categories")
    if not isinstance(cats, list) or not cats:
        err(where, 'categories 必须是非空数组（如 ["database"]）')
    else:
        for cat in cats:
            if cat not in CATEGORIES:
                err(where, f"categories 含闭集外的值：{cat!r}")
        if len(set(cats)) != len(cats):
            err(where, f"categories 有重复值：{cats}")

    if cats == ["unknown"]:
        unclassified.append(doc.get("canonical_name") or path.stem)

    if doc.get("kind") not in KINDS:
        err(where, f"kind 不在闭集内：{doc.get('kind')!r}（可用：{'、'.join(sorted(KINDS))}）")

    ports = doc.get("default_ports", [])
    if not isinstance(ports, list):
        err(where, "default_ports 必须是数组")
        ports = []
    for port in ports:
        if not isinstance(port, int) or not (0 < port < 65536):
            err(where, f"default_ports 端口非法：{port!r}")
        elif port >= 32768:
            warn(where, f"default_ports 里 {port} 属临时端口区（>=32768），不能当稳定服务端口")

    ext = doc["external_ids"]
    if not isinstance(ext, list) or not ext:
        err(where, "external_ids 必须是非空数组")
        ext = []
    purls = []
    for item in ext:
        if not isinstance(item, dict) or "kind" not in item or "value" not in item:
            err(where, "external_ids 每项必须有 kind / value")
            continue
        if item["kind"] not in ID_KINDS:
            err(where, f"external_ids.kind 不在词表内：{item['kind']!r}")
        if item["kind"] == "purl":
            purls.append(item["value"])

    # purl 降为「别名」：只登记，不派生 id。但它不能同时属于两个产品。
    if not purls:
        warn(where, "没有 purl 别名：只能靠 CPE/SWID 或纯手工维护映射")
    for purl in purls:
        key = normalize_purl(purl)
        owner = purl_owner.get(key)
        if owner and owner[0] != cid:
            err(
                where,
                f"purl {key} 已被 {owner[1].relative_to(ROOT)}（canonical_id={owner[0]}）占用，"
                f"又被 {cid} 引用 —— 要么它们是同一个产品，要么其中一条的 purl 写错了",
            )
        else:
            purl_owner[key] = (cid, path)

    check_recognition(path, doc, pid, seen_criteria)
    check_bundles(path, doc)
    check_provenance(path, doc)

    if pid in ids:
        err(where, f"product_id 与 {ids[pid].relative_to(ROOT)} 重复：{pid}（同一个 canonical_id 只能出现一次）")
    else:
        ids[pid] = path


def check_recognition(path: Path, doc: dict, pid: str,
                      seen_criteria: dict[tuple[str, str], tuple[str, Path]]) -> None:
    """识别判据：产品固有 —— 换装法也基本成立（包名/路径/unit/容器/镜像）。"""
    where = str(path.relative_to(ROOT))
    rec = doc.get("recognize")
    if not isinstance(rec, list) or not rec:
        err(where, "recognize 必须是至少一条 [[recognize]] 表")
        return

    kinds_used = set()
    for idx, entry in enumerate(rec):
        tag = f"recognize[{idx}]"
        if not isinstance(entry, dict):
            err(where, f"{tag} 必须是表")
            continue
        kind = entry.get("kind")
        strength = entry.get("strength")
        values = entry.get("values")

        if kind not in RECOGNIZE_KINDS:
            err(where, f"{tag}.kind 不在闭集内：{kind!r}（可用：{'、'.join(sorted(RECOGNIZE_KINDS))}）")
        else:
            kinds_used.add(kind)
        if strength not in STRENGTHS:
            err(where, f"{tag}.strength 不在闭集内：{strength!r}（可用：{'、'.join(sorted(STRENGTHS))}）")
        mode = entry.get("match", "substring")
        if mode not in MATCH_MODES:
            err(where, f"{tag}.match 不在闭集内：{mode!r}（可用：{'、'.join(sorted(MATCH_MODES))}）")

        if not isinstance(values, list) or not values:
            err(where, f"{tag}.values 必须是非空数组")
            continue

        for value in values:
            if not isinstance(value, str) or not value.strip():
                err(where, f"{tag}.values 有空值")
                continue
            literal = value.replace("*", "")
            # 实战证明 `*` 必须允许出现在中间：包名嵌主版本（openjdk-*-jre）、
            # 路径嵌版本目录（/usr/lib/postgresql/*/bin/postgres）。
            if literal.strip() == "":
                err(where, f"{tag}.values 不能是裸 `*`：{value!r}")
            elif value.startswith("*"):
                err(where, f"{tag}.values 不能以 `*` 开头（会匹配一切）：{value!r}")
            elif len(literal) < 3:
                err(where, f"{tag}.values 字面部分太短（<3 字符），会误匹配：{value!r}")
            elif "*" in value.rstrip("*"):
                warn(where, f"{tag}.values 含内嵌通配，依赖生态命名约定，改版时容易失效：{value!r}")

            # 同一个观测（kind:value）只能指向一个产品。
            # 这是「一个产品裂成两条」和「mariadb 被当成 mysql」的共同症状。
            if kind in RECOGNIZE_KINDS:
                ckey = (kind, value)
                owner = seen_criteria.get(ckey)
                if owner is not None and owner[0] != pid:
                    err(where, f"判据 {kind}:{value} 已被 {owner[1].relative_to(ROOT)} 认领 —— 同一个观测不能指向两个产品")
                else:
                    seen_criteria[ckey] = (pid, path)

        if kind == "container" and strength != "weak":
            warn(where, f"{tag}: container 名是部署者起的，不该标 {strength} —— 实测同一镜像可叫 metrics / vm")
        if kind == "image" and strength == "strong":
            warn(where, f"{tag}: image 是子串匹配，标 strong 容易误判（nginx 会命中 nginx-ingress）")

    if not (kinds_used & {"dpkg", "apk", "rpm"}):
        warn(where, "没有包名判据 —— 只能靠路径/unit/镜像认，版本往往也拿不到（如实记录，不是错）")


def check_bundles(path: Path, doc: dict) -> None:
    """自带运行时：产品 bundle 进来、但**不通过包管理器安装**的东西（JDK、Erlang…）。

    和 `requires` 的区别：`requires` 是「外部必须有」，`bundles` 是「它自己带着」。
    版本靠探测（probe）而不是声明 —— 否则外层产品一升级，声明就腐化。
    """
    where = str(path.relative_to(ROOT))
    bundles = doc.get("bundles") or []
    if not isinstance(bundles, list):
        err(where, "bundles 必须是数组（[[bundles]]）")
        return

    for idx, item in enumerate(bundles):
        tag = f"bundles[{idx}]"
        if not isinstance(item, dict):
            err(where, f"{tag} 必须是表")
            continue
        ref = item.get("product")
        if not isinstance(ref, str) or "/" not in ref:
            err(where, f'{tag}.product 必须是 canonical_id（如 "openjdk/openjdk"）')
            continue
        probe = item.get("probe")
        if not isinstance(probe, dict):
            err(where, f"{tag}.probe 缺失 —— 自带运行时的版本必须能探测出来")
            continue
        if probe.get("kind") not in VERSION_PROBE_KINDS:
            err(where, f"{tag}.probe.kind 不在闭集内：{probe.get('kind')!r}")
        if probe.get("kind") == "binary" and not probe.get("command"):
            err(where, f"{tag}.probe.kind=binary 必须写 `command`")
        probe_path = None
        command = probe.get("command")
        if probe.get("kind") == "binary" and isinstance(command, list) and command:
            if isinstance(command[0], str) and command[0].startswith("/"):
                probe_path = command[0]
        bundle_refs.append((path, ref, probe_path))


def check_provenance(path: Path, doc: dict) -> None:
    where = str(path.relative_to(ROOT))
    label = path.stem
    prov = doc.get("provenance")
    if prov not in PROVENANCE:
        err(where, f"provenance 不在闭集内：{prov!r}")
        return

    provenance_count[prov] = provenance_count.get(prov, 0) + 1
    if prov == "inferred":
        inferred_products.append(label)

    verified = doc.get("verified_on") or []
    if prov == "measured":
        if not verified:
            err(where, "provenance=measured 必须写 `verified_on`（在哪个环境、哪天验的）")
        for entry in verified:
            if not isinstance(entry, str) or not VERIFIED_ON_RE.match(entry):
                err(where, f"verified_on 格式不对：{entry!r}（应为 `<env>@YYYY-MM-DD`）")
                continue
            try:
                when = date.fromisoformat(entry.rsplit("@", 1)[1])
            except ValueError:
                err(where, f"verified_on 日期非法：{entry!r}")
                continue
            age = (date.today() - when).days
            if age > STALE_DAYS:
                warn(where, f"verified_on 已过期 {age} 天（> {STALE_DAYS}）：{entry} —— 建议复验")
    elif prov == "vendor-doc":
        if not doc.get("source_url"):
            err(where, "provenance=vendor-doc 必须写 `source_url`（哪份文档说的）")
    elif verified:
        warn(where, "provenance=inferred 却写了 verified_on —— 要么改 measured，要么删掉")

    unverified = doc.get("unverified") or []
    if not isinstance(unverified, list):
        err(where, 'unverified 必须是数组（如 ["recognize.unit"]）')
        unverified = []
    for item in unverified:
        if not isinstance(item, str) or not UNVERIFIED_RE.match(item):
            err(where, f"unverified 条目格式不对：{item!r}（应为 recognize[.kind] / version_probe / governance.<key>）")
    if unverified and prov == "inferred":
        warn(where, "provenance=inferred 时不必再列 unverified（整个产品都没验）")
    if unverified:
        unverified_products.append((label, [u.replace("recognize.", "") or "all" for u in unverified]))


# ── profiles/ ────────────────────────────────────────────────────────────────


def check_profile(path: Path, doc: dict, product_ids: set[str],
                  seen_form: dict[tuple[str, str], Path]) -> None:
    """部署画像：认出之后「怎么管」。同一产品可以有多个（systemd / k8s …）。"""
    where = str(path.relative_to(ROOT))
    for key in ("product_id", "form"):
        if key not in doc:
            err(where, f"缺字段 `{key}`")
            return

    pid = doc["product_id"]
    if pid not in product_ids:
        err(where, f"product_id 指向不存在的产品：{pid}（先加到 products/）")

    form = doc["form"]
    if form not in PROFILE_FORMS:
        err(where, f"form 不在闭集内：{form!r}（可用：{'、'.join(sorted(PROFILE_FORMS))}）")
    elif (pid, form) in seen_form:
        err(where, f"{pid} 的 form={form} 已由 {seen_form[(pid, form)].relative_to(ROOT)} 定义（同一形态只能一个 profile）")
    else:
        seen_form[(pid, form)] = path

    rt = doc.get("runtime", {})
    if rt and rt.get("bind_scope") not in BIND_SCOPES:
        err(where, f"runtime.bind_scope 不在闭集内：{rt.get('bind_scope')!r}")

    gov = doc.get("governance", {})
    if gov and gov.get("upgrade_mode") not in UPGRADE_MODES:
        err(where, f"governance.upgrade_mode 不在闭集内：{gov.get('upgrade_mode')!r}")

    vp = doc.get("version_probe")
    if vp is not None:
        if vp.get("kind") not in VERSION_PROBE_KINDS:
            err(where, f"version_probe.kind 不在闭集内：{vp.get('kind')!r}")
        elif vp.get("kind") == "binary" and not vp.get("command"):
            err(where, "version_probe.kind=binary 必须写 `command`")

    if not (rt or gov or vp or doc.get("observability")):
        warn(where, "profile 里没有任何策略字段（runtime / governance / version_probe / observability）")


# ── advisories/ 与 bulk/ ─────────────────────────────────────────────────────


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
            err(where, f"单文件超过 GitHub 硬限 {MAX_FILE_BYTES} 字节")
        elif size > WARN_FILE_BYTES:
            warn(where, f"单文件 {size} 字节 > 建议上限 {WARN_FILE_BYTES}，考虑再分片")
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
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
    product_docs: list[tuple[Path, dict]] = []
    purl_owner: dict[str, tuple[str, Path]] = {}
    seen_criteria: dict[tuple[str, str], tuple[str, Path]] = {}

    for path in sorted(PRODUCTS.rglob("*.toml")):
        doc = load_toml(path)
        if doc is not None:
            check_product(path, doc, product_ids, purl_owner, seen_criteria)
            product_docs.append((path, doc))

    # `requires` 用 canonical_id 互相引用（canonical_id 不可变；canonical_name 可以改）。
    canonical_ids = {doc.get("canonical_id") for _, doc in product_docs}
    for path, doc in product_docs:
        for ref in doc.get("requires") or []:
            if ref not in canonical_ids:
                err(str(path.relative_to(ROOT)), f"requires 指向不存在的 canonical_id：{ref!r}")

    # bundles：引用要存在；而且**探测路径不能同时是它的识别判据** ——
    # 否则「自带」会被认成「独立安装」（apache/kafka 里的 Temurin 就是这样被误判的）。
    by_cid = {doc.get("canonical_id"): doc for _, doc in product_docs}
    for path, ref, probe_path in bundle_refs:
        where = str(path.relative_to(ROOT))
        target = by_cid.get(ref)
        if target is None:
            err(where, f"bundles 指向不存在的 canonical_id：{ref!r}")
            continue
        if not probe_path:
            continue
        for entry in target.get("recognize") or []:
            if entry.get("kind") != "path":
                continue
            for value in entry.get("values") or []:
                if fnmatch.fnmatchcase(probe_path, value):
                    err(
                        where,
                        f"bundles 的探测路径 {probe_path} 同时是 {ref} 的识别判据 {value!r} "
                        f"—— 它会被认成独立安装的产品",
                    )

    advisory_ids: dict[str, Path] = {}
    known = set(product_ids)
    for path in sorted(ADVISORIES.rglob("*.toml")):
        doc = load_toml(path)
        if doc is not None:
            check_advisory(path, doc, known, advisory_ids)

    bulk_records = check_bulk()

    profile_paths = sorted(PROFILES.rglob("*.toml")) if PROFILES.exists() else []
    seen_form: dict[tuple[str, str], Path] = {}
    for path in profile_paths:
        doc = load_toml(path)
        if doc is not None:
            check_profile(path, doc, known, seen_form)

    print(f"products    {len(product_ids)}")
    prov = " / ".join(f"{k} {v}" for k, v in sorted(provenance_count.items())) or "无"
    print(f"profiles    {len(profile_paths)}（{prov}）")
    if inferred_products:
        print(f"            仍为 inferred：{'、'.join(sorted(inferred_products))}")
    if unverified_products:
        detail = "、".join(f"{name}[{'/'.join(items)}]" for name, items in sorted(unverified_products))
        print(f"            判据级未验证：{detail}")
    if unclassified:
        print(f"            待归类（categories 只有 unknown）：{'、'.join(sorted(unclassified))}")
    print(f"advisories  {len(advisory_ids)}（curated）")
    if BULK.exists():
        print(f"bulk        {bulk_records} 行")
    else:
        print("bulk        未建（机器生成的镜像层，等导入器与生成器）")

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
