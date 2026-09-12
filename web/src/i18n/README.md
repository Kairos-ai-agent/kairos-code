# i18n — 多国语言 / multi-language UI

**63 种语言**，全部可切换。同一语言的多个 locale（en-GB、fr-CA、nl-BE、pt-PT、zh-TW/zh-HK）只保留主 locale —— 变体的机器译文几乎重复，只在日期/拼写上有差别；要恢复某个变体，把它加进 `scripts/gen_languages.mjs` 的 `KEEP_VARIANTS`。切换入口在 **设置抽屉顶部 → 显示语言 / Display
language**（带搜索的下拉，选项用各自的原生语言名显示），选择写入
`localStorage['kairos-lang']` 并在刷新后保持；首访按浏览器语言自动匹配。
`<html lang>` 与 `<html dir>` 一起跟着切 —— RTL 语言（ar / fa / he / ku / ur）
会自动镜像布局。

## 架构（为什么能撑 70 种而不撑爆 bundle）

```
web/src/i18n/parts/*.json      作者维护的源文案：{key: {zh, en}}
web/src/i18n/catalog/<lang>.json  其它语言的译文：{key: "文本"}（每语言一个文件）
web/src/i18n/languages.json    语言清单（63 行，scripts/gen_languages.mjs 生成）
web/src/i18n/languages.ts      生成的语言表：原生语言名 + antd/dayjs 懒加载器
web/src/i18n/locales/<lang>.ts 生成物（scripts/merge_i18n.py），每个导出 `dict`
web/src/i18n/index.tsx         Provider / useT / tGlobal / 检测 / 懒加载
```

* 每门语言的 **antd locale 对象、dayjs locale、词典** 都是动态 `import()`，
  各自成为一个 chunk —— 首屏只加载当前语言，另外常驻 en-US/zh-CN 两个兜底列。
* **回退链**：`catalog[lang]` → `zh-CN`（其它中文变体）或 `en-US` → `humanizeKey()`。
  所以某语言词典哪怕只翻了一半，界面也绝不会出现 `loop.plan.approved` 这种原始键。
* 新增语言 = 翻译 `catalog/<lang>.json` + 跑一次 `merge_i18n.py`，**不改代码**。

## 维护命令

```bash
python scripts/merge_i18n.py                # 生成 63 个 locales/*.ts
python scripts/merge_i18n.py --check        # 校验生成物是否最新
python scripts/merge_i18n.py --coverage     # 每语言翻译覆盖率表
python scripts/merge_i18n.py --strict       # 覆盖率不足 100% 时退出码 1

python scripts/translate_i18n.py --list                 # 同上，另一种视图
python scripts/translate_i18n.py --langs ja-JP --limit 40   # 小样
python scripts/translate_i18n.py --all --workers 5      # 全量（可断点续跑）

node scripts/gen_languages.mjs             # 语言表（antd 升级后重跑）
```

翻译走 `data/settings.json` 里已配置的 API（默认强制 `deepseek-chat`：配置里的
思考型模型会烧光预算返回空正文）。**每个 batch 落盘一次**，中断后重跑只补缺口。
脚本会校验键集、空值、以及 `{placeholder}` 是否与源文案一致，不一致的会报出来。

## 门禁（“完全切换，不是部分”）

```bash
cd web && node ../scripts/check_i18n.mjs   # 0 硬编码 / 0 缺键
npx vitest run src/test/i18n.test.tsx src/test/i18nKeys.test.ts
npx tsc --noEmit
```

* `check_i18n.mjs`：TS 编译器 AST 扫描 43 个源文件 —— JSX 文本、
  `placeholder/title/label/description/emptyText/okText/cancelText/aria-label`、
  Tabs/表格列的 `label` 等对象字段、`message.*`/`Modal.confirm` 文案、
  **`call-arg`（`msgApi.error('…')` / `setXxxError('…')` 这类不经过 JSX 的界面文案）**、
  以及 `t('key')` 引用了但字典里没有的键。
