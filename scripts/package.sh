#!/usr/bin/env bash
# 把知识库打包成可分发的制品：`wist-software-kb-<version>.tar.gz`
#
# 为什么打成包：消费方（wist-atlas）按**包**导入本地库，不读本仓目录树 ——
# 与 `wist-knowledge` 同一套纪律（包名与顶层目录都带版本，可离线投放、可回滚）。
#
# 用法：
#   ./scripts/package.sh                  # 版本取自 version.txt
#   ./scripts/package.sh --version 0.1.1
#   ./scripts/package.sh --out dist
#   ./scripts/package.sh --dry-run
#
# 产物：
#   <out>/wist-software-kb-<version>.tar.gz           顶层一层同名目录
#   <out>/wist-software-kb-<version>.tar.gz.sha256
#   <out>/wist-software-kb-<version>.tar.gz.sig       仅在 KB_SIGNING_KEY 给定时产出
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ROOT}/dist"
VERSION=""
DRY_RUN=0

# 进包的内容 = **白名单**，不是 `*`（仓里以后多出别的东西不该被顺手打进制品）。
CONTENT=(kb.toml products profiles advisories)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h | --help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1（--help 看用法）" >&2; exit 2 ;;
  esac
done

FILE_VERSION=""
if [[ -f "${ROOT}/version.txt" ]]; then
  FILE_VERSION="$(tr -d '[:space:]' <"${ROOT}/version.txt")"
fi
[[ -n "${VERSION}" ]] || VERSION="${FILE_VERSION}"
[[ -n "${VERSION}" ]] || { echo "版本为空：version.txt 缺失或为空，用 --version 指定" >&2; exit 2; }

CORE_VERSION="${VERSION%%-*}"
CHANNEL=""
if [[ "${VERSION}" != "${CORE_VERSION}" ]]; then
  CHANNEL="${VERSION#*-}"
  case "${CHANNEL}" in
    alpha | beta) ;;
    *) echo "通道后缀只认 -alpha / -beta（收到：${VERSION}）" >&2; exit 2 ;;
  esac
fi
if [[ -n "${FILE_VERSION}" && "${CORE_VERSION}" != "${FILE_VERSION}" ]]; then
  echo "版本不一致：参数核心版本 ${CORE_VERSION}，version.txt 是 ${FILE_VERSION}" >&2
  exit 2
fi

for name in "${CONTENT[@]}"; do
  [[ -e "${ROOT}/${name}" ]] || { echo "缺内容：${ROOT}/${name}" >&2; exit 2; }
done

PACKAGE="wist-software-kb-${VERSION}"
TARBALL="${OUT}/${PACKAGE}.tar.gz"

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo "[dry-run] 不会写盘。会做的事："
  echo "  版本      ${VERSION}（核心 ${CORE_VERSION} 与 version.txt 一致${CHANNEL:+，通道 ${CHANNEL}}）"
  echo "  顶层目录  ${PACKAGE}/"
  echo "  内容      ${CONTENT[*]} + manifest.json"
  echo "  产物      ${TARBALL}"
  echo "            ${TARBALL}.sha256"
  if [[ -n "${KB_SIGNING_KEY:-}" ]]; then
    echo "            ${TARBALL}.sig（会用 ${KB_SIGNING_KEY} 签）"
  else
    echo "  签名      无（KB_SIGNING_KEY 未设置）"
  fi
  exit 0
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT
DEST="${STAGE}/${PACKAGE}"
mkdir -p "${DEST}"
for name in "${CONTENT[@]}"; do
  cp -R "${ROOT}/${name}" "${DEST}/${name}"
done

# 工作区垃圾不进包：macOS 的 `.DS_Store`、git 占位 `.gitkeep`、Python 缓存。
# 没有这步，`cp -R` 会把它们原样带进制品（包会随“谁在哪台机器上打的”而变化）。
find "${DEST}" \( -name .DS_Store -o -name .gitkeep -o -name __pycache__ -o -name '*.pyc' \) \
  -prune -exec rm -rf {} +

# manifest.json：让包自描述（导入侧不解包也知道版本与内容摘要）。
python3 - "${ROOT}" "${DEST}" "${VERSION}" <<'PY'
import hashlib, json, os, pathlib, subprocess, sys
from datetime import datetime, timezone

