# 发行准备与验证

当前版本为未发布的 `0.1.0a1`，发行名 `ezviz-unified`，导入名 `ezviz_unified`，要求 Python 3.12+，与旧 `ezviz-openapi-utils` 独立。仓库为 `sunbos/ezviz-unified`；独立仓库已公开并通过交接CI，旧库已归档，PyPI尚未发布。每次发行仍须核对拟发布提交的实际checks。发布前先阅读[官方契约覆盖](../contracts/coverage.json)及[真实验证记录](validation/live/README.md)。源代码与官方资料来源见[来源记录](provenance.md)。

## 构建与检查

在仓库根目录使用已安装开发依赖的Python：

```sh
python -m pytest -q
python -m mypy src/ezviz_unified
python -m harness.audit --repo . --check-summary
python -m harness.contract_eval --repo . --require-all
python -m build
python tools/release/check_artifacts.py
python -m twine check dist/*
```

确保dist中只有本次版本的一份wheel和一份sdist。检查器核对名称、版本、Python要求、MIT许可证、Markdown说明、类型标记，并按明确布局边界核对归档：wheel顶层只允许SDK导入包目录和唯一且经名称/版本校验的dist-info目录；sdist根项只允许LICENSE、MANIFEST.in、PKG-INFO、README.md、pyproject.toml、setup.cfg（及常规setup.py）、src、tests，其中src下只含SDK包与对应egg-info，tests下只含扁平的conftest.py、test_offline_policy.py与test_sdk*.py，且sdist必须实际包含这三类测试文件。同时拒绝路径穿越、链接成员、重复条目及上述边界外的任何条目。该步骤只校验元数据与布局存在性，不执行测试、不替代安装或功能验证，也不做内容级秘密扫描。

wheel仅含SDK；sdist还含SDK测试。完整文档采集、维护harness和相关测试保留在Git仓库，不进入发行文件。开发这些工具时请使用Git检出目录。

## 独立安装验证

仓库内可一次执行完整检查，替代手工创建环境：

```sh
python tools/release/verify_installations.py --installer pip
# 已具备uv缓存时，明确使用离线模式：
python tools/release/verify_installations.py --installer uv --offline --output reports/install.json
```

工具验证并固定发行文件快照，分别安装wheel/sdist、运行mock smoke，并默认运行sdist中的SDK测试。--skip-source-tests可显式省略最后一项，报告不会将省略内容标记为通过。子进程不继承常见模型/EZVIZ凭据和安装目标覆盖设置，正常网络配置可通过环境传入；这不是运行不可信代码的安全沙箱。

分别创建干净环境安装wheel与sdist，再运行：

```sh
/path/to/clean-env/bin/python -I tools/release/smoke_installed.py
```

脚本检查SDK从安装环境加载，用MockTransport完成同步/异步设备和录像查询；不读取账号或操作真实设备。CI在Python 3.12–3.15运行仓库测试、类型/格式检查、构建、元数据检查及两种发行文件的独立安装。以实际CI运行结果为准，配置存在不代表矩阵已通过。

## 发布条件

- 冻结拟发布版本和提交，所有检查针对该版本通过。
- 确认官方契约支持范围、来源缺项与离线评测完整性；不以旧方法映射或兼容性作为发布条件。
- 独立仓库交接与旧库归档已完成；发布时仍核验当前提交的代码、文档、CI和包元数据链接。
- 发布说明分别列出离线验证、真实验证和缺项；空设备列表不能证明设备接口可用。
- 检查公开文档链接、包名归属及发布权限。当前没有自动发布workflow，也没有上传本地生成的文件。

构建产物的hash用于标识该次验证的文件；重新构建后应重新核验，不把旧hash对应的结果套到新文件上。
