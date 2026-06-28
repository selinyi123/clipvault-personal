<!-- 见 docs/AGENT_WORKFLOWS.md。一个 PR 只装一个关切。 -->

## 目标（唯一）

<!-- 本 PR 解决的单一问题；它服务北极星/核心目标吗？ -->

## 改动范围

<!-- 改了哪些文件/模块，为什么是最小集 -->

## 可验证性

- [ ] 🟢 本地可验（桌面/contracts/host-JVM）
- [ ] 🟡 CI 可验（需 Android SDK / windows-latest）
- [ ] 🔵 设备/人工（交 Owner 验证）

## 测试证据

<!-- 贴命令 + 原始结果，不接受"口头解释通过" -->

```
python -m pytest -q --ignore=tests/test_watcher.py --ignore=tests/test_instance_lock.py
# → N passed
```

## 不变量确认

- [ ] 密钥不进 Obsidian / GitHub / 同步 / FTS / memory（G1）
- [ ] IME 无网络、不记录普通键入（G2）
- [ ] 未修改非目标文件 / 未破坏架构边界
- [ ] 未引入新运行时依赖（stdlib-only），或已附 ADR
- [ ] 改了 schema/迁移 → 已验证升级路径，或已附 ADR
- [ ] 改了两端共享逻辑 → contracts/vectors 已同步

## 风险与回滚

<!-- 风险点；如何回退 -->

## 对外动作（需 Owner 显式批准）

- [ ] 本 PR **不含**对外/难回退动作
- [ ] 含 bump 版本号 / 切 Release / 推用户产物 → **已获 Owner 批准**

## 文档

- [ ] 已更新 HANDOFF / 相关 docs（"不在 repo docs = 没发生"）
