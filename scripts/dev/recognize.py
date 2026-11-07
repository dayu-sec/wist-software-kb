#!/usr/bin/env python3
"""判据原型：把「机器上的事实」对上 `profiles/` 里的 recognize 规则。

    python3.12 scripts/dev/recognize.py --docker        # 对本机运行中的容器跑一遍
    python3.12 scripts/dev/recognize.py --docker --all  # 连未命中的容器一起列出

**这是 dev 工具，不是发布链路的一部分**（需要本机 Docker，扫的是运行中的容器）。
用途：改了 `products/` 的判据后，用它回归一遍「还能不能认出真实镜像」。

**这是 spike，不是成品。** 目的只有一个：用真实环境验证「产品固有 + 部署画像」这个形状。
已知不做的事：
  * 不做版本区间比较（那是 KB 导入侧的事）；
  * 不反查 `/proc/<pid>/exe`（本机没有 Linux 生产机，macOS 的判据完全不同）；
  * 不处理 systemd unit（容器里没有 systemd）。

需要 Python ≥ 3.11（`tomllib`）。
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PROFILES = ROOT / "profiles"
PRODUCTS = ROOT / "products"

PKG_KINDS = ("dpkg", "apk", "rpm")
PKG_QUERY = {
    "dpkg": "dpkg-query -W -f='${Package}\\t${Version}\\n' 2>/dev/null",
    "apk": "apk info -v 2>/dev/null",
    "rpm": "rpm -qa --qf '%{NAME}\\t%{VERSION}-%{RELEASE}\\n' 2>/dev/null",
}
RANK = {"strong": 3, "medium": 2, "weak": 1}


def load_products() -> dict[str, dict]:
    out = {}
    for path in PRODUCTS.rglob("*.toml"):
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
        out[doc["product_id"]] = doc
    return out


def load_units() -> list[dict]:
    """判据在产品层（产品固有）；profiles 只放治理策略，不参与识别。"""
    units = []
    for path in sorted(PRODUCTS.rglob("*.toml")):
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
        units.append(
            {
                "file": str(path.relative_to(ROOT)),
                "product_id": doc["product_id"],
                "name": doc.get("canonical_name", "?"),
                "categories": doc.get("categories", []),
                "criteria": doc.get("recognize", []),
                "bundles": doc.get("bundles", []),
            }
        )
    return units


def sh(cmd: list[str], stdin: str | None = None) -> str:
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=120).stdout


def container_packages(name: str) -> tuple[str, dict[str, str]]:
    probe = sh(["docker", "exec", name, "sh", "-c",
                "command -v dpkg-query || command -v apk || command -v rpm || echo none"]).strip()
    mgr = ""
    if probe.endswith("dpkg-query"):
        mgr = "dpkg"
    elif probe.endswith("apk"):
        mgr = "apk"
    elif probe.endswith("rpm"):
        mgr = "rpm"
    if not mgr:
        return "", {}

    raw = sh(["docker", "exec", name, "sh", "-c", PKG_QUERY[mgr]])
    packages = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if mgr == "apk":
            # `nginx-1.27.5-r1` → name=nginx, version=1.27.5-r1
            head, _, tail = line.rpartition("-")
            name_part, _, ver_core = head.rpartition("-")
            packages[name_part] = f"{ver_core}-{tail}"
        elif "\t" in line:
            pkg, _, version = line.partition("\t")
            packages[pkg] = version
        else:
            packages[line] = ""
    return mgr, packages


def probe_version(container: str, probe: dict) -> str:
    """探测自带运行时的版本（stdout + stderr 都看：java -version 走 stderr）。"""
    kind = probe.get("kind")
    if kind == "binary" and probe.get("command"):
        out = subprocess.run(
            ["docker", "exec", container, *probe["command"]],
            capture_output=True, text=True, timeout=60,
        )
        text = f"{out.stdout}\n{out.stderr}"
    elif kind == "image_tag":
        text = container_image(container)
    else:
        return "?"
    m = re.search(r'version "([^"]+)"', text) or re.search(r"\b(\d+\.\d+(?:\.\d+)*)\b", text)
    return m.group(1) if m else "?"


def container_image(name: str) -> str:
    return sh(["docker", "inspect", "-f", "{{.Config.Image}}", name]).strip()


def container_paths(name: str, patterns: list[str]) -> set[str]:
    if not patterns:
        return set()
    script = "for p in " + " ".join(f"'{p}'" for p in patterns) + \
             '; do for f in $p; do [ -e "$f" ] && echo "HIT|$p"; done; done\n'
    out = sh(["docker", "exec", "-i", name, "sh", "-s"], stdin=script)
    return {line.split("|", 1)[1] for line in out.splitlines() if line.startswith("HIT|")}


def match(units: list[dict], name: str) -> tuple[list[dict], str, int, int]:
    mgr, packages = container_packages(name)
    image = container_image(name)
    all_patterns = sorted({
        p for u in units for c in u["criteria"]
        if c.get("kind") == "path" for p in c.get("values", [])
    })
    found_paths = container_paths(name, all_patterns)

    hits = []
    matched_pkgs: set[str] = set()
    for unit in units:
        evidence = []
        best = 0
        version = ""
        for crit in unit["criteria"]:
            kind = crit.get("kind")
            strength = crit.get("strength", "weak")
            for value in crit.get("values", []):
                hit = False
                if kind in PKG_KINDS:
                    # 只跟自己生态比：dpkg 的清单拿去匹配 apk 规则是错的
                    if kind == mgr:
                        for pkg, ver in packages.items():
                            if fnmatch.fnmatchcase(pkg, value):
                                evidence.append((kind, value, f"{pkg}={ver}", strength))
                                matched_pkgs.add(pkg)
                                version = version or ver
                                hit = True
                                break
                elif kind == "path":
                    if value in found_paths:
                        evidence.append((kind, value, value, strength))
                        hit = True
                elif kind == "image":
                    if crit.get("match") == "suffix":
                        # 只比仓库名（去 registry 前缀和 tag）：wist-atlas != wist-atlas-web
                        repo = image.rsplit("/", 1)[-1].split(":", 1)[0].lower()
                        matched = repo == value.lower()
                    else:
                        matched = value.lower() in image.lower()
                    if matched:
                        evidence.append((kind, value, image, strength))
                        hit = True
                elif kind == "container":
                    if fnmatch.fnmatchcase(name, value) or value == name:
                        evidence.append((kind, value, name, strength))
                        hit = True
                if hit:
                    best = max(best, RANK.get(strength, 0))
        if evidence:
            hits.append({**unit, "evidence": evidence, "version": version,
                         "strength": {v: k for k, v in RANK.items()}.get(best, "weak"),
                         "via": mgr or "none"})
    return hits, image, len(packages), len(matched_pkgs)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--docker", action="store_true", help="对本机运行中的容器跑")
    ap.add_argument("--all", action="store_true", help="连未命中的容器也列出来")
    args = ap.parse_args()

    units = load_units()
    if not args.docker:
        ap.error("目前只实现了 --docker")

    listing = sh(["docker", "ps", "--format", "{{.Names}}"]).split()
    print(f"profile {len(units)} 个；容器 {len(listing)} 个\n")

    covered: dict[str, list[str]] = {u["name"]: [] for u in units}
    unmatched = []
    total_pkgs = total_matched = 0
    for name in sorted(listing):
        hits, image, pkg_count, matched_count = match(units, name)
        total_pkgs += pkg_count
        total_matched += matched_count
        if not hits:
            unmatched.append((name, image, pkg_count))
            continue
        print(f"{name}   [{image}]")
        for hit in hits:
            version = f"  v{hit['version']}" if hit["version"] else ""
            cats = ",".join(hit["categories"])
            print(f"  → {hit['name']:<20} {cats:<22} {hit['strength']:<6}{version}")
            shown = " + ".join(f"{k}:{v}[{s[0]}]" for k, v, _, s in hit["evidence"][:3])
            print(f"      {shown}")
            for bundle in hit.get("bundles") or []:
                ver = probe_version(name, bundle.get("probe") or {})
                print(f"      └ bundles {bundle.get('product')} v{ver}")
            covered[hit["name"]].append(name)
        if len(hits) > 1:
            print(f"      ⚠ 同一容器命中 {len(hits)} 个产品（复合：一个是应用、一个是底座）")
        print()

    print("=" * 72)
    ratio = (100.0 * total_matched / total_pkgs) if total_pkgs else 0.0
    print(f"包记录 {total_pkgs} 条，命中 {total_matched} 条（{ratio:.1f}%）—— 其余是基础镜像的依赖包")
    print()
    print("产品覆盖（本机可验证 = 有容器跑它的）")
    for key, where in covered.items():
        mark = f"命中 {len(where)} 个容器" if where else "本机无样本"
        print(f"  {key:<32} {mark}")

    if args.all:
        print("\n未命中容器：")
        for name, image, count in unmatched:
            print(f"  {name:<28} [{image}]  包 {count}")
    else:
        print(f"\n未命中容器 {len(unmatched)} 个（--all 看明细）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
