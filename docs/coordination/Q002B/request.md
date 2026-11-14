# Q002B：发行物布局边界修补

固定devin/swe-2、effort=max、零工具。只输出JSON代码块：`{"edits":[{"path":"...","old":"唯一原文","new":"替换"}],"notes":[]}`。允许tools/release/check_artifacts.py、tests/test_release_artifacts.py、MANIFEST.in、docs/releasing.md；不改其他实现、不运行或声称测试通过。

主导方通过既有artifact测试fixture实际复现：wheel_extra=unexpected_module.py与sdist_extra=evals/maintenance/private.json均未被拒绝。实际当前构建包没有混入这些内容。现有checker只排除少数目录，未落实文档所述边界。

修补范围：

1. Wheel只允许import package目录及经过现有名称/版本校验的唯一dist-info目录。不硬编码ezviz_unified，复用package/expected元数据参数。保留现有链接、路径穿越、重复条目、.env文件和metadata校验。其他顶层模块、目录、AGENTS/.github/evals/reports都拒绝。
2. sdist允许的根布局以实际构建为基准：LICENSE、MANIFEST.in、PKG-INFO、README.md、pyproject.toml、setup.cfg（可允许明确的setup.py作为常规打包文件）、src、tests。src只允许SDK package和与发行名对应的egg-info；不要允许任意其他src包。tests继续只允许扁平的conftest.py、test_offline_policy.py与test_sdk*.py。目录成员正常接受，不把合法setuptools目录项当未知文件；不使用README*/setup.*这样的宽泛模式。
3. sdist必须实际含tests/conftest.py、tests/test_offline_policy.py及至少一个test_sdk*.py文件。明确这是存在性/布局校验，不是测试执行或秘密内容扫描。不要把--skip-source-tests变成伪造测试通过。
4. MANIFEST.in中SDK测试改成与扁平策略一致的非递归include。源码模块的recursive-include不变。现有源码包SDK测试1845项应仍完整保留。
5. 更新最小有效fixture使其包含上述必需测试文件；补充未授权wheel顶层文件/目录、sdist维护目录/未知根文件/未知src包、各类必需测试缺失的拒绝测试。回归应实际构造并检查归档，不仅断言实现字符串。既有合法归档、元数据/哈希及安装工具fixture使用保持通过。
6. docs/releasing.md简短说明明确布局与必需测试文件、不替代安装/测试/内容级秘密扫描。不要宣称任意私密字符串都可检测。

实际构建布局：wheel顶层为ezviz_unified与ezviz_unified-0.1.0a1.dist-info；sdist顶层为LICENSE/MANIFEST.in/PKG-INFO/README.md/pyproject.toml/setup.cfg/src/tests，src下为ezviz_unified与ezviz_unified.egg-info。下方提供完整checker、相关测试与配置，供精确替换。
