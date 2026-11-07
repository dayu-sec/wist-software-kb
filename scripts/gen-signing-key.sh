#!/usr/bin/env bash
# 一次性生成知识库**内容包**的签名密钥对（Ed25519）—— 一把，不做多密钥共存。
#
#   ./scripts/gen-signing-key.sh [--out-dir keys]
#
# 产物：
#   keys/kb-signing.pem        私钥（**不要提交**；正式流程里它只在 CI secret `KB_SIGNING_KEY`）
#   keys/kb-signing.pub.pem    公钥（提交进仓，消费方用它验 `.sig`）
#
# 与安装脚本那把签名方向相反，所以是**另一把**钥匙：
#   安装脚本：网关签、目标主机验；本仓：CI 签、计算区（wist-atlas）验。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ROOT}/keys"
GENERATE_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir) OUT="${2:-}"; shift 2 ;;
    -h | --help) sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

mkdir -p "${OUT}"
KEY="${OUT}/kb-signing.pem"
if [[ -f "${KEY}" ]]; then
  echo "已存在：${KEY}（不覆盖；要换钥匙请先手工移走）" >&2
  exit 1
fi

openssl genpkey -algorithm ed25519 -out "${KEY}" >/dev/null
openssl pkey -in "${KEY}" -pubout -out "${OUT}/kb-signing.pub.pem" >/dev/null
chmod 600 "${KEY}"

echo "已生成："
echo "  私钥  ${KEY}   （→ 放进 CI secret KB_SIGNING_KEY，切勿提交）"
echo "  公钥  ${OUT}/kb-signing.pub.pem（提交进仓）"
