# products：产品固有

一个文件 = **一个产品**（跨环境、跨装法都成立的那部分）。

```text
products/<product>.toml
```

它回答「这是谁、叫什么、干什么、怎么认」。**不回答**「在这个环境里怎么管」—— 那是 `profiles/`。

---

## 文件形状

```toml
product_id      = "sha256:…"        # = hash(canonical_id)，由闸门重算比对
canonical_id    = "f5/nginx"        # **权威名**：人定、小写、定了不许改
canonical_name  = "nginx"
vendor          = "F5"
package_type    = "deb"             # deb|rpm|apk|brew|app|npm|pypi|go|cargo|maven|nuget|gem|generic|oci|unknown
homepage        = "https://…"       # 可选
lifecycle_state = "supported"       # supported|maintenance|eol|unknown

categories      = ["web-server"]    # **多值**：功能类别（闭集，见下）
kind            = "daemon"          # daemon|runtime|agent|library|cli|kernel-module
default_ports   = [80, 443]
requires        = ["openjdk/openjdk"]   # 可选：必须有**外部提供**的运行时

provenance      = "measured"        # measured|vendor-doc|inferred
verified_on     = ["docker-image@2026-11-07"]
unverified      = ["recognize.dpkg"]    # 本次验证没覆盖到的判据

aliases     = ["NGINX"]
external_ids = [                    # 别名：只用于识别，**不参与 id 派生**
  { kind = "purl", value = "pkg:deb/debian/nginx" },
  { kind = "cpe",  value = "cpe:2.3:a:f5:nginx" },
]

# ── 识别判据（每条带强度）────────────────────────────────────────────────
[[recognize]]
kind     = "apk"
values   = ["nginx"]
strength = "strong"

[[recognize]]
kind     = "image"
values   = ["wist-atlas"]
match    = "suffix"          # substring（默认）| suffix —— 只比仓库名
strength = "medium"
```

## `canonical_id` 是权威名，purl 只是别名

purl 是「包在**某个生态**里的坐标」，不是产品身份：

- 同一产品在不同生态名字不同：Debian `redis-server` / Alpine `redis` / 镜像 `redis`；
- 自研或发行版不打包的软件**根本没有 purl**（只能手写 `pkg:generic/…`）。实测：17 个产品里 12 个如此。

所以 purl / CPE 全部降级为别名。改 `canonical_id` = 换主键，必须走显式 rename（PR 标题带 `rename:`）。

## 判据：七种 + 三档强度

| kind | 匹配对象 |
| --- | --- |
| `dpkg` / `apk` / `rpm` | 包管理器里的包名（**只跟自己生态比**） |
| `path` | 绝对路径，支持 `*`（版本目录：`/usr/lib/postgresql/*/bin/postgres`） |
| `unit` | systemd 单元，模板实例写 `postgresql@*.service` |
| `container` | 容器名 —— **慎用**，实测同一镜像可叫 `metrics` / `vm` |
| `image` | 镜像仓库名，`match = substring`（默认）或 `suffix` |

| strength | 含义 |
| --- | --- |
| `strong` | 精确包名 / 精确路径 —— 单独命中即可认定 |
| `medium` | 含通配的路径、镜像名 —— 需配合其他证据 |
| `weak` | 容器名 —— 实测不可靠 |

**同一个观测（如 `dpkg:nginx`）只能属于一个产品**，闸门会在两个产品认领同一观测时报错。

## `requires` 的边界（2026-11-07 收紧）

> `requires` 只表达「**必须有外部提供的运行时**」：JDK、Erlang、.NET runtime。

| 不表达 | 为什么 | 该放哪 |
| --- | --- | --- |
| 需要数据库 / 中间件 | 那是部署拓扑 | atlas 的 `runtime_binding` |
| 可选加速组件 | 没有它也能跑，不算依赖 | 不写 |
| 运行期实际用的那个 JVM | 每台机不同 | 客户域采集 |
| **自带**的运行时（elasticsearch 自带 JDK） | 不是「依赖外部」 | 下方的 `bundles` |

规模上限：产品数 × 平均出度 ≈ 千级边。**不会膨胀** —— 膨胀只会发生在把运行期依赖也塞进来。

## `bundles`：自带运行时（2026-11-07 定）

有些产品**自己带着**一个运行时，而且不通过包管理器安装：

| 产品 | 自带什么 | 路径 | 系统装的判据能发现吗 |
| --- | --- | --- | --- |
| `apache/kafka:3.7.0` | Temurin 21.0.2 | `/opt/java/openjdk/bin/java` | 能 —— 但这正是问题所在 |
| `elasticsearch:8.13.0` | JDK 22 | `/usr/share/elasticsearch/jdk/bin/java` | ❌ 看不见（静默盲区） |

```toml
[[bundles]]
product = "openjdk/openjdk"
probe   = { kind = "binary", command = ["/usr/share/elasticsearch/jdk/bin/java", "-version"], stderr = true }
```

### 版本靠探测，不靠声明

不要写 `version = "22"` —— 外层产品一升级，声明就腐化了。写**探测命令**，让版本每次现场算：

```
elasticsearch:8.13.0 → bundles openjdk v22
apache/kafka:3.7.0   → bundles openjdk v21.0.2
```

### 与 `requires` 的区别

| | 含义 | 漏洞归属 |
| --- | --- | --- |
| `requires` | 外部必须提供（否则跑不起来） | 算在依赖方 |
| `bundles` | 它自己带着 | **算在外层产品上**，标记来源是内置的运行时 |

### 闸门：探测路径不能同时是识别判据

这条正是从实战 bug 里长出来的：`openjdk` 曾经把 `/opt/java/openjdk/bin/java` 当判据，
结果 **kafka 容器被报成「kafka + openjdk」两个产品**。闸门现在会拦：

```
bundles 的探测路径 /usr/lib/jvm/java-21-openjdk/bin/java 同时是 openjdk/openjdk 的
识别判据 '/usr/lib/jvm/*/bin/java' —— 它会被认成独立安装的产品
```

### 仍然开放

自带的运行时命中 CVE 后，**要不要在外层产品的命中里体现**（`via = "bundled:openjdk"`）？
这需要导入器侧决定，KB 这层已经把事实备好了。

## `categories` 闭集

```
database  cache  message-queue  search  web-server  runtime  pipeline  application  unknown
```

**判定规则**：一个值必须绑定一个**不同的动作或查询**。

> 检验：把 `category = X` 换成别的值，系统行为会变吗？不变 → 删掉它。

据此（2026-11-07）：
- 删 `monitoring`（与 `database` 无行为差异）、`security`（与 `vendor` 重复）、`gateway`（与 `web-server` 重合）；
- 加 `application`：业务应用的动作确实不同 —— 版本来自镜像 tag、几乎没有上游公告、升级是滚动替换、只有 stdout 面。

> 推论：**某个值长期停在 `unknown` = 闭集缺一格**，不是数据没填。
> `validate.py` 会把 `categories == ["unknown"]` 的产品单独列成「待归类」。
