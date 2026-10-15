# 号码工作台

双击“号码工作台.exe”打开界面。此版本同时支持 GUI 和 CLI，GUI 启动会伴随控制台窗口。

## 库文件放置约定

本目录应包含以下五个文件，解压后请保持同级：

- 号码工作台.exe
- invalid_customers.txt
- opened_customers.txt
- valid_customers.txt
- readme.md

程序启动自动识别三个 TXT 库文件（有效号码、已开户号码、无效号码各一行）；识别不到时，点击对应行“浏览”手动选择。

程序只扫 exe 所在目录及已选择源文件的所在目录，不进入子目录。启动及选择源文件时仅补填空行；“重新扫描”更新所有未锁定行。手动选过或改过的行会锁定，重启后仍生效，重新扫描不会覆盖；手动清空也保持锁定，需要更换时再次手动选择。

状态文字分别为“自动发现”“未找到请手动选择”“已手动指定”。候选优先级：程序目录优先于源文件目录；同目录内精确名优先于关键词；同级按修改时间较新者优先，同时间按路径排序。冲突规则、零匹配、缺失目录、权限错误和不可读取文件均有日志；解除文件占用后可重新扫描。

只接受 TXT（扩展名不区分大小写）。关键词按 invalid/无效、opened/已开户、valid/有效顺序整词识别，下划线和短横线可分词；validation、JSON 和日志不会被误认。

配置：`%LOCALAPPDATA%\NumberWorkbench\settings.ini`

日志：`%LOCALAPPDATA%\NumberWorkbench\logs\workbench.log`

配置与日志均位于当前用户 AppData，不落本交付目录。

命令行示例：

```
号码工作台.exe help
号码工作台.exe normalize input.txt output.txt
号码工作台.exe process source.txt output.txt valid_customers.txt opened_customers.txt invalid_customers.txt
```
