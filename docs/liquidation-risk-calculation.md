# Binance Options 强平风险计算说明

> 状态：最终实现说明
>
> 更新日期：2026-08-06
>
> 适用范围：Binance Classic Options Margin、BTC/USDT European Options

本文是本项目强平风险功能的计算口径、边界条件和合成验收案例说明。

## 1. 先说结论

- 工具计算的是 **95% 保证金比例场景触发边界**（scenario trigger estimate），不是 Binance 承诺的强平成交价。
- 当前账户指标直接读取 Binance；未来场景中的期权价值使用 Black-Scholes 模型重估。
- 每个仓位使用自己的 `markIV`，然后统一加上同一个 IV shift。界面上的 `referenceIv` 只用于解释横轴，不参与定价。
- 默认一次计算 `-50` 到 `+150`、步长 `10 vol points` 的 21 个 IV 场景。`-10 vol` 表示 IV 减少 10 个百分点，不是乘以 90%。
- Valuation time 会改变剩余期限、期权时间价值和到期处理；它可能对某些仓位不敏感，但不能从公式中删除。
- 每个场景扫描 `0.2 × 当前 BTC Index` 到 `2.0 × 当前 BTC Index`，使用 2,001 个基础节点并加入当前 Index 和持仓行权价，再以不超过 1 USDT 的区间精度细化边界。
- 混合 Call/Put、Long/Short 组合可能同时存在 Lower 和 Upper Trigger，也可能存在多个边界；结果不保证随 IV 单调变化。
- 详情页还会在同一扫描区间内寻找剩余仓位组合的全部损益两平价；该补充结果不参与 95% 强平触发判断。

## 2. 数据来源、可计算项与不可计算项

### 2.1 Binance 输入

| 输入 | Binance 来源 | 用途 |
| --- | --- | --- |
| BTC Index、源时间 | `GET /eapi/v1/index?underlying=BTCUSDT` | 当前价格、扫描中心和数据新鲜度 |
| 合约方向、行权价、到期时间、Contract Unit | `GET /eapi/v1/exchangeInfo` | BSM、OTM、结算和名义金额 |
| Maintenance Margin、Minimum Maintenance Margin、Liquidation Fee Rate | `GET /eapi/v1/exchangeInfo` | 空头维持保证金 |
| `markPrice`、`markIV`、`riskFreeInterest` | `GET /eapi/v1/mark` | 当前对账、场景波动率和 BSM 利率 |
| `adjustedEquity`、`maintMargin`、`unrealizedPNL` | `GET /eapi/v1/marginAccount` | 当前账户指标与基线对账 |
| 仓位、带符号数量、`entryPrice`、`markValue`、逐仓 `unrealizedPNL`、更新时间 | `GET /eapi/v1/position` | Long/Short、详情展示、当前多头价值和场景仓位 |

官方 API 字段可对照：

