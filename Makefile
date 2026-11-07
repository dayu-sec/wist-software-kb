# 本地入口：CI 做同样的事（见 .github/workflows/ci.yml）。
#
# `python3` 在 macOS 常是 3.9（没有 tomllib），所以默认用 python3.12；
# 换解释器：`make validate PYTHON=python3.11`。

PYTHON ?= python3.12

.PHONY: help validate pack dry-run clean signing-key

help:
	@echo "make validate     校验 products / advisories / bulk（schema、引用、id、区间、体量）"
	@echo "make dry-run      预览打包（不写盘）"
	@echo "make pack         打包到 dist/（KB_SIGNING_KEY 给定时顺带签名）"
	@echo "make signing-key  生成签名密钥对（一次性；私钥进 CI secret，公钥进 keys/）"
	@echo "make clean        删掉 dist/"

validate:
	$(PYTHON) scripts/validate.py

dry-run:
	./scripts/package.sh --dry-run

pack:
	./scripts/package.sh

signing-key:
	./scripts/gen-signing-key.sh

clean:
	rm -rf dist
