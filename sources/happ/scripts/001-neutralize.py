#!/usr/bin/env python3
# Neutralize the smali traps of a decoded Happ 3.18.1 APK.

# cspell:ignore smali proxyutility HappApplication ServerCustomConfigActivity iget lit
# cspell:ignore nez eqz sget Lzu Lgd Lth Lvj Ldp Lsw Ljp Lz Lo Lk Xposed Magisk Landroid
# cspell:ignore Ljava
from __future__ import annotations

import sys
from pathlib import Path

UNIT = (
  '    .locals 1\n\n'
  '    sget-object v0, Lzu4;->a:Lzu4;\n\n'
  '    return-object v0\n'
  '.end method\n'
)
NULL = '    .locals 1\n\n    const/4 v0, 0x0\n\n    return-object v0\n.end method\n'
FALSE = '    .locals 1\n\n    const/4 v0, 0x0\n\n    return v0\n.end method\n'


def say(message: str) -> None:
  sys.stdout.write(message + '\n')
  sys.stdout.flush()


def patch_text(path: Path, old: str, new: str) -> None:
  text = path.read_text(encoding='utf-8')
  if text.count(old) != 1:
    message = f'Anchor not unique or missing in {path}'
    raise SystemExit(message)
  path.write_text(text.replace(old, new), encoding='utf-8')


def replace_method(path: Path, signature: str, new_block: str) -> None:
  text = path.read_text(encoding='utf-8')
  needle = '\n' + signature + '\n'
  if text.count(needle) != 1:
    message = f'Method not unique or missing: {signature} in {path}'
    raise SystemExit(message)
  start = text.index(needle) + 1
  end_key = '\n.end method\n'
  end = text.index(end_key, start) + len(end_key)
  path.write_text(text[:start] + new_block + text[end:], encoding='utf-8')


def text_patches(smali: Path, happ: Path) -> list[tuple[str, Path, str, str]]:
  flags = '    iget p1, p1, Landroid/content/pm/ApplicationInfo;->flags:I\n\n'
  lines = '    .line 166\n    .line 167\n    .line 168\n'
  return [
    (
      'tamper trap: startup signature mismatch scrambles all secrets',
      happ / 'HappApplication.smali',
      '    if-nez v0, :cond_0\n',
      '    goto :cond_0\n',
    ),
    (
      'tamper trap: debuggable build scrambles all secrets',
      happ / 'ui' / 'BaseActivity.smali',
      flags + '    .line 280\n    .line 281\n    and-int/lit8 p1, p1, 0x2\n',
      flags + '    .line 280\n    .line 281\n    and-int/lit8 p1, p1, 0x0\n',
    ),
    (
      'tamper trap: emulator during custom config import scrambles secrets',
      happ / 'feature' / 'custom_config' / 'ServerCustomConfigActivity.smali',
      '    invoke-static {}, Lk10;->F()Z\n\n' + lines + '    move-result p1\n',
      '    const/4 p1, 0x0\n\n' + lines + '    nop\n',
    ),
    (
      'remote blacklist: ignore any already stored blacklist',
      smali / 'nz.smali',
      '    if-eqz v0, :cond_18\n',
      '    goto :cond_18\n',
    ),
  ]


def method_patches(smali: Path, happ: Path) -> list[tuple[str, Path, str, str]]:
  dto = happ / 'dto' / 'CheckSubscriptionRestrictedResult.smali'
  restricted = 'Lsu/happ/proxyutility/dto/CheckSubscriptionRestrictedResult;'
  return [
    (
      'remote blacklist: disable load',
      smali / 'gd4.smali',
      '.method public static final a(Lgd4;Lo90;)Ljava/lang/Object;',
      UNIT,
    ),
    (
      'remote blacklist: disable update',
      smali / 'gd4.smali',
      '.method public final d(Lth4;)Ljava/lang/Object;',
      UNIT,
    ),
    (
      'subscription restriction: always report not restricted',
      smali / 'sa.smali',
      (
        '.method public final g(Lz9;Lsu/happ/proxyutility/dto/SubscriptionItem;Lo90;)'
        'Ljava/lang/Object;'
      ),
      (
        '    .locals 3\n\n'
        f'    new-instance v0, {restricted}\n\n'
        '    const/4 v1, 0x0\n\n'
        '    const/4 v2, 0x0\n\n'
        f'    invoke-direct {{v0, v1, v2}}, {restricted}'
        '-><init>(Ljava/lang/String;I)V\n\n'
        '    return-object v0\n'
        '.end method\n'
      ),
    ),
    (
      'subscription restriction: no restriction status object',
      dto,
      '.method public final b()Ljp3;',
      NULL,
    ),
    (
      'subscription restriction: never restricted',
      dto,
      '.method public final c()Z',
      FALSE,
    ),
    (
      'subscription restriction: drop applying the remote status',
      smali / 'ai2.smali',
      '.method public final r(Ljava/lang/Object;)Ljava/lang/Object;',
      UNIT,
    ),
    (
      'Firebase remote control: ignore all server push commands',
      happ / 'firebase' / 'FirebaseMessagingService.smali',
      '.method public final d(Lvj3;)V',
      '    .locals 0\n\n    return-void\n.end method\n',
    ),
    (
      # Deliver null without network: the menu row keeps the brief spinner-then-idle
      # feedback and the app is never blocked or updated.
      'forced update: behave as if the update servers are unreachable',
      smali / 'sw4.smali',
      (
        '.method public static final b(Lsw4;Ljava/lang/String;Ldp1;Lo90;)'
        'Ljava/lang/Object;'
      ),
      (
        '    .locals 1\n\n'
        '    const/4 v0, 0x0\n\n'
        '    invoke-virtual {p2, v0}, Ldp1;->n(Ljava/lang/Object;)Ljava/lang/Object;\n\n'
        '    sget-object v0, Lzu4;->a:Lzu4;\n\n'
        '    return-object v0\n'
        '.end method\n'
      ),
    ),
    (
      'root, Xposed and Magisk detection: always report a clean device',
      smali / 'xa3.smali',
      '.method public p()Z',
      FALSE,
    ),
  ]


def main() -> None:
  args = sys.argv[1:]
  if len(args) != 1:
    message = 'usage: 001-neutralize.py <decoded APK directory>'
    raise SystemExit(message)
  smali = Path(args[0]) / 'smali'
  happ = smali / 'su' / 'happ' / 'proxyutility'
  texts = text_patches(smali, happ)
  methods = method_patches(smali, happ)
  total = len(texts) + len(methods)
  step = 0
  for title, path, old, new in texts:
    step += 1
    say(f'[{step}/{total}] {title}: anchor in {path.relative_to(smali)}')
    patch_text(path, old, new)
    say(f'[{step}/{total}] OK')
  for title, path, signature, body in methods:
    step += 1
    say(f'[{step}/{total}] {title}: {signature} in {path.relative_to(smali)}')
    replace_method(path, signature, signature + '\n' + body)
    say(f'[{step}/{total}] OK')
  say('Neutralized tamper, blacklist, subs, push, update-check and root')


if __name__ == '__main__':
  main()
