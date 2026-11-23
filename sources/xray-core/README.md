# Xray-core

<!-- markdownlint-disable MD013 -->
<!-- cspell:ignore xray xtls geoip geosite geodata softfloat riscv loong mips mipsle armv openbsd Vless Xtls trimpath buildid GOARM GOMIPS leastload leastlopi devi grpcurl protoc outbounds vprotogen -->

Patched build of [Xray-core](https://github.com/XTLS/Xray-core), distributed in [GitHub Releases](https://github.com/megalomania428/update-graves/releases) as binaries. `build.yaml` pins tag `v24.11.11` with `patch: 1` and `patch_width: 3`: the release version is `24.11.11-p001`, pull requests and manual builds use `24.11.11-p001.dev.<run>.<attempt>`. The binaries are built like the official upstream release: `CGO_ENABLED=0`, `-trimpath`, `-s -w -buildid=`, Go 1.23.3, and Go 1.21.4 for Windows 7. `xray version` shows the upstream version `24.11.11` and then the recipe version in place of the upstream build name.

A release has these assets, where `<version>` is the file-name form `24_11_11-p001`, each archive with one binary named like the archive without the extension, plus `.exe` on Windows:

- `xray-<version>-<platform>.txz` for `linux-amd64`, `linux-386`, `linux-arm64`, `linux-armv7`, `linux-armv6`, `linux-armv5` (`GOARM` 7, 6 and 5), `linux-riscv64`, `linux-loong64`, `linux-mips64`, `linux-mips64le`, `linux-mips`, `linux-mipsle`, `linux-mips-softfloat`, `linux-mipsle-softfloat` (`GOMIPS=softfloat`), `linux-ppc64`, `linux-ppc64le`, `linux-s390x`, `android-arm64`, `darwin-amd64`, `darwin-arm64`, `freebsd-amd64`, `freebsd-386`, `freebsd-arm64`, `freebsd-armv7`, `openbsd-amd64`, `openbsd-386`, `openbsd-arm64` and `openbsd-armv7`; the Linux and FreeBSD binaries are fully static, and the build fails if `file` does not report them as statically linked;
- `xray-<version>-<platform>.7z` for `windows-amd64`, `windows-386`, `windows-arm64`, `windows-armv7`, `windows7-amd64` and `windows7-386` – the same binary with `.exe`, the `windows7-*` ones built by Go 1.21.4 for Windows 7;
- `xray-balancer-scores-<version>.sh` – the [balancer score script](#balancer-scores), a copy of `scripts/balancer-scores.sh`.

Installation on Linux x64:

```bash
T="$(mktemp -d)" && curl -fsSL 'https://github.com/megalomania428/update-graves/releases/download/xray-core-v24.11.11p001/xray-24_11_11-p001-linux-amd64.txz' -o "${T}/xray.txz" \
  && tar -xJf "${T}/xray.txz" -C "${T}" && install -D -m 0755 "${T}/xray-24_11_11-p001-linux-amd64" ~/.local/bin/xray; rc=$?
rm -rf "${T}"; (exit "${rc}")
```

The archives carry no `geoip.dat` and `geosite.dat`. Configurations with `geoip:` or `geosite:` rules need them in the directory of the binary or, if `XRAY_LOCATION_ASSET` is set, in its directory instead of the directory of the binary; outside Windows Xray-core also looks in `/usr/local/share/xray`, `/usr/share/xray` and `/opt/share/xray`, in this order, when the file is not in the first directory.

Before packaging, every build runs all upstream Go tests on Linux x64 with Go 1.23.3 and the geodata pinned in `build.yaml`: `geoip.dat` of `v2fly/geoip` release `202411070052` and `dlc.dat` of `v2fly/domain-list-community` release `20241104071109` as `geosite.dat`. A failed test fails the build and publishes nothing, except the tests that need the Internet – the DNS name server tests of `app/dns` and `TestVlessXtlsVisionReality` – whose failures only add a warning to the build.

## Balancer strategy leastLoPi

The patched binary adds the balancer strategy `leastLoPi`. It ranks the alive outbounds of the balancer selectors by `score = avg × sqrt(costPing) + dev × sqrt(costDevi)`, where `avg` and `dev` are the average RTT and its deviation of the successful probes of `burstObservatory`; with the plain `observatory` `avg` is the last delay and `dev` is 0. The lower score wins; equal scores go to the lower unweighted `avg`, then fewer failed probes, more probes and the tag in ascending order. Outbounds without a status and dead ones take no part.

```json
{
  "type": "leastLoPi",
  "settings": {
    "expected": 1,
    "costs": [
      { "match": "^cg-vl-w$", "regexp": true, "costPing": 1, "costDevi": 1 },
      { "match": "^mh-vl-w$", "regexp": true, "costPing": 16, "costDevi": 1 },
      { "match": "^cb-vl-w$", "regexp": true, "costPing": 99, "costDevi": 99 }
    ]
  }
}
```

- `costs` – rules in order, the first one whose `match` is found in the tag sets both weights: a Go regular expression with `regexp: true`, a substring otherwise. A missing `costPing` or `costDevi` is `1`, an explicit `0` turns its term off; negative weights, a pair of explicit zeros, an empty `match`, an invalid regular expression and unknown keys such as `value` are configuration errors. Unlike `leastLoad`, numbers in the tag never become weights.
- `expected`, `baselines` and `maxRTT` work as in `leastLoad`, with `baselines` compared with the score. Without `baselines` the pool is the best `expected` outbounds, one when `expected` is `1`, `0` or unset. With `baselines` the pool takes the best outbounds with the score strictly under a baseline, trying the baselines in the configured order until the pool has `expected` outbounds or, for `0` or unset, at least one; a positive `expected` then fills the pool up to `expected`, while `0` or unset may leave it empty. An `expected` larger than the number of qualified outbounds takes them all. `maxRTT` strictly limits the unweighted delay compared in whole milliseconds, the fractional part of the threshold is dropped as in `leastLoad`, and `0` turns it off. An outbound of the pool is picked at random, an empty pool goes to `fallbackTag`, and a manual override still has priority.

`RoutingService/GetBalancerInfo` of the patched binary also returns, besides `override` and `principleTarget`, the normalized `strategy` of the balancer, its `costRules` in the configured order (`match`, `regexp`, and `value` for `leastLoad`, where `0` takes the weight from the number in the matched text or else `1`, or the configured `costPing` and `costDevi` for `leastLoPi`) and `outboundCosts` – the effective weights, not their square roots, of every current candidate of the selectors sorted by tag: `cost` for `leastLoad`, `costPing` and `costDevi` for `leastLoPi`. Strategies without weights return neither rules nor weights.

## Balancer scores

`xray-balancer-scores-<version>.sh` prints every outbound of the observatory report with the score of one balancer, computed from the weights the running Xray reports. It needs `grpcurl` and `jq`, reads no configuration files and has no tags or weights of its own: changing the weights in the server configuration is enough. The Xray API must enable `RoutingService`, `ObservatoryService` and `ReflectionService`, for example:

```json
{
  "api": {
    "tag": "api",
    "listen": "127.0.0.1:19644",
    "services": ["RoutingService", "ObservatoryService", "ReflectionService"]
  }
}
```

```bash
bash xray-balancer-scores-24_11_11-p001.sh -s 127.0.0.1:19644 -b vl-w
```

`-s` sets the API address, `127.0.0.1:19644` by default, and `-b` the balancer tag, `vl-w` by default. A line shows the tag, `alive`, `avg`, `dev`, `min` and `max` of the successful probes or `last` for the plain observatory, `RTT=n/a` without successful probes, the `score`, all of them in milliseconds with three decimals (the values are rounded to a microsecond), `fail=<failed>/<all>` probes and the balancer weights of the outbound:

```text
mh-vl-w alive=true avg=150.000ms dev=45.000ms min=101.000ms max=230.000ms score=645.000ms fail=1/10 costPing=16 costDevi=1
cg-vl-w alive=true avg=1500.000ms dev=0.000ms min=1500.000ms max=1500.000ms score=1500.000ms fail=0/10 costPing=1 costDevi=1
cb-vl-w alive=false RTT=n/a score=n/a fail=10/10 costPing=4096 costDevi=4096
```

`score=n/a` marks dead outbounds and outbounds out of the balancer. Alive outbounds go first, the scored ones by score and the order of the strategy for equal scores. The first line is not the routing decision: `maxRTT`, `baselines`, `expected` and an override also take part, and the two requests return snapshots taken at different moments. For a strategy without weights or an Xray API without them the script prints a warning and no scores, and for an observatory report without outbounds a warning and no lines. A `leastLoad` score out of the `int64` range of the server's `time.Duration` (a huge weight) gets one warning that the server overflows it and its order may differ.

## List of patches

- `001-leastlopi-balancer-costs` – the [`leastLoPi` strategy](#balancer-strategy-leastlopi) in `app/router` and `infra/conf`, and the `strategy`, `costRules` and `outboundCosts` fields of `BalancerMsg` read by `GetBalancerInfo`, with tests. `app/router/config.pb.go` and `app/router/command/command.pb.go` are generated as by `infra/vprotogen` of the tag: protoc 28.2 (`v5.28.2`), `protoc-gen-go` v1.35.1 and `protoc-gen-go-grpc` v1.5.1, with `paths=source_relative`.