* `i18nKeys.test.ts`：同样的不变量在 vitest 里再查一遍（`npm test` 会拦），
  并断言 **63 个 locale 模块每个都拥有完整键集、无空值**；
  覆盖率低于 100% 会在 `I18N_STRICT=1` 时失败（部署前用）。

## 代码约定

1. 组件里 `const t = useT()`；**模块级函数**（stores、utils、`agoLabel()`、
   `diffLabel()`）用 `tGlobal()` —— `useT` 是 hook，在组件外用会直接 tsc 报错。
2. 复用 `common.*` 词汇；同义不同译会被 `merge_i18n.py` 报为 warning。
3. 键名扁平：`<area>.<component>.<slug>`，插值 `{name}`。
4. **句子由数据拼装**，不要翻译后端存的整句：例如 `kairos/alerts.py` 存的是英文句子，
   但 alert 同时带 `kind/severity/metric/baseline/current/delta_pct`，
   `AlertPanel.alertText()` 按语言重建句子，未知 kind 才回退原文。
5. 状态/枚举值（`active`、`done`、severity…）用映射表 + 回退，别直接渲染原始值。

## 踩过的坑（改动文案前先看）

* **动态键两个校验器都看不见**：`src/llm/presets.ts` 里 `label/hint` 存的是键字符串，
  渲染处必须 `t(p.label)`；漏了界面就会显示 `preset.deepseek.label`。
* **单小写词是文案**（`crit`/`muted`/`flaky`/`active`），驼峰/全大写标识符不是。
* **替换裸词会误伤代码**：`turn`/`score`/`add` 同时出现在 JSX 文本和 `event.turn`/`r.score`
  里，替换后必须立刻 `tsc` 并扫 `\.\{t\(` 残留。
* **JSX 内嵌片段**（`{n} chars · {m} bytes`）要抽成带参数的键，别只替换半边。
* **函数实参也是界面文案**，见上面的 `call-arg` 规则。
* **RTL**：`AntD` 读的是 `<ConfigProvider direction>`（不是 `<html dir>`）；间距要用
  逻辑属性（`marginInlineStart` 而非 `marginLeft`），否则 RTL 下不镜像。
* **作者列（zh-CN / en-US）永远不能被 catalog 覆盖**：翻译脚本曾经把 en-US 也"翻译"了一遍，
  生成的 `catalog/en-US.json` 里一条被模型回吐成嵌套对象，直接把作者维护的英文列盖掉，
  界面渲染出 `{'en': '0% done'}`。现在 merge 对这两个语言**忽略 catalog**，
  且拒绝任何非字符串值（只拒"序列化对象/数组"，`{n} Dateien` 这种以占位符开头的正常文案不受影响）。
* **请求 payload 必须是单层扁平 map**：发 `{key: {en, zh_hint}}` 时模型会**照抄这个嵌套结构**返回
  `{"strings": {...}}`，于是所有带占位符的键永远被拒、永远补不上（覆盖率卡在 ~96% 不动）。
  现在 payload 只有 `{key: english}`，响应侧另有 `unwrap()` 兜底解包 `strings/translations/…`。
* **占位符一致性是硬校验**：译文里的 `{n}` 多了/少了会被翻译脚本拒绝（不写库，下次重试），
  并有 vitest 守卫跨全部 63 语言比对源文案的占位符集合 —— 防止渲染出 "1 score 1"。
* **unused-key 列表只是参考**：动态键（`preset.*`）永远不会被算作“已使用”，不要据此删键。

## 人工验收（“完全切换”长什么样）

切到某语言后逐页走 对话 / 今天 / 工具 / 循环 / 项目 + 设置抽屉：正文不应出现整句英文
（模型名、用户数据、`API key`/`endpoint URL` 这类技术术语是预期的）。切到 English，
同样页面 **CJK 必须为 0**（聊天正文除外）。RTL 语言额外确认布局镜像。