- [Option Margin Account Information](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-options/api/rest-api/account#option-margin-account-information)
- [Option Position Information](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-options/api/rest-api/trade#option-position-information)
- [Option Mark Price](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-options/api/rest-api/market-data#option-mark-price)
- [Binance Options Clearing Procedures](https://bin.bnbstatic.com/static/cms/cg08ou2ak0tn7mcplvfg/file/53197b612332da02c20b5b7d19b81ff53ee5f4938c6330c72a30a1ca4f91049f.pdf)

风险功能只调用账户和仓位只读端点，不下单、不划转、不提现，也不把 API Key 或账户响应保存到浏览器。

### 2.2 工具能够派生的结果

- 当前官方保证金比例与是否已经触发。
- `derivedWalletBalance`、当前 Long 期权价值。
- 每个 BTC Index、Valuation time、IV shift 下的期权理论价格。
- 场景 Adjusted Equity、维持保证金、保证金比例和触发原因。
- 全部触发边界，以及距当前最近的 Lower/Upper Trigger。
- 估值时点剩余仓位组合的全部损益两平价；`entryPrice` 仅用于详情展示，不作为损益零点基线。
- Focus 场景当前 Index 点的逐仓位 MM 贡献和占比。
- 账户参考 IV、对账偏差、仓位覆盖率、数据时间偏差和类型化警告。

### 2.3 工具无法知道的未来状态

- Binance 最终强平委托的成交价、滑点、市场深度和专门做市商报价。
- 未来的 IV smile/skew、相关性和利率变化。本工具采用“每个合约当前 `markIV` 平行移动”的静态场景。
- 估值时刻之前可能发生的交易、订单成交、充值、提现、划转、费用和盈亏变化。
- 未来到期前 30 分钟的真实结算均价。本工具只能用场景 BTC Index 近似。
- 未纳入的开放订单成交后会形成的仓位。

因此页面和文档统一使用“触发边界估算”，不能把结果当作可成交的止损价或 Binance 官方清算价格。

## 3. 计算流程

```text
Binance 当前账户与仓位
        │
        ├─ 当前官方 AE / MM / 保证金比例
        ├─ 当前 markPrice 对账 MM
        └─ official AE - 当前 Long mark value
                         ↓
                 derivedWalletBalance
                         │
BTC Index 扫描 × Valuation time × 21 个 IV shift
                         │
          BSM 场景价格 + 到期结算现金流
                         │
        场景 Adjusted Equity 与场景 MM
                         │
                检查 95% 触发条件
                         │
       找全部状态边界并以 1 USDT 精度求根
```

账户、仓位、合约和 Valuation time 每次请求只校验一次。21 个 IV 场景共享 BTC 扫描节点、`log(S/K)`、OTM、基础 MM、到期现金流和期限常量，只重复计算各 IV 真正不同的 `d1/d2`、期权价格及场景汇总。

## 4. 公式

### 4.1 符号与单位

| 符号 | 含义 |
| --- | --- |
| `S` | 场景 BTC Index，USDT |
| `K` | 行权价，USDT |
| `T` | `max(expiryMs - valuationTimeMs, 0) / 31,536,000,000` |
| `r` | Binance `riskFreeInterest` |
| `σᵢ` | 第 `i` 个合约的 `markIVᵢ + ivShiftVolPoints / 100` |
| `unit` | Contract Unit |
| `q` | 带符号仓位数量；Long 为正，Short 为负 |
| `N(x)` | 标准正态分布累积分布函数 |

所有内部计算使用未舍入的有限双精度数；金额单位为 USDT，时间单位为整数毫秒。展示层再进行格式化。

### 4.2 未到期期权的 Black-Scholes 场景价格

```text
d1 = [ln(S/K) + (r + σ²/2)T] / (σ√T)
d2 = d1 - σ√T

Call = unit × [S·N(d1) - K·e^(-rT)·N(d2)]
Put  = unit × [K·e^(-rT)·N(-d2) - S·N(-d1)]
```

股息率固定为 0。`S > 0`、`K > 0`、`T > 0`、`unit > 0`、`σ > 0`；`r` 只要求为有限数。模型结果小于 0 时按 0 处理。

当前官方指标与 MM 对账仍使用 Binance 当前 `markPrice`。只有未来场景点使用 BSM，避免把模型误差混进当前账户基线。

### 4.3 IV shift 与 Reference IV

对每个未到期仓位分别计算：

```text
scenarioVolatilityᵢ = markIVᵢ + ivShiftVolPoints / 100
```

例如 `markIV = 0.65`：

```text
-10 vol → σ = 0.55
+10 vol → σ = 0.75
```

如果某个合约在某档 shift 下得到 `σ <= 0`，该 IV 场景单独返回 `non_positive_scenario_volatility`；其他场景继续计算，不能把 IV 钳制为 0。

单账户 `referenceIv` 只是未到期仓位按绝对数量加权的展示值：

```text
referenceIv
= Σ(markIVᵢ × abs(qᵢ))
 / Σ(abs(qᵢ))
```

All accounts 图中的 reference IV 是可用账户 `referenceIv` 的算术平均。两者都不替代每个合约自己的 `markIV`，也不进入 BSM 定价。

### 4.4 OTM Amount

```text
Call OTM = min(S - K, 0)
Put  OTM = min(K - S, 0)
```

OTM Amount 按 Binance 公式是非正数。不能先转成正数再直接加进 MM。

### 4.5 空头维持保证金

每个未到期 Short 仓位：

```text
MM_position = {
  max(
    S × minMaintenanceMargin,
    S × maintenanceMargin + OTM
  ) × unit
  + scenarioOptionPrice
  + S × unit × liquidationFeeRate
} × abs(q)

MM_total = Σ MM_position
```

所有费率来自当次 `exchangeInfo`，不在代码中硬编码。Long 仓位的 MM contribution 为 0，但其场景价值进入 Adjusted Equity。

Binance `marginAccount.asset[].maintMargin` 在实测中可能不含 liquidation fee reserve。当前对账会同时比较含储备和不含储备的口径并取更接近官方值的一项；场景 MM 和触发判断始终使用上面的含储备公式。

### 4.6 Adjusted Equity 与派生钱包

工具不尝试重造当前 Binance Adjusted Equity。它从官方值反推一个固定的钱包基数：

```text
derivedWalletBalance
= officialAdjustedEquity
- Σ currentLongPositionMarkValue
```

普通未到期场景：

```text
scenarioAdjustedEquity
= derivedWalletBalance
+ Σ scenarioLongOptionValue
+ Σ expiredPositionSettlementCashflow
```

这意味着：

- Short 期权理论价格进入 MM，不直接加回场景 AE。
- Long 期权理论价格进入场景 AE，其 IV 和时间价值可能显著影响触发边界。
- `derivedWalletBalance` 是反推值，界面必须标记为 Derived，不能把它误写成 Binance 直接返回的钱包余额。

### 4.7 账户损益两平价

详情页的损益两平价只描述估值时点仍未到期的仓位。当前时点尚无仓位到期时，以 Binance 当前账户 `unrealizedPNL` 为锚点，不使用开仓均价重新构造当前损益；若估值时点已有部分仓位到期，则先扣除这些仓位当前的逐仓 `unrealizedPNL`。每个 BTC 指数情景的剩余仓位组合损益为：

```text
remainingPnlBaseline
= officialAccountUnrealizedPnl
- Σ expiredPositionCurrentUnrealizedPnl

scenarioRemainingPositionPnl
= remainingPnlBaseline
+ Σ remainingPosition(
     scenarioOptionPrice - currentMarkPrice
  ) × signedQuantity
```

已到期仓位不再进入损益两平价计算；它们仍按既有口径通过包含行权费的 `settlementCashflow` 进入强平分析的场景 AE。工具在与强平分析相同的 BTC 指数扫描区间（当前指数的 20%–200%）内寻找全部剩余仓位 PNL 零点，价格细化精度为 1 USDT，并合并 1 USDT 内的重复根。零点搜索同时处理符号穿越、采样点恰好为零以及不改变符号的切触零点。

详情接口通过 `remainingPositionCount` 返回估值时点仍未到期的非零仓位数。`breakEvenPrices` 的含义为：数组表示升序排列的全部零点；空数组表示扫描区间内没有零点，若 `remainingPositionCount` 同时为 `0`，则表示估值时点已经没有持仓；`null` 表示补充计算不可用。损益两平价不可用不会使原有强平情景失效。

### 4.8 到期与行权费

当 `valuationTimeMs >= expiryMs` 时不再使用 BSM：

```text
Call payoff = max(S - K, 0) × unit
Put payoff  = max(K - S, 0) × unit

exerciseFeePerContract
= min(0.015% × S × unit, 10% × intrinsic × unit)

settlementCashflow
= payoff × q - exerciseFeePerContract × abs(q)
```

到期仓位不再贡献 MM，其现金流进入场景 AE，并产生 `expired_position_settled` 和 `expiry_settlement_approximation` 提示。

若 `expiryMs - 30 分钟 <= valuationTimeMs < expiryMs`，当前官方指标仍可显示，但场景和触发价返回 `near_expiry_trigger_disabled`，因为普通 BSM 不再对应 Binance 到期前最后阶段的 Mark/结算规则。

### 4.9 保证金比例与三类触发条件

通常使用：

```text
marginRatio = maintenanceMargin / adjustedEquity
```

每个场景点按下面的互斥顺序判断：

```text
1. MM > 0 且 Adjusted Equity <= 0
   → triggered: non_positive_adjusted_equity

2. MM > 0、Adjusted Equity > 0，且 MM / Adjusted Equity >= 0.95
   → triggered: margin_ratio_threshold

3. MM = 0、derivedWalletBalance < 0、eligibleLongOptionValue > 0，且
   abs(derivedWalletBalance) / eligibleLongOptionValue >= 0.95
   → triggered: negative_wallet_long_value_ratio
```

Adjusted Equity 小于等于 0 时，`marginRatio` 返回 `null`，不能继续除法。第三类条件没有可用 Long 价值时返回 `no_eligible_long_option_value`，不制造无穷比例。

## 5. 当前值、场景当前点与触发价不要混淆

页面上存在三类看起来相近但口径不同的数据：

| 数据 | 口径 | 是否受 IV/Valuation time 影响 |
| --- | --- | --- |
| `current` | Binance 当前官方 AE、MM 和 Mark | 否 |
| 场景 `currentPoint` | 把 `S` 固定为当前 Index，再按所选 IV/时间用 BSM 重估 | 是 |
| Lower/Upper Trigger | 在整个 BTC Index 扫描范围内寻找 95% 状态边界 | 是 |

因此官方当前保证金比例和 `+0 vol` 的场景当前点不必完全相等。二者差异主要来自当前 `markPrice` 与 BSM 重估值并不完全一致；这不表示账户字段或强平公式计算错误。

`current.triggeredNow` 只由当前官方值判断，不随用户切换 IV 或 Valuation time。场景图中的当前点和触发边界则会变化。

## 6. 触发边界搜索

1. 扫描范围固定为 `[0.2 × currentIndex, 2.0 × currentIndex]`。
2. 生成 2,001 个包含首尾的等距节点。
3. 加入当前 BTC Index 和范围内的全部持仓行权价，去重后排序。
4. 对每个节点计算场景 AE、MM、比例和触发状态。
5. 找出所有 `not triggered ↔ triggered` 状态变化区间。
6. 对每个区间二分细化，直到价格区间不超过 1 USDT。
7. 当前 Index 以下最近的边界为 Lower Trigger；以上最近的边界为 Upper Trigger。全部边界仍保存在 `triggers`。

没有边界只表示给定扫描范围内没有找到状态切换，不表示所有价格都安全。应结合：

- 当前是否已经触发；
- 曲线是全程安全还是全程触发；
- 是否有 `unavailable` 或数据警告；
- 实际扫描上下限。

API 的 `scan.sampleCount` 始终返回基础扫描密度 `2001`。Focus 的内部求根仍使用完整节点，但网络响应中的绘图曲线压缩为目标 401 点；首尾、当前 Index、行权价、边界两侧和状态/原因切换节点被强制保留。压缩只影响绘图数据量，不影响触发价和当前点贡献表。

## 7. IV 和 Valuation time 的敏感性

### 7.1 IV 为什么可能看起来“不影响强平价”

IV 只通过期权场景价格进入计算：Short 的期权价格进入 MM，Long 的期权价格进入 AE。若触发边界处的期权已经深度 ITM/OTM、非常接近到期，或者组合中各仓位 Vega 相互抵消，场景价格对 IV 的变化会很小，多个 IV 档位的触发价就会靠得很近。

这说明该组合在该边界附近对 IV 不敏感，不表示 IV 在公式中没有作用。以下组合通常更敏感：

- 有较长期、接近平值的 Short 期权；
- Long 期权价值占 AE 比例较高；
- Call/Put 混合使上下边界落在高 Vega 区域；
- 当前保证金比例已经接近 95%。

### 7.2 为什么需要向下减 IV

只计算向上 IV 会漏掉“波动率回落”的方向。`-10 vol` 至少有三项用途：

- 检查 IV 回落是否让 Short 期权价格和 MM 降低、把触发边界推远；
- 检查 Long 保护腿贬值是否反而降低 AE、把风险拉近；
- 验证组合净 Vega 的方向以及触发价是否非单调。

所以 IV 向下减有用，但它不是压力最坏情景的同义词。对净 Long Vega 组合，降 IV 可能更危险；对净 Short Vega 组合，升 IV 往往更危险；混合组合必须实际计算。默认覆盖 `-50～+150 vol` 正是为了同时观察两个方向。某档导致任一合约 `σ <= 0` 时，该档显示不可用是正确行为。

### 7.3 Valuation time 为什么有时变化不大

Valuation time 通过 `T`、贴现因子和到期状态进入计算，但它不修改 Binance 当前官方卡片。对深度实值/虚值或剩余时间很短的仓位，Theta 对触发边界可能很弱；如果把时间推进到某个到期日之后，仓位会从 BSM/MM 切换为结算现金流，结果可能突然明显变化。

因此 Valuation time 的用途是回答“如果仓位和钱包不变，到未来这个时刻，触发边界可能在哪里”，不是预测这段时间内账户真的会保持不变。

## 8. 接近真实的脱敏组合案例

本例来自仓库固定夹具 [classic-options-snapshot.json](../tests/fixtures/liquidation_risk/classic-options-snapshot.json)。它按 Binance 真实字段结构构造，但不含真实账户 ID、API Key 或资产归属。

### 8.1 初始快照

- 时间：2026-08-01 00:00 UTC。
- BTC Index：`62,885.16`。
- Binance 官方 Adjusted Equity：`18,263.454137`。
- Binance 官方 Maintenance Margin：`13,295.794612`。
- 官方当前保证金比例：`72.8%`。
- 6 个仓位：4 个 Short、2 个 Long，包含 Call/Put，行权价 `55,000～70,000`，到期日在 8 月 2 日至 9 月 25 日。
- 各仓位 markIV 为 `52%～67%`；按绝对数量加权的 `referenceIv = 58.6154%`。
- 当前两个 Long 的 Mark Value 合计 `1,010`，所以：

```text
derivedWalletBalance
= 18,263.454137 - 1,010
= 17,253.454137
```

### 8.2 Now：IV 向下和向上同时计算

| IV shift | 展示参考 IV | 场景当前比例 | Lower Trigger | 距当前 | Upper Trigger | 距当前 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| -50 vol | 8.6% | 62.7% | 58,552 | -6.9% | 71,531 | +13.7% |
| -10 vol | 48.6% | 75.0% | 59,385 | -5.6% | 70,410 | +12.0% |
| 0 vol | 58.6% | 78.2% | 60,013 | -4.6% | 69,881 | +11.1% |
| +10 vol | 68.6% | 81.3% | 60,313 | -4.1% | 69,407 | +10.4% |
| +50 vol | 108.6% | 92.6% | 62,276 | -1.0% | 64,902 | +3.2% |

说明：

- `-10 vol` 相比 `0 vol`，Lower Trigger 下降约 `628 USDT`，Upper Trigger 上升约 `529 USDT`，在这个混合组合上是有信息量的。
- IV 上升时，本例两个边界总体向当前 Index 收拢；但这只是该组合的结果，不是所有账户都必须满足的单调规律。
- 官方当前比例是 `72.8%`，而 `0 vol` 场景当前点是 `78.2%`。前者使用 Binance 当前官方 Mark，后者使用 BSM 在当前 Index 上重估，口径不同。
- `-50 vol` 的“展示参考 IV 8.6%”不是把所有合约都定价为 8.6%；实际仍是各合约 `markIV - 50 vol`，本例为 `2%～17%`。

### 8.3 固定 0 vol，改变 Valuation time

| Valuation time | 场景当前比例 | Lower Trigger | Upper Trigger | 备注 |
| --- | ---: | ---: | ---: | --- |
| Now | 78.2% | 60,013 | 69,881 | 所有仓位未到期 |
| +1d | 77.1% | 59,347 | 69,973 | 最早到期仓位尚余约 8 小时 |
| +7d | 52.2% | 55,113 | 77,799 | 最早仓位已按结算处理，产生结算近似警告 |

这个结果说明 Valuation time 在短时间内可能只小幅改变边界，但跨过到期日后可能产生结构性变化。+7d 的结果还依赖“场景 Index 近似未来结算价”，只能作为压力情景。

### 8.4 为什么不能用无持仓账户验证

若 账户 B 或其他真实账户在快照时没有未平仓期权，则 MM、Vega 和可重估期权价值都不足以形成有意义的 IV 敏感性案例；界面上的空值或接近值不能用来证明 IV 有效或无效。验证应使用像本例这样包含非零 Short/Long、多个行权价和到期日的组合。

## 9. 对账、可用性与警告

当前模型 MM 与 Binance 官方 MM 的偏差：

```text
mmDriftPercent
= abs(modelMM - officialMM)
/ max(abs(officialMM), 1)
× 100
```

| 对账状态 | 条件 | 行为 |
| --- | --- | --- |
| `matched` | 仓位覆盖完整且 drift ≤ 0.5% | 正常显示预测 |
| `warning` | 覆盖完整且 0.5% < drift ≤ 2% | 显示预测并警告 |
| `unreconciled` | drift > 2% 或缺仓位/合约参数 | 隐藏触发价 |
| `unsupported` | 非 Classic、非 USDT 或存在非 BTC 仓位 | 只显示可用官方值 |

任一相关仓位缺少合约、Mark、IV、利率或关键数值时，该账户相应场景返回 `unavailable`，不能把缺失值当 0。市场或账户缓存超过允许的 stale 时限也停止预测。

## 10. 性能实现与等价性验收

最终实现保留 21 个 IV 场景一次完整返回。批量内核采用“BTC 价格点优先、IV 场景批量计算”，总览不生成逐点 Dataclass 明细和贡献排序；只有 Focus 指定场景保留完整计算曲线，并只在当前 Index 点生成精确贡献表。

同机、同数据、两个账户 × 21 个 IV 场景的缓存命中基准：

| 指标 | 优化前 | 优化后 | 结果 |
| --- | ---: | ---: | ---: |
| 服务计算时间 | 2.321 s | 0.362 s | 降低约 84.4% |
| Focus 响应体 | 367,321 B | 74,904 B | 约 75 KB |
| Focus 绘图点 | 约 2,004 | 401 | 计算扫描仍为 2,001 基础点 |

使用当时 账户 A 与 账户 B 的同一快照，对旧单场景逻辑和新批量逻辑逐一比较 42 个场景：状态、方向、原因和边界数量一致，触发边界最大差异 `0 USDT`，当前场景比例最大差异 `0`。

## 11. 代码与测试入口

- 纯计算与批量场景内核：`server/risk_calculator.py`
- API 编排、对账和曲线压缩：`server/risk_service.py`
- Binance 只读账户与公共风险行情：`server/risk_binance.py`
- 后端类型：`server/risk_types.py`
- 前端类型与解析：`src/types.ts`、`src/risk-api.ts`
- 风险页：`src/risk-page.tsx`
- 固定夹具：`tests/fixtures/liquidation_risk/`
- 计算测试：`tests/test_risk_calculator.py`
- 服务测试：`tests/test_risk_service.py`

开发收尾验证命令：

```powershell
npm test
npm run build
```

最终验收覆盖负 IV、无效 IV、Long/Short、Call/Put、多边界、当前点边界、临近到期、跨到期、批量/单场景等价、Focus 曲线关键点保留，以及桌面、390px、320px 页面无根级横向溢出。
