# 号码工作台

*haoma-workbench*

一个 Windows 桌面（GUI）与命令行（CLI）工具，用于手机号码文件的规范化、质量校验、分类与库匹配。GUI 与 CLI 共用一个入口 `customer_number_processor_single.py`，可打包为单文件 exe。

## 项目说明

- **号码规范化**：解析、去重、统一格式，支持 e164 等导出格式。
- **质量校验**：按「结构错误（长度/国家码）→ 重复 → NPA/NXX 规则错误 → 有效」优先级分类；默认统计模式，每类最多保留 5 条样例。
- **库匹配**：自动识别有效号码库、已开户号码库、无效号码库，并按库对号码分类。
- **输出事务与恢复**：可恢复的固定路径事务（临时文件 + pending 清单 + 旧文件备份 + 原子替换 + committed 清单），支持 `recover` 手动恢复。
- **编码**：自动检测 BOM，其次分块完整验证 UTF-8，再次验证 GB18030；也可手动指定。

## 依赖与安装

Python 3.10+，Windows。

```powershell
python -m pip install -r requirements.txt
```

核心运行时依赖（会打进 exe）：`numpy`、`PyQt6`。
开发/打包/文档依赖（不打进 exe）：`pytest`、`pytest-qt`、`black`、`isort`、`mypy`、`pyinstaller`、`sphinx`、`sphinx-rtd-theme`。

## 源码运行

无参数启动 GUI：

```powershell
python customer_number_processor_single.py
```

命令行用法：

```powershell
python customer_number_processor_single.py normalize input.txt output.txt
python customer_number_processor_single.py console normalize input.txt output.txt
python customer_number_processor_single.py validate input.txt
python customer_number_processor_single.py export input.txt output.txt --format=e164
python customer_number_processor_single.py normalize input.txt output.txt --encoding gb18030
python customer_number_processor_single.py recover D:\outputs
```

退出码：0 成功，2 参数/输入错误，1 未处理执行错误或构建失败。

## 打包命令

```powershell
python customer_number_processor_single.py build
```

或直接使用 spec：

```powershell
pyinstaller 号码工作台.spec
```

构建使用当前解释器和脚本绝对路径，产物在执行构建时的当前目录 `dist` 下。GUI 和 CLI 共用一个带控制台的单文件 exe；双击 GUI 时会伴随控制台窗口。

## 目录结构

```
.
├── customer_number_processor_single.py   # 主程序（GUI + CLI）
├── library_matcher.py                    # 库文件匹配器（无 Qt 依赖，可单独测试）
├── 号码工作台.spec                        # PyInstaller 打包配置
├── requirements.txt                      # 依赖清单
├── benchmark_check.py                    # 百万行单一号码基准脚本
├── README.md
├── LICENSE                               # GPLv3
├── .gitignore
├── docs/
│   └── delivery-readme.md                # 交付目录内 readme.md 的源文件
├── tests/                                # pytest 用例
│   ├── test_library_matcher.py
│   ├── test_library_ui.py
│   └── fixtures/
│       └── protected_core_baseline.py    # 保护核心回归基线快照
└── tools/                                # 校验与打包辅助脚本
    ├── check_library_exe.py
    ├── check_library_ui.py
    └── package_library_delivery.py
```

## 库文件说明

三个逻辑库：**有效号码**、**已开户号码**、**无效号码**。每个库是一个纯文本 txt 文件，**每行一个号码**。

将 `invalid_customers.txt`、`opened_customers.txt`、`valid_customers.txt` 与 exe 放在**同一目录**。程序启动自动识别；识别不到时，请点击对应行的“浏览”手动指定。

- **只识别 TXT 文件**（扩展名不区分大小写）。
- **精确名优先**：`valid_customers.txt`、`opened_customers.txt`、`invalid_customers.txt`。
- **非精确名按关键词识别**：含 `valid` 或 `有效` 认有效，含 `opened` 或 `已开户` 认已开户，含 `invalid` 或 `无效` 认无效。英文词的字母或数字不能粘连，分隔符可用空格、下划线、短横线等；中文可使用“有效号码库.txt”等名称。`invalid_customers.txt` 不会误认成 valid，`validation.txt`、JSON、日志等不会命中。

启动只扫描程序所在目录，选择源文件后补扫源文件所在目录；只填空着且未锁定的行。点击“重新扫描”同时扫描这两个目录，更新未锁定行；已消失且无候选的自动路径会清空。相同目录仅扫描一次，不进入任何子目录。

用户浏览选定或手动修改的行会**锁定**，重启后仍保留手动路径；手动清空也保持锁定。重新扫描不会覆盖手动行，需要变更时再次手动选择。三态文字为“自动发现”“未找到请手动选择”“已手动指定”，后台统计不会覆盖这些文字。

