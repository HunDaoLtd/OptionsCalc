# 项目协作说明

## 项目范围

本项目是 Vite + React + TypeScript 前端与 Flask 后端组成的 BTC Options 数据工具。公共收益矩阵使用 Binance 公共行情；可选 Risk 功能只读取后端配置的 Classic Options Margin 账户。

仓库只包含通用应用、开发配置、测试与计算文档。特定环境的部署脚本、服务配置、日志、账户配置和私人运维资料保存在仓库之外，不要重新加入。

## 安全约束

- 不引入下单、撤单、账户写入、划转、提现或其他交易能力。
- Binance API Key 与 Secret 仅在后端读取，权限限定为只读；不得进入前端、日志、测试夹具或仓库。
- 不提交真实账户标识、账户原始响应、密码哈希、Cookie、会话数据库、环境文件或环境备份。
- 不把后端秘密放入 `VITE_` 变量；公开示例使用合成数据和通用账户名称。
- 保留认证失效关闭、同源检查、Cookie 安全属性和敏感响应禁止缓存的边界。
- Risk 的 400 天滚动 Cookie 和服务端长期会话是既定行为，未经明确要求不要改变。
- 不修改用户的 Git 全局或本地代理配置。

## 开发与验证

```powershell
npm ci
python -m pip install -r requirements.txt
npm run dev
npm test
npm run build
```

开发页面为 `http://127.0.0.1:5173/2_options_calc/`，后端默认监听 `127.0.0.1:5004`。本地配置使用已忽略的 `.env.local`；开发服务器保持回环监听。

认证管理使用 `python -m server.manage_risk_auth`，通过 `--env-file` 与 `--backup-dir` 指定本机文件。不要使用真实凭据编写测试。

常规改动后运行 `npm test` 和 `npm run build`。涉及界面时额外检查约 1280px、390px 和 320px 宽度，页面自身不能横向溢出，矩阵和长表只在容器内滚动。

## 计算与界面约束

- 未获明确要求不要改变收益计算逻辑。
- 权利金使用 `markPrice`，IV 使用 `markIV`。
- `netPremium = markPrice - max(strike - indexPrice, 0)`。
- Actual Yield 分母为 `Margin / Contract`，Secured Yield 分母为 `strike`。
- 矩阵数字通常保留一位小数，BTC Index 保留两位小数。
- 单元格左上 IV、左下收益率、右上 mark、右下 net；mark 与 net 展示相同时不重复显示 mark。
- Expiry 列在宽度大于 520px 时横向 sticky，520px 及以下关闭；不能遮挡收益数据。
- 收益率可能上千甚至上万，不得用省略号截断。
- 保持紧凑、清爽的数据工具风格。主题按钮仅显示颜色方块，选中态轻量。

## Git 约束

操作前检查分支、上游、工作区及待推送/待拉取提交。保留用户已有改动，只提交当前任务相关文件；未经授权不得强推、改写已发布历史或删除远端分支。

提交信息使用中文标题，空行后以 `1、...`、`2、...` 编号正文说明实际改动，即使只有一项也需正文。

## 主要文件

- `src/main.tsx`、`src/risk-page.tsx`：页面和状态。
- `src/styles.css`：主题、布局和响应式样式。
- `src/lib/options.ts`：收益矩阵计算。
- `src/risk-api.ts`：Risk 请求与响应校验。
- `server/app.py`：Flask 路由与 API 边界。
- `server/risk_auth.py`、`server/manage_risk_auth.py`：认证与管理 CLI。
- `server/risk_binance.py`、`server/risk_accounts.py`：只读账户客户端和配置。
- `server/risk_service.py`、`server/risk_calculator.py`：风险编排和纯计算。
- `tests/`：前后端测试及合成夹具。
- `docs/liquidation-risk-calculation.md`：计算口径和边界条件。