root, dest, version = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]


def count(dirname, pattern):
    base = dest / dirname
    return len(list(base.rglob(pattern))) if base.exists() else 0


try:
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
except Exception:
    commit = ""

# created_at 必须**可复现**：同一 commit 重建的包要逐字节一致（否则 sha256 变、签名变，
# `kb_state.pack_sha256` 就失去“是不是同一份”的意义）。
#   优先 SOURCE_DATE_EPOCH（可复现构建惯例），否则取 HEAD 的提交时间，都没有才用当前时间。
def build_time() -> str:
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if epoch.isdigit():
        return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if commit:
        try:
            raw = subprocess.run(
                ["git", "-C", str(root), "log", "-1", "--format=%cI"],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            iso = datetime.fromisoformat(raw)
            return iso.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

files = {}
for path in sorted(dest.rglob("*")):
    if path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files[str(path.relative_to(dest))] = digest

# kb_version = 包版本 + 内容摘要（内容变了就必须换版本，否则下游无法判断"是否同一份"）。
content_hash = hashlib.sha256("".join(f"{k}:{v}\n" for k, v in files.items()).encode()).hexdigest()

manifest = {
    "name": "wist-software-kb",
    "version": version,
    "kb_version": f"{version}+sha256:{content_hash[:32]}",
    "created_at": build_time(),
    "commit": commit,
    "counts": {
        "products": count("products", "*.toml"),
        "profiles": count("profiles", "*.toml"),
        "advisories": count("advisories", "*.toml"),
        "bulk_files": count("bulk", "*.jsonl"),
    },
    "files": files,
}
(dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"  manifest  kb_version={manifest['kb_version']} counts={manifest['counts']} commit={commit or '-'}")
PY

mkdir -p "${OUT}"
rm -f "${TARBALL}" "${TARBALL}.sha256" "${TARBALL}.sig"

# 可复现构建：mtime 全部钉死 + 条目按路径排序 + gzip 不写时间戳。
# 否则 tar/gzip 会写入“什么时候拷贝的”、目录遍历顺序和当前时间 ——
# 同一份内容两次打包 sha256 就不同，签名也跟着变。
find "${STAGE}" -exec touch -h -t 198001010000 {} +
( cd "${STAGE}" && find "${PACKAGE}" | LC_ALL=C sort | tar -cf - -T - --no-recursion | gzip -n ) >"${TARBALL}"

SHA="$(sha256_of "${TARBALL}")"
printf '%s  %s\n' "${SHA}" "$(basename "${TARBALL}")" >"${TARBALL}.sha256"

# 签名：Ed25519 一把密钥，签的是**摘要的十六进制文本**（与 .sha256 里那串同一段字节），
# 运维用 openssl 能手工重验。私钥只放 CI secret（`KB_SIGNING_KEY`），公钥放 keys/。
if [[ -n "${KB_SIGNING_KEY:-}" ]]; then
  msg="$(mktemp)"
  sig="$(mktemp)"
  printf '%s' "${SHA}" >"${msg}"
  if ! openssl pkeyutl -sign -rawin -inkey "${KB_SIGNING_KEY}" -in "${msg}" -out "${sig}" >/dev/null 2>&1; then
    rm -f "${msg}" "${sig}"
    echo "签名失败：需要 OpenSSL ≥ 3.0 做 Ed25519 原始签名。" >&2
    echo "  正式发布走 CI（用 secret 签）；本地要签请装真 openssl。" >&2
    exit 1
  fi
  base64 <"${sig}" | tr -d '\n' >"${TARBALL}.sig"
  printf '\n' >>"${TARBALL}.sig"
  rm -f "${msg}" "${sig}"
  echo "  签名      $(basename "${TARBALL}").sig"
else
  echo "  签名      无（KB_SIGNING_KEY 未设置）→ 配了公钥的消费方会拒收"
fi

echo "已打包 → ${TARBALL}"
echo "  顶层目录  ${PACKAGE}/"
echo "  条目数    $(tar -tzf "${TARBALL}" | wc -l | tr -d ' ')"
echo "  摘要      $(cat "${TARBALL}.sha256")"
