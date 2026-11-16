# SP002：受控worktree工具路径探针

固定devin/swe-2 effort=max，基线81b86b4。仅允许Read当前worktree的AGENTS.md和docs/plans/2026-11-17-sdk-product-completion.md，然后Write唯一文件docs/coordination/SP002/probe.md，内容必须恰为“# Worker probe\n\nControlled worktree read/write verified.\n”。无Bash/网络/凭据/SDK执行，不读工作区外内容，不更改信任或配置。若权限拒绝，停止并报告真实拒绝，不尝试绕过。只报告实际文件与工具结果，不宣称测试通过。
