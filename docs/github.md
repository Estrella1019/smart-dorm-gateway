# 上传到 GitHub

本项目尚未绑定远程仓库。你可以创建一个空的公开仓库，建议名称 `smart-dorm-gateway`，不要在 GitHub 创建时额外初始化 README，以避免首次推送发生历史冲突。

**只把这个 smart-dorm-gateway 文件夹作为仓库根目录**，不要在包含课堂照片和 Word 文件的上层目录初始化。当前没有选择开源许可证；如需要授权他人复用，先由小组确定合适许可证再添加 LICENSE。

在此目录运行（将占位地址替换为你实际创建的仓库地址）：

```bash
git init -b main
git add .
git status --short
git diff --cached --stat
git commit -m "Initial runnable gateway scaffold"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

推送前确认暂存内容只有项目代码与文档。`.gitignore` 已排除本地配置、环境变量文件、数据库、日志与常见密钥文件，但它不能自动识别所有敏感内容。不要提交 Token、密码、个人信息或测试时的真实外部数据。

GitHub 登录使用你自己的客户端或凭据管理工具，不把 Token 写入 README 或 remote URL。推送后查看 Actions 测试结果，再邀请组员协作。是否开放许可证、仓库保护规则由小组决定。

## 日常分支约定

建议分支名：`feat/device-simulators`、`feat/control-service`、`feat/web-console`、`feat/logging-tests`。一次 Pull Request 聚焦一个可验证增量，写清新增行为、运行命令和测试结果。协议和共享接口修改先由组长协调。