多候选优先级：程序目录优先于源文件目录，其次同目录内精确标准名优先于关键词，再次修改时间较新的文件，时间相同时按完整路径排序；选择规则及候选写入日志。不存在、无权限或无法读取的目录/候选会记录原因，可读候选继续参与匹配；文件占用导致无法读取时跳过，解除占用后可重新扫描。

配置位于 `%LOCALAPPDATA%\NumberWorkbench\settings.ini`，日志位于 `%LOCALAPPDATA%\NumberWorkbench\logs`；不写入交付目录。首次使用文件配置时迁移已有主题和窗口设置。扫描只查看目录直接文件和候选的首字节，不解析或加载号码库，当前适用于本地目录。

## 仓库不含库文件说明

出于**数据隐私**考虑，本仓库**不包含任何号码库文件**（`*_customers.txt` 已在 `.gitignore` 中排除）。使用者需自备库文件，或按下述格式自行创建。

格式：纯文本 txt，UTF-8 或 GB18030 编码，**每行一个号码**。

```text
13800000000
13800000001
13900000002
```

> 以上号码仅为格式示例，非真实数据。

## 输出事务与恢复

采用方案 A，保留固定文件名。输出必须在同一目录，目录级 Windows 文件锁防止同时提交或恢复。先写临时文件并 flush/fsync，再写 pending 事务清单，创建旧文件备份，逐个原子替换目标，最后原子更新 committed 清单。备份采用复制后原子发布，原目标在替换前继续可读。它需要额外磁盘空间和旧文件大小对应的 O(S) 备份 I/O。

进程内失败会尝试恢复旧文件；恢复失败明确报错，保留 JSON 清单及备份供再次恢复。恢复可重复执行。committed 清单只清理备份，不撤销成功结果。不要手动删除遗留 `.number-transaction-*.json` 或其引用的 `.bak` 文件。

GUI 启动在后台扫描程序目录及当前工作目录；其他输出目录在下次写入前恢复，也可运行 `recover <目录>` 手动恢复。扫描依据事务清单，不根据孤立 `.bak` 猜测目标。

限制：这是可恢复的固定路径事务，不是整组输出同时原子可见。消费者可能在提交过程中观察到新旧混合；断电、介质损坏和目录元数据持久化仍不保证。需要整组崩溃一致性时应迁移到版本目录加单一版本指针（方案 B）。回滚也失败时需先解除文件占用或权限问题，再执行恢复。

## 校验与内存

优先级：结构错误（长度/国家码）→重复→NPA/NXX 规则错误→有效。结构合法的号码先登记规范化键，后续相同号码即使规则非法也归重复。10 位与带 1 前缀的 11 位使用同一去重键。

质量分析默认统计模式，每类只保留最多 5 条样例；精确去重集合仍为 O(U)，U 为唯一号码数。调用 `check_file_quality(path, statistics_only=False)` 可取得全量分类结果，空间 O(N+U)。源解析仍保留全量 int64 数组 O(N)，分块拷贝拼接峰值约 16N 字节，不含分类集合及其他开销。匹配索引排序 O(M log M)，匹配 O(N log M)。

GUI 两个页面均可选择编码，CLI 可使用 `--encoding`，环境变量 `NUMBER_WORKBENCH_ENCODING` 也可指定。自动检测 BOM 优先，然后分块完整验证 UTF-8，再验证 GB18030。完整验证发生在输出创建前，编码候选失败不会污染计数或临时结果；代价是增加 O(文件字节数) 的读取。不支持处理过程中被其他程序修改的输入文件。

取消在分块边界检查。NumPy 索引生成和排序等单次调用无法在内部打断，界面明确显示等待当前操作完成；没有承诺固定毫秒级响应上限。取消在提交点之前保留旧输出，提交开始后完成提交或回滚。

## 验证范围

`tests` 覆盖第二次替换失败、回滚失败及恢复、遗留 pending 清单恢复、CLI 双入口和失败状态、校验分类及有限样例、编码、取消保留旧输出、外部多轮归并、窗口初始化和主题切换，以及保护核心回归（对 `tests/fixtures/protected_core_baseline.py` 做 AST 对比，确保解析、过滤、事务、CLI 与既有信号连接未被改动）。

```powershell
python -m pytest -q
```

`benchmark_check.py` 可复现百万行单一号码基准。这是当前机器的测量，不代表高唯一度数据的内存上限，也不构成修改前后性能对比。尚需真实用户完成文件对话框、拖拽、长任务取消和关窗等完整人工交互验收。

## 许可证

本项目采用 **GNU General Public License v3.0**（GPLv3）。完整条款见 [LICENSE](LICENSE)。

之所以采用 GPLv3 而非宽松许可证，是因为运行时依赖 PyQt6（Riverbank Computing）以 `GPL-3.0-only` 或商业授权双轨发布；在 GPL 版 PyQt6 之上分发本程序，整体必须遵循 GPLv3。若要改为 MIT 等宽松许可证，必须先移除 PyQt6。
