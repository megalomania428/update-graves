# Happ with neutralized traps

<!-- markdownlint-disable MD013 -->
<!-- cspell:ignore apktool apksigner zipalign smali proxyutility debuggable Xposed Magisk -->

Rebuilt APK of Happ – Proxy Utility (`su.happ.proxyutility`), an Android client for proxy subscriptions. The original APK is never published: graves downloads the `happ-3_18_1.apk` asset of the `sources-happ` release in the private `megalomania428/update-bunker` repository, checks its SHA256 pinned in `build.yaml`, decodes it with apktool 3.0.3, neutralizes the traps listed below with `scripts/001-neutralize.py`, builds it again, aligns it with `zipalign` and signs it with `apksigner` of the newest Android build-tools on the runner, using the own key, the default signature schemes of `apksigner` and no v4 signature. The result is published only to `megalomania428/update-bunker`: `version: 3.18.1` with `patch: 1` and `patch_width: 3` releases `happ-3_18_1-p001.apk`, while pull requests and manual builds upload `happ-3_18_1-p001_dev_<run>_<attempt>.apk` to the `v999` draft of that repository.

The signature differs from the original one, so Android installs the APK only after the original Happ is removed, and its data are lost. Every anchor and method of the neutralization must occur exactly once in the decoded smali, otherwise the build fails: a new upstream version needs the anchors checked again.

## List of patches

- `tamper-traps` – the startup signature check of `HappApplication` no longer scrambles all secrets of a re-signed APK, the debuggable flag check of `BaseActivity` always sees a non-debuggable build, and the emulator check during a custom config import of `ServerCustomConfigActivity` always reports a real device.
- `remote-blacklist` – an already stored blacklist is ignored, and loading and updating the remote blacklist do nothing.
- `subscription-restrictions` – the subscription restriction check always reports an unrestricted subscription without a status object, and a remote restriction status is never applied.
- `firebase-push` – all server push commands received by the Firebase messaging service are ignored.
- `forced-update` – the release check behaves as if the update servers are unreachable: the menu row shows its brief spinner and returns to idle, and the app is never blocked or updated.
- `root-detection` – the root, Xposed and Magisk detection always reports a clean device.
