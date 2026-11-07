# BTC Options Toolkit

用于观察 Binance BTC European Options 的网页工具，包含公共 Put 收益率矩阵和可选的只读账户强平风险分析。

公共收益矩阵不需要 API Key。Risk 功能读取后端配置的账户和仓位，估算不同 BTC Index、IV 与估值时间下的保证金风险边界；结果是压力场景估算，不是 Binance 保证的强平成交价。

## 功能与技术栈

- Vite、React、TypeScript 与纯 CSS 前端，Flask 后端。
- 公共行情来自 Binance Options，权利金使用 `markPrice`，IV 使用 `markIV`。
- 支持 Actual Yield、Secured Yield、到期日与行权价矩阵、主题及中英文切换。
- Risk 支持 Classic Options Margin 只读账户、IV 场景、估值时间、触发边界、对账与仓位贡献。
- 测试使用 Vitest、React Testing Library 和 Pytest。

计算口径、边界条件和合成案例见[强平风险计算说明](docs/liquidation-risk-calculation.md)。固定夹具仅供测试，不含真实账户标识。

## 本地开发

安装依赖：

```powershell
npm ci
python -m pip install -r requirements.txt
```

启动：

```powershell
npm run dev
```

页面地址为 `http://127.0.0.1:5173/2_options_calc/`。Flask 默认监听 `127.0.0.1:5004`，Vite 将页面的 API 请求代理到本机后端。

`npm run dev` 在导入 Flask 应用前加载 `.env.local`，已有进程环境变量优先。前端开发服务器仅监听回环地址。

常用命令：

```powershell
npm run dev:web
npm run dev:server
npm run start
npm test
npm run build
```

`dev:web` 和 `dev:server` 分别只启动前端和开发后端；`start` 直接启动 Flask 后端并读取进程环境变量，不自动加载 `.env.local`。

构建先检查 TypeScript，再生成 `dist/`。构建版本来自 Git 标签；没有 Git 元数据时使用 `package.json` 的版本。

## 可选的只读账户配置

账户配置仅在后端读取。请在本地忽略的 `.env.local` 或运行环境中提供以下变量，不要提交真实值：

```dotenv
BINANCE_OPTIONS_ACCOUNT_IDS=account_a
BINANCE_OPTIONS_DEFAULT_ACCOUNT_ID=account_a
BINANCE_OPTIONS_ACCOUNT_ACCOUNT_A_LABEL=Account A
BINANCE_OPTIONS_ACCOUNT_ACCOUNT_A_API_KEY=
BINANCE_OPTIONS_ACCOUNT_ACCOUNT_A_SECRET_KEY=
```

账户 ID 只允许小写字母、数字和下划线；单账户变量后缀使用 ID 的大写形式。API Key 只启用读取权限并限制允许访问的 IP。不要启用交易、划转或提现权限。

网络相关设置由 `BINANCE_PROXY`、`BINANCE_REQUEST_TIMEOUT` 和 `BINANCE_CACHE_TTL_SECONDS` 控制。设置 `BINANCE_PROXY=` 可禁用显式代理；未设置时默认尝试本机 `127.0.0.1:7890`，连接失败后直连。公共行情的进程内缓存默认 15 秒；多个 worker 的缓存相互独立。

## Risk 认证与会话管理

Risk 静态页面可公开加载，账户列表和计算结果需要认证。认证配置缺失或不可用时，Risk 数据接口失效关闭，公共收益矩阵不受影响。

认证使用共享密码的 Argon2id 哈希与服务端会话。正常 HTTPS 环境的 Cookie 带 `Secure`、`HttpOnly` 和 `SameSite=Strict`，路径为 `/2_options_calc/risk/`。Cookie 为 400 天滚动有效期，服务端会话不自动过期；退出、管理员撤销或密码轮换会使会话失效。

管理 CLI 接受显式的环境文件和备份目录：

```powershell
python -m server.manage_risk_auth --env-file .env.local --backup-dir .private/auth-backups set-password
python -m server.manage_risk_auth --env-file .env.local sessions
python -m server.manage_risk_auth --env-file .env.local revoke <session-id>
python -m server.manage_risk_auth --env-file .env.local revoke-all
```

运行前手工创建 `.env.local`，并为 `OPTIONS_CALC_RISK_SESSION_DB` 设置当前机器上的绝对路径。密码命令隐藏输入，更新 `OPTIONS_CALC_RISK_PASSWORD_HASH` 和 `OPTIONS_CALC_RISK_SESSION_GENERATION`，并撤销旧会话；执行后重启应用以加载新配置。环境备份和会话数据库同样不得提交。

密码命令默认启用安全 Cookie。本机仅使用 HTTP 调试时，执行密码命令后可在 `.env.local` 设置 `OPTIONS_CALC_RISK_COOKIE_SECURE=false` 并重启；对外提供服务时必须使用 HTTPS 和安全 Cookie。不要把任何后端凭据放进 `VITE_` 环境变量。

## 安全边界

本项目不下单、不撤单、不划转、不提现。浏览器不保存 Binance Key、Secret 或原始账户响应。请勿提交真实凭据、密码哈希、会话数据库、账户快照、日志、环境备份或私人运维配置。

本仓库不包含特定环境的部署、进程管理或反向代理配置。应用部署方需要在自己的运维环境中配置 HTTPS、可信代理头、访问限制和服务权限。开发服务器用于本机调试。

## 验证

```powershell
npm test
npm run build
```

修改界面后检查约 1280px、390px 和 320px 宽度：页面不能横向溢出，矩阵可在自身容器横向滚动，长收益率数字不能截断。Expiry 列在宽度大于 520px 时保持横向 sticky，520px 及以下关闭。
