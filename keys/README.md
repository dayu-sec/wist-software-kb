# 内容签名公钥

`kb-signing.pub.pem` 是**知识库内容包**的验签公钥（`scripts/gen-signing-key.sh` 生成，Ed25519）。

- 私钥只在 CI secret（`KB_SIGNING_KEY`）里，**永不进仓**（`.gitignore` 挡 `keys/*.pem`，只放行 `*.pub.pem`）；
- 计算区（`wist-atlas kb import`）用这里的公钥验 `*.tar.gz.sig`；
- 签名对象是**包摘要的十六进制文本**（`.sha256` 里那串字节，不带换行），不是整个 tar.gz ——
  所以验签不需要先解压，也方便手工复核：

```sh
sha256sum -c wist-software-kb-<ver>.tar.gz.sha256          # 先核摘要
cut -d' ' -f1 wist-software-kb-<ver>.tar.gz.sha256 \
  | tr -d '\n' > /tmp/msg
base64 -d wist-software-kb-<ver>.tar.gz.sig > /tmp/sig          # macOS：base64 -d -i … -o …
openssl pkeyutl -verify -rawin -pubin -inkey kb-signing.pub.pem \
  -in /tmp/msg -sigfile /tmp/sig                                # → Signature Verified Successfully
```

## 密钥轮换

换钥匙 = 一次**显式**的轮换，不是「重新生成一下」：

1. 新密钥对生成后，**公钥以新文件名并存**（如 `kb-signing-2027.pub.pem`），旧公钥保留；
2. 计算区同时信任两把公钥，直到所有计算区都升级；
3. 确认无旧包依赖后，才删旧公钥。

直接覆盖同名公钥会让「已发布但还没导入的包」全部验签失败。
