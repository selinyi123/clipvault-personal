# ClipVault v2 候选版本锁

`contracts/v2_candidate_version.json` 是 v2 daily candidate 的唯一版本锁。

- Android Runtime 与独立 IME 在 Gradle 配置阶段直接读取 `version_name` 和各自
  的 `version_code`。
- Windows v2 installer 的无参数默认 `AppVersion` 必须与 `version_name` 相同；
  `desktop/tests/test_v2_candidate_version.py` 阻止漂移。显式传入 `AppVersion` 仅用于
  构建同一版本锁对应的候选，不得用来绕过版本治理。
- Runtime 的 `versionCode` 必须严格高于已发布 v1.6.0 的 13，避免升级降级冲突。

版本升级必须在一个补丁中更新锁、installer fallback 和对应测试期望；不得分别
修改两个 Android module 或安装器版本。
