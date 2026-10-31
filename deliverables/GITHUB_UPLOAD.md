# GitHub 上传说明

本机没有配置 GitHub 账号/远程仓库，所以**没有伪造公开地址**。源码已在本地初始化 git，按下面步骤上传后，把真实地址替换到 `作品信息.md`。

## 1. 创建远程仓库

在 GitHub 新建空仓库，例如 `dunyu-bank-agent`，不要勾选 README（本地已有）。

## 2. 推送

```powershell
cd D:\银行agent项目\bankagent
git remote add origin https://github.com/<your-account>/dunyu-bank-agent.git
git branch -M main
git push -u origin main
```

如果已配置 SSH：

```powershell
git remote add origin git@github.com:<your-account>/dunyu-bank-agent.git
git push -u origin main
```

## 3. 上传后检查

- 仓库根目录能看到 `README.md`、`backend/`、`frontend/`、`docs/`、`tests`；
- 不包含 `backend/data/*.db`、`backend/.env`、`portable_build/`、`dist/`（`.gitignore` 已排除）；
- 在 `作品信息.md` 填入真实 URL：

```
GitHub 仓库地址：https://github.com/<your-account>/dunyu-bank-agent
```

## 4. 当前提交

`git log -1` 可看到本地提交；提交包不包含远程地址，避免被误认为已公开。
