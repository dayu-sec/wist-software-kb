# wist-software-kb

wist 的**软件知识库**——产品、版本、外部标识、漏洞公告。

- **仓 = 创作源**：人写的那部分（产品归一、别名、purl/CPE 映射、区间修正）在这里评审、blame、回滚；
- **包 = 分发单元**：消费方不读本仓目录树，只读 `wist-software-kb-<version>.tar.gz`（带 sha256 与签名）；
- **DB = 检索**：计算区导入本地 Postgres，按「产品 × 版本区间」匹配（文件树做不了这件事）。

> 设计文档：[`wist-design/doc/design/center/models/software-knowledge-repo.md`](https://github.com/dayu-sec/wist-design/blob/main/doc/design/center/models/software-knowledge-repo.md)
> 与 [`software-normalization-and-vuln-enrichment.md`](https://github.com/dayu-sec/wist-design/blob/main/doc/design/center/models/software-normalization-and-vuln-enrichment.md)。
>
> 本仓是**公共知识库**：不含任何客户数据（主机、IP、安装路径、命中结果）。客户私有产品定义走
> 本地 overlay，从不回写。

## 目录

```text
version.txt                     # 版本权威（gx adm v_patch / v_feat 改它，tag 也取它）
kb.toml                         # schema_version / kb_version / 数据源与许可证
products/<product>.toml         # curated：产品固有（权威名 + 类别 + 识别判据）
profiles/<product>/<form>.toml  # curated：部署画像（认出之后怎么管）—— 见 profiles/README.md
advisories/<source>/…           # curated：人工修正、供应商公告
bulk/                           # 上游镜像分片（机器生成，别手改）
scripts/                        # validate.py / package.sh / gen-signing-key.sh / recognize.py
keys/                           # 内容签名公钥（私钥只在 CI secret）
_gal/                           # gx 工作区：gx adm v_patch | v_feat | tag_alpha | tag_beta | tag_stable
```

> 没有 `schemas/` 和 `examples/`（2026-11-07 删）。
> 前者过期且无人使用，后者会随模型变而腐化 —— **闸门 `scripts/validate.py` 是唯一事实来源**，
> 新加产品直接照 `products/nginx.toml` 抄（真实文件有 CI 守着）。

本地入口是 `Makefile`：`make validate` / `make dry-run` / `make pack`（详见下方 CI 一节）。

## 产品固有 vs 部署画像

```
products/  它是谁：canonical_id、功能类别（多值）、kind、默认端口、**识别判据**
profiles/  怎么管：version_probe、bind_scope、upgrade_mode、config_paths、日志/指标面
```

分开的理由：同一个 postgres，systemd 上是 `restart`，k8s 里是滚动替换 Pod；
这些不是产品属性，是环境属性。**判据则相反** —— `/usr/sbin/nginx` 换个装法也成立，所以留在产品层。

> 这条是被实测逼出来的：把 `path`/`unit` 判据放进 profile 后，`nginx/deb`
> 也命中了 Alpine 容器（因为路径本来就不随装法变）。

### 识别判据（`[[recognize]]`）

七类：`dpkg` / `apk` / `rpm` / `path` / `unit` / `container` / `image`，**每条带强度**：

| 强度 | 典型用法 |
| --- | --- |
| `strong` | 精确包名、精确路径 —— 单独命中即可认定 |
| `medium` | 含通配的路径、镜像名（子串）—— 需配合其他证据 |
| `weak` | 容器名 —— 实测不可靠（同一镜像可叫 `metrics` / `vm`） |

同一个观测（如 `dpkg:nginx`）**只能属于一个产品** —— 这是「一个产品裂成两条」的闸门。

### 出处（`provenance`）

每个产品必须老实标：

| 值 | 含义 | 强制要求 |
| --- | --- | --- |
| `measured` | 至少一条判据在真实环境命中过 | `verified_on = ["<env>@YYYY-MM-DD"]` |
| `vendor-doc` | 官方文档写明，未在现场验过 | `source_url` |
| `inferred` | 按惯例推的，未核实 | 无，但 `validate.py` 每次都会列出来 |
| — | （可选）`unverified` 列出本次没覆盖的判据 | 如 `["recognize.unit"]` |

`validate.py` 会在头部打出可信度分母：

```
products    10
profiles    11（measured 10）
            判据级未验证：nginx[dpkg/unit]、openjdk[dpkg/rpm]、…
```

`measured` 只承诺「至少一条判据在真实环境命中过」，**不承诺每条都验过**；
没验到的逐条写在 `unverified` 里，不然字段就变成安慰剂了。

完整字段与纪律见 [`profiles/README.md`](profiles/README.md)；
10 个产品的实测结果见 [`SPIKE.md`](SPIKE.md)。

## 两级

| 层 | 谁写 | 形态 | 覆盖关系 |
| --- | --- | --- | --- |
| **curated** | 人（PR） | 一对象一文件 TOML | **覆盖** bulk |
| **bulk** | CI 定时拉 OSV / NVD / 发行版 tracker | 按源 + 年份/首字母分片 JSONL | 被 curated 覆盖 |

同一个 `osv_id` 两边都有时，curated 胜（人工修正优先，PR 里要写理由）。

## 提交一个新软件

1. 加 `products/<product>.toml`（照 `products/nginx.toml` 抄 —— 真实文件有 CI 守着，不会腐化）；
2. 先定 **`canonical_id` = `<vendor>/<name>`**（小写，如 `f5/nginx`）——**这是产品的权威名**；
3. `product_id` = `sha256:` + `sha256(canonical_id)[:32]`，校验脚本会重算比对，不一致直接失败；
4. purl / CPE **全部是别名**，只进 `external_ids`，**不参与 id 派生**；
5. 需要的话再加 `profiles/<product>/default.toml`（照 `profiles/README.md`）；
6. `python3.12 scripts/validate.py`（或 `make validate`）；
7. PR。

> 为什么权威名不是 purl：purl 是「包在某个生态里的坐标」。同一个产品在不同生态里名字不同
> （Debian 叫 `redis-server`、Alpine 叫 `redis`），而自研/无发行版打包的软件根本没有 purl
> （只能手写 `pkg:generic/…`）。拿 purl 做权威，等于把主键交给「当初在数组里写了哪一条」。

**改 `canonical_id` 会让 `product_id` 变化** —— 下游客户域的安装记录会悬空。
这种变更必须显式声明（PR 标题带 `rename:`，并在描述里写清从旧 id 到新 id 的映射）。
改 `external_ids` / `aliases` 不受影响（它们只是别名）。

## 打包与消费

```sh
./scripts/package.sh --dry-run          # 预览
./scripts/package.sh                    # → dist/wist-software-kb-<ver>.tar.gz + .sha256 (+ .sig)
```

计算区侧：

```sh
wist-atlas kb import --pack /path/wist-software-kb-<ver>.tar.gz [--overlay /etc/wist-kb/local]
```

导入是幂等的：同一包重复导入结果一致；包里的 `kb_version` 会记到命中结果上，
用于回答「上周没事、这周有了，是机器变了还是知识库变了」。

## 许可证与出处

- 本仓内容：Apache-2.0（见 `LICENSE`）；
- bulk 层按源记录许可证（见 `kb.toml` 的 `[sources]`）：**只存 id / 区间 / 链接，不转存全文**；
- 引用外部数据时保留 `source` 与 `references`，这是 CI 闸门的一部分。

## CI

| 工作流 | 触发 | 做什么 |
| --- | --- | --- |
| `ci.yml` | PR / push | `scripts/validate.py`（schema、引用完整性、区间语法、id 稳定性、体量） |
| `release.yml` | tag `v*.*.*` | 校验 + 打包 + 签名 + 上传 GitHub Release |

本地没有 Python 3.11+ 时：`brew install python@3.12`，用 `python3.12 scripts/validate.py`。

## 发版

版本权威是 `version.txt`；三个通道 `alpha → beta → main`（alpha/beta 发成 prerelease，stable 才占 latest）：

```sh
make validate                      # 先过闸门
gx adm v_feat                      # 或 v_patch：改 version.txt（内容新增用 feat，修数据用 patch）
gx adm tag_alpha                   # 或 tag_beta / tag_stable → 推 tag，CI 打包发版
```

签名私钥只在 CI secret `KB_SIGNING_KEY`（PKCS#8 PEM）。没配 secret 时 CI 仍会发包，
但包**没有 `.sig`** —— 配了 `keys/kb-signing.pub.pem` 的消费方会拒收。
