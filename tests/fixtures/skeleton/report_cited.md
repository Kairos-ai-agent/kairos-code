# 三方案对比报告

| 维度 | alpha | beta | gamma |
|---|---|---|---|
| 延迟 | 约 40ms，延迟最低 [[alpha.md]] | 约 120ms，延迟最高 [[beta.md]] | 约 75ms，延迟居中 [[gamma.md]] |
| 成本 | 每百万 token 1.2 元，成本最高 [[alpha.md]] | 每百万 token 0.3 元，成本最低 [[beta.md]] | 每百万 token 0.6 元，成本居中 [[gamma.md]] |
| 部署方式 | 需要常驻 GPU 节点 [[alpha.md]] | 无服务器，按调用计费 [[beta.md]] | 容器化，可弹性伸缩 [[gamma.md]] |
| 适用场景 | 低延迟实时场景 [[alpha.md]] | 批量离线任务 [[beta.md]] | 中等并发的在线服务 [[gamma.md]] |

## 推荐
- 若优先追求低延迟实时体验：推荐 **alpha**，约 40ms 延迟，但成本最高且需常驻 GPU 节点 [[alpha.md]]。
- 若优先控制成本且任务可离线批量处理：推荐 **beta**，每百万 token 0.3 元且无服务器按调用计费，但延迟约 120ms [[beta.md]]。
- 若需要在延迟、成本和弹性之间平衡，面向中等并发在线服务：推荐 **gamma**，约 75ms、每百万 token 0.6 元、容器化可弹性伸缩 [[gamma.md]]。
- 综合默认推荐：**gamma**；实时低延迟选 **alpha**，批量离线选 **beta** [[alpha.md]] [[beta.md]] [[gamma.md]]。

SELF_CHECK: citations=3/3 ok (alpha.md, beta.md, gamma.md)