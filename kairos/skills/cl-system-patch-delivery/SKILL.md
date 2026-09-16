---
name: "cl-system-patch-delivery"
description: "|"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/.archive/cl-system-patch-delivery/SKILL.md"
---
# cl-system-patch-delivery

## 项目事实速览

- **项目位置**: E:\cl_system (Windows + git-bash,MSYS 路径可用)
- **技术栈**: Flask + SQLite + 原生 JS (jQuery + Bootstrap)
- **关键文件**:
  - `app.py` (~3100 行,所有 Flask 路由都在这里)
  - `core/db.py` (数据库连接 + schema + 启动时迁移)
  - `core/report.py` (报告生成 HTML)
  - `core/quotation_parser.py` (报价单 PDF/Excel/Word 解析)
  - `core/bom_parser.py` / `core/market.py` / `core/rfq.py` / `core/rfq_excel.py` / `core/report_excel.py`
  - `templates/index.html` + `static/js/app.js` (前端单文件)
- **数据库**: `cost_center.db` (项目根,首次启动自动建表)
- **启动**: 双击 `start.bat` 或 `python start.py`
- **重启前必做**: 关掉旧的 `start.py`(5788 端口冲突常见,看 `netstat -ano | grep 5788`)
- **数据操作前**: `cp cost_center.db cost_center.db.before_<reason>.bak`
- **默认管理员**: `admin / admin123` (在 `app.py` 启动时自动创建,见 L3028 `bcrypt.hashpw(b'admin123', ...)`)
  - 用 `Flask test_client` 做 e2e 验证时第一件事就是 POST `/api/auth/login` JSON 拿 session cookie,否则所有 `/api/*` 都被 401

## 关键表结构速记

| 表 | 作用 | 关键字段 |
|---|---|---|
| `materials` | 物料主数据 | `id`, `code` UNIQUE, `name`, `is_temporary` |
| `material_suppliers` | 物料-供应商关联 | `material_id`, `supplier_id`, `role` ∈ {primary/secondary/backup}, `last_price` |
| `price_history` | 报价历史 | `material_id`, `supplier_id`, `unit_price`, `quotation_date` |
| `bom_items` | 子项目 BOM 明细 | `subproject_id`, `material_id`, `qty`, `unit_cost` |
| `bom_substitutes` | BOM 级替代关系 | `subproject_id`, `bom_item_id`, `substitute_material_id` |
| `pending_bom_substitutes` | 替代表总表导入暂存池 | `bom_product_code`, `material_code`, `substitute_material_code` |

**注意**:`materials` 表没有 `current_price` 字段 —— "当前价"来源是 `material_suppliers.last_price` 或 `price_history` 最新一条,这是用户说"直接抓材料库价格"的根本依据。

## 诊断工作流

### 1. 读代码,不要猜

```bash
# 用 ripgrep 找关键模式,不要全文件读
search_files pattern="<关键字>" path="E:/cl_system"
```

- 报告中心问题 → `core/report.py`
- 报价单问题 → `core/quotation_parser.py`
- 项目/子项目/物料 → `app.py` 找对应路由
- 数据库表结构 → `core/db.py` 找 `CREATE TABLE`

### 2. 看实际数据库状态

```python
import sqlite3
conn = sqlite3.connect('E:/cl_system/cost_center.db', timeout=30)
conn.row_factory = sqlite3.Row

# 看每个核心表的统计
for tbl in ['materials','material_suppliers','bom_substitutes','bom_items',
            'subprojects','projects','price_history','pending_bom_substitutes']:
    cnt = conn.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]
    print(f'  {tbl}: {cnt}')
```

**关键洞察**: 用户报告 bug 时,DB 状态可能不是他报告时的状态:
- `materials=0` + `pending_bom_substitutes>0` = 材料库被清空,但暂存池还在
- `bom_substitutes=0` = 替代关系没从 pending 落位
- 数据缺失 ≠ bug,但**可能解释为什么用户看到 0**

### 3. 看最近生成的报告

```bash
ls -la E:/cl_system/reports/ | grep "2026" | tail -5
# 找零价行
grep "0.0000" E:/cl_system/reports/report_*.html
```

旧报告 HTML 直接反映当时的 bug 现象(替代料价格、合计金额等)。

## 新增功能(不只是修 bug)

用户的补丁请求不一定都是 bug 修复,经常是"在 X 模块增加 Y 功能"。常见形态:
- "报告中心增加导出 Excel 文件" → 加 `?format=excel` 出口
- "询价单增加打印" → 加 `?format=pdf` 出口
- "在物料列表加批量删除" → 加一个 POST 路由

**与 bug 修复的差异**:
- bug 修复 → 通常是 SQL/函数内的精简,改动小、影响局部
- 功能新增 → 通常引入 1 个新文件 + 1 处路由分支 + 1 个按钮 + 1 个 JS 函数
  4 处联动,任何一处漏了都会让用户看到"按钮点了没反应"

**功能新增的最小 4 步**(以 v4.0.0 Excel 导出为例):

1. **新文件**: `core/<feature>_excel.py` 或 `core/<feature>.py`
   - 输入:复用现有数据(如 `get_project_report_data` 的 data dict)
   - 输出:字节流 / 文件 / 字典
   - **不要碰数据库**, 只读
2. **app.py 路由**: 在已有路由的 mode 处理块**之后、html 渲染之前**,加 18 行 `?format=` 出口分叉
   - 关键:HTML 路径**一行都不要动**,验证时要回放 HTML 路径确认仍 200
3. **templates/index.html**: 在合适位置加一个按钮(`onclick="xxx()"`),按钮放在用户能想到的地方
4. **static/js/app.js**: 加导出函数,用 `fetch` + `blob` + `<a download>` 模式(见下文)

**新输出格式路由分叉模板**(`?format=excel/pdf/word`):

```python
# 在已有路由函数的 mode 处理块(html 渲染)之前,加:
fmt = request.args.get('format', 'html')
if fmt == 'excel':
    import traceback
    try:
        xlsx_bytes = excel_mod.render_xxx_excel(data)
        return send_file(
            io.BytesIO(xlsx_bytes),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'xxx_{pid}_{mode}_{dt.datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx',
        )
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': f'导出 Excel 失败: {e}'}), 500

# 下面是原有的 html 路径,一行不动
```

**前端 fetch+blob 下载触发文件**(cl_system 必须的模式,因为登录拦截):

```javascript
function exportXxx() {
  const pid = document.getElementById('xxx-pid').value;
  if (!pid) return showToast('请先选择', 'error');
  const params = new URLSearchParams();
  params.set('format', 'excel');
  // ... 带上其他筛选条件
  const url = `/api/xxx/${pid}?` + params.toString();
  fetch(url, { credentials: 'same-origin' })   // 必须带 same-origin,否则 401
    .then(async (resp) => {
      if (!resp.ok) {
        let msg = `HTTP ${resp.status}`;
        try { const j = await resp.json(); if (j && j.error) msg = j.error; } catch (_) {}
        throw new Error(msg);
      }
      return resp.blob();
    })
    .then((blob) => {
      const dlUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = dlUrl;
      a.download = `xxx_${pid}_${Date.now()}.xlsx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(dlUrl);
      showToast('已开始下载', 'success');
    })
    .catch((e) => showToast('导出失败: ' + e.message, 'error'));
}
```

**为什么不用 `<a download href="?format=excel">`?**
- 错误响应(401/500)会被浏览器当成 .xlsx 存下来,文件名乱、用户困惑
- 没法在 JS 里给 toast 提示
- fetch+blob 模式可以走 `resp.json()` 拿后端 error 信息弹出来

## 修复原则 —— 用户强偏好:简化!

**用户口头禅**:"弄复杂了"、"不用这么麻烦"、"直接 X 不就可以了"、"瞎折腾"
**记忆关联**: 用户偏好极简,任何超出最小可用方案的实现都是 scope creep。

### 核心原则

1. **每条回退分支都要问"这条能走到吗?"**
   - v3.0/v3.1 的"三级回退"第一条 `bom_items.unit_cost` 是死路
     —— 替代料如果同时在 BOM 里就不叫替代了
     —— 永远匹配不上,等于白做,只增加 SQL 复杂度
   - 修复后:去掉死路,只留 `material_suppliers → price_history` 两级

2. **材料库价格 = `material_suppliers.last_price` + `price_history`**
   - 不要绕道 `bom_items` 查"当前价"
   - `bom_items.unit_cost` 是 BOM 行级别,不是物料级别

3. **SQL 越短越好**
   - 能用 `COALESCE(a, b, 0)` 就不要写多层 `CASE WHEN`
   - 能用 `LEFT JOIN` + `COALESCE` 就不要子查询套子查询

4. **scope 严格守边界**
   - 用户说"我只要 X" → 交付最小可用的 X,不要顺手加管理 UI / 配置抽象 / 测试端点

## 种子数据验证 (必做!)

**不要**:只测 SQL,或者跑 `print("OK")`
**要**:用真实函数调用 + 已知输入输出对验证

```python
import sqlite3, shutil, os, sys

BACKUP = os.path.expanduser('~/AppData/Local/Temp/cost_center.db.backup')
shutil.copy('cost_center.db', BACKUP)

conn = sqlite3.connect('cost_center.db', timeout=30)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 1. 插种子数据(主料 + 替代料 + 供应商 + 物料-供应商关联 + 价格)
cur.execute("INSERT OR IGNORE INTO suppliers(id, name, is_temporary) VALUES (8, 'A', 0)")
cur.execute("INSERT OR IGNORE INTO materials(id, code, name, is_temporary) VALUES (15054, 'TEST_M1', '主料一', 0)")
cur.execute("INSERT OR IGNORE INTO materials(id, code, name, is_temporary) VALUES (15055, 'TEST_M2', '替代料二', 0)")
cur.execute("INSERT OR IGNORE INTO material_suppliers(id, material_id, supplier_id, role, last_price) VALUES (12, 15055, 9, 'secondary', 3.0)")
# ... (project / subproject / bom_items / bom_substitutes)

conn.commit()

# 2. 调用真实函数,不是手测 SQL
sys.path.insert(0, '.')
from core import report as rep
data = rep.get_project_report_data(project_id)
for s in data['sub_reports']:
    for it in s['items']:
        for a in it.get('alt_candidates', []):
            print(f"  {a['alt_code']} = {a['alt_unit_cost']} ({a['alt_price_source']})")

# 3. 验证完清理并恢复
conn2 = sqlite3.connect('cost_center.db', timeout=30)
cur2 = conn2.cursor()
for tbl, where in [...]:
    cur2.execute(f'DELETE FROM {tbl} WHERE {where}')
conn2.commit()
shutil.copy(BACKUP, 'cost_center.db')
```

**断言模式**:
- 期望替代料价格 = X(具体数字)
- 期望 `alt_price_source` = 'material_suppliers' / 'price_history' / 'none'
- 期望 `grand_total` = min/max 计算正确
- 报告 HTML 里 `<span class="text-end">{alt_p:.4f}</span>` 显示正确

**必覆盖场景:回退链最末级也要命中一次**

只测"主路径有数据"会漏掉"数据源全空"的兜底逻辑 bug。**v4.0.0 E2E 验证栽过的坑**:种子数据插了 `material_suppliers` 也有 `price_history`,SQL 永远在第 1 级命中,根本测不到"3 级 vs 2 级"的差异。v3.2 漏回退 `materials.max_price` 的 bug 因此一直没暴露,直到用户在 v4.0.0 之后报"min/max/all 模式替代料价格不对"。

```python
# 必加一组:把所有数据源都清空,让 SQL 走最末级兜底
conn.execute("DELETE FROM material_suppliers WHERE material_id IN (替代料 ids)")
conn.execute("DELETE FROM price_history WHERE material_id IN (替代料 ids)")
# 再跑一次 get_project_report_data,看 alt_unit_cost 是不是从 materials.max_price 拉到
# 验证完恢复 material_suppliers + price_history 数据,最后用 shutil.copy(BACKUP) 恢复 DB
```

**断言模式(必加项)**:
- 至少 1 条数据断言 alt_price_source='none' → alt_unit_cost=0
- 至少 1 条数据断言 alt_price_source='materials' → alt_unit_cost=materials.max_price(走兜底)
- 至少 1 条数据断言 alt_price_source='material_suppliers' → alt_unit_cost=material_suppliers.last_price
- 至少 1 条数据断言 alt_price_source='price_history' → alt_unit_price=price_history.unit_price

如果实际跑出来的 alt_price_source 集合**少于预期** → SQL 漏了回退级,看 `references/duplicate-sql-drift.md`。

## 打包补丁 (vX.Y.Z 规范)

每次补丁的产物:`patch_vX.Y.Z_<简短描述>.zip`(项目根)

**用户偏好:多个小改动合成一个 zip,不要每个改动出一个 zip**

如果一次会话里连续出了多个改动(比如"加 Excel 导出" + "修 max_price bug" + "子项目合计改三列"),**不要分别出 v4.0.0 / v4.0.1 / v4.0.2 三个 zip**。直接把 live 代码重新打包成**一个 v4.0.0 合并版**,INSTALL.txt 里说明包含 3 个改动。

**理由**:用户原话"合成一个"——他不想每次解压 3 个 zip 覆盖。后续再有改动,继续往 v4.0.0 合并版里塞,**不**递增版本号到 v4.0.2 / v4.0.3。

**只在以下情况才递增版本号**:
- 旧 patch 已经被用户解压到生产环境(已经在跑 v4.0.0)
- 这次改动跟旧 v4.0.0 互不兼容
- 必须区分"已部署的"和"未部署的"

否则一直接同一个版本号,INSTALL.txt 用"第 N 次重打"区分。

**zip 内容**(典型 3-5 个文件,按需增减):
```
app.py                      # 仅当 app.py 有改动
core/<被改的文件>           # 仅当对应文件有改动
core/<新模块>.py            # 仅当新增模块(如 report_excel.py)
templates/index.html        # 仅当加按钮
static/js/app.js            # 仅当加函数
INSTALL.txt                 # 安装说明
```

**功能新增类补丁**(如 v4.0.0)通常含 4-5 个文件:app.py + 新模块 + templates + js + INSTALL。

**INSTALL.txt 模板**:

```
翔泰成本中心 - 补丁 vX.Y.Z
==========================

【本补丁修复内容】
<一句话描述 bug>

【补丁文件清单】(覆盖到 E:\\cl_system\\ 根目录)
  app.py                -> E:\\cl_system\\app.py
  core/report.py        -> E:\\cl_system\\core\\report.py

【安装步骤】
1. 关闭正在运行的 start.py(若有)
2. 把 zip 解压到 E:\\cl_system\\ 根目录(直接覆盖)
3. 双击 E:\\cl_system\\start.bat 重新启动

【修复方案】
<技术细节:为什么这样改、改了哪段 SQL/函数、回退链简化前后对比>

【兼容性】
- 完全兼容 <上一个版本的补丁>
- 不影响 <其他模块:PDF/Excel 解析、报价单 apply 等>

【回滚】
用备份文件覆盖回去即可: app.py / core/report.py

【验证情况】
种子数据:  ...
修复结果:  ...

【补丁日期】YYYY-MM-DD
【补丁版本】X.Y.Z
【前置补丁】v1.0.0 + v2.0.0(可叠加)
【替代补丁】v3.0.0 / v3.1.0(本补丁为升级版)
```

**打包命令**(一次性脚本,见下):

```python
import os, zipfile
BASE = r'E:\cl_system'
OUT = os.path.join(BASE, 'patch_vX.Y.Z_<desc>.zip')

INSTALL_TXT = '''<见上方模板>'''

with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as z:
    for relpath in ['app.py', 'core/report.py']:  # 按需
        full = os.path.join(BASE, relpath)
        if os.path.exists(full):
            z.write(full, arcname=relpath)
    z.writestr('INSTALL.txt', INSTALL_TXT)
print(f'已生成: {OUT}')
```

打补丁脚本用完即删(`rm _build_v32.py`),不留项目污染。

## 用户错怪补丁:其实是数据问题(反向证明工作流)

**触发**:用户在新补丁打上后说"原来可以正常 X,现在不行了,是不是你改坏了?" — 比如本次 v4.0.0 打完,用户说"替代料的价格没有正确拉取,你给我改了?"

**正确反应**:不立刻相信也不立刻反驳,**用证据链证明责任归属**。

### 1. 字节级 diff — 证明补丁没动相关代码

```bash
# 取出 v_old 和 v_new zip,只对相关函数/常量做 diff
mkdir -p /tmp/diff_check && cd /tmp/diff_check
unzip -q "E:/cl_system/patch_v3.2.0_xxx.zip" -d v32
unzip -q "E:/cl_system/patch_v4.0.0_xxx.zip" -d v40
# 用 Python 提取 + diff(避开行号错位,直接对内容做字节级比较)
python3 -c "
import re
def extract(path, pattern, end_pattern):
    src = open(path, encoding='utf-8').read()
    m = re.search(pattern, src, re.M)
    start = m.start()
    em = re.search(end_pattern, src[start+10:], re.M)
    return src[start:start+10+em.end()]
v32 = extract('v32/app.py', r'^_SUBSTITUTE_PRICE_SQL', r\"^'''\n\")
v40 = extract('v40/app.py', r'^_SUBSTITUTE_PRICE_SQL', r\"^'''\n\")
print('完全一致:', v32 == v40, '| 字节:', len(v32), 'vs', len(v40))
"
```

**关键点**:
- 用 `re.search` 按模式定位起始位置,不用行号(sed -n 范围会因前面改动错位)
- 比较字节级一致性,不要 `diff` 输出 — 用户看不出 diff 哪些行是真正的 SQL
- 提取"用户怀疑的代码段"和"实际新加的代码段"分别对比

### 2. 实地 e2e — 证明当前代码仍能跑出正确结果

```python
# 用 Flask test_client + 种子数据,跑真实路由看替代料价格是不是真的没拉
from app import app
with app.test_client() as client:
    client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'})
    for mode in ['main', 'min', 'max', 'all']:
        r = client.get(f'/api/reports/project/99001/substitutes?mode={mode}')
        j = r.get_json()
        # 注意:路由只返回 grand_total,要看 items 需直接调 report_mod.get_project_report_data
        print(f'  {mode}: grand_total={j[\"grand_total\"]}')
        # 也直接调函数看 alternatives 列表
        from core import report as rep
        data = rep.get_project_report_data(99001)
        for sub in data['sub_reports']:
            for it in sub['items']:
                for a in it.get('alt_candidates') or []:
                    print(f'    替代 {a[\"alt_code\"]} 价格={a[\"alt_unit_cost\"]} source={a[\"alt_price_source\"]}')
```

如果 e2e 跑出正确价格 → 补丁无问题 → 进入第 3 步查数据

### 3. 查 live DB 实际状态 — 90% 是数据问题

```python
import sqlite3
conn = sqlite3.connect('E:/cl_system/cost_center.db', timeout=30)
for tbl in ['projects','subprojects','bom_items','materials','bom_substitutes',
            'material_suppliers','price_history','project_subprojects']:
    n = conn.execute(f'SELECT COUNT(*) FROM \"{tbl}\"').fetchone()[0]
    flag = '❌' if n == 0 else '✅'
    print(f'  {flag} {tbl}: {n}')
```

**关键发现模式**(本次 v4.0.0 验证过):
- subprojects=0 → 报告里没子项目块
- bom_items=0 → 报告里没 BOM 行
- bom_substitutes=0 → 报告里没替代料数据(SQL 跑不出结果)
- materials=0 + material_suppliers 引用不存在的 material_id → FK 悬空,价格永远 0

### 4. 给用户的回复模板

```
[v4.0.0 diff]    _SUBSTITUTE_PRICE_SQL 字节级完全相同
[v4.0.0 e2e]    4 种 mode 跑出来替代料价格都正确(具体数字)
[live DB 实际]   subprojects=0, bom_items=0, bom_substitutes=0 ← 没数据可拉
[结论]          v4.0.0 没改坏,问题在数据空
```

不主动恢复数据(用户偏好"反对瞎折腾"),让用户自己决定要不要从备份恢复。

## 常见陷阱 (踩过的坑)

### 7月12日 `before_clear.bak` 是 v1 时代备份 — 没有 bom_substitutes 表

**坑**:用户 DB 看起来很空,你想从 `cost_center.db.before_clear.bak`(7月12日 21:36)恢复。但这个备份是 **v2.0 之前** 的,SQLite schema 里**根本没有 `bom_substitutes` 表**。

恢复它能拿回:projects(3) / subprojects(5) / bom_items(55) / materials(46)。
但**拿不回任何替代关系** — 那个时代替代关系是写在 `bom_items.remark` 里的 `"[ECN:xxx]替代"` 文字标记,没结构化。

**结论**:要从这个备份恢复数据 + 跑 v3.2 报告看到替代料价,需要在 UI 里手动建替代关系(走 `/api/substitutes/pending/assign` 或 `/api/substitutes` POST)。

**怎么判断一个备份是什么时代**:
```python
import sqlite3
c = sqlite3.connect('E:/cl_system/<backup_file>', timeout=10)
tabs = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print('bom_substitutes 存在:', 'bom_substitutes' in tabs)   # False=v1 时代
print('material_substitutes 存在:', 'material_substitutes' in tabs)
print('quotation_audit 存在:', 'quotation_audit' in tabs)   # v2 之后
```

**7月12日之后任何含 `cost_center_backup_20260721_*.db` 的备份** — 都有 schema 升级,但 `projects/subprojects/bom_items` 全是 0,只有 `materials` 暴增(报价单导入的临时物料),这些备份也救不了项目/子项目/BOM/替代关系数据。

### bom_substitutes 是空的?

如果用户报告"报告里看不到替代料",先看 `bom_substitutes` 表有没有数据。
- 有数据 → SQL bug
- 无数据 → 用户没从 `pending_bom_substitutes` 落位,需要走 `/api/substitutes/pending/assign`
- 也可能材料库(`materials` 表)被清空了,FK 引用悬空,落位会失败

### 跨文件重复 SQL 漂移(cl_system 实证,2026-07-28)

**坑**:同一个业务逻辑在两个文件里实现,改一份没改另一份,导致模式之间行为不一致。

**cl_system 真实案例**:`_SUBSTITUTE_PRICE_SQL`(在 `app.py`,min/max/all 模式用)和 `alt_sql`(在 `core/report.py` 里,main 模式报告 HTML/Excel 渲染时填 `alt_candidates` 字段)是**两段几乎一样的 SQL**。v3.2 简化时只改了 `app.py` 的 `_SUBSTITUTE_PRICE_SQL`(去掉 bom_items 死路),`core/report.py` 的 `alt_sql` 没动。结果:`core/report.py` 有 3 级回退 `COALESCE(ms_agg.last_price, ph.unit_price, m.max_price, 0)`,`app.py` 只有 2 级,漏了 `materials.max_price`。

**触发条件**:任何时候"补丁修改了某段 SQL"——必须 grep 全项目找 SQL 副本,确认副本行为一致。

**怎么发现**:用户反馈"X 模式不对,Y 模式对"(同数据,不同模式结果不同)。grep 关键字模式(回退链、典型 JOIN),通常能发现 2 段。

**怎么防御**:
- 改 SQL 前用 `search_files pattern="COALESCE\(ms_agg\.last_price" path="E:/cl_system"` 找副本
- 改完后跑 e2e,断言多个 alt_price_source 取值(见上面"必覆盖场景")
- 详细案例 + 修复 SQL 见 `references/duplicate-sql-drift.md`

### diff 防御 ≠ 实际验证(2026-07-28 教训)

**坑**:用户说"原来可以,现在不行,是不是你改坏了?"。如果立刻回答"diff 看了,我没动这块代码"——

- ❌ **错**:diff 只证明"我没破坏",**不证明"它现在能用"**。v3.2 的漏回退 bug 一直存在,虽然你 v4.0 没动这块代码,bug 依然在
- ✅ **对**:diff(证明我没破坏) + 实际跑函数 e2e(证明它真的工作) + 如果 e2e 也不对,继续查是 pre-existing bug 还是数据问题

用户**最讨厌的是敷衍**——只说"我没动"但没真去验证。**正确回应是直白的诊断 + 现场验证 + 可执行的修复补丁**,而不是"理论上应该没问题"。

### 价格显示 0.0000 的两种原因

1. **数据缺失**: 该物料在 `material_suppliers` 和 `price_history` 里都没记录
2. **SQL bug**: 查询逻辑走不通
   - **区分方法**:看 `alt_price_source` 字段 = 'none' = 数据缺失,= 'material_suppliers' 或 'price_history' = SQL bug

### 不要忘了外键

`materials` 表里没有 `substitute_material_id` 字段 —— 替代关系在 `bom_substitutes` 或 `material_substitutes` 表里。**别写错 JOIN**。

### 数据库操作前先备份

```bash
cp E:/cl_system/cost_center.db E:/cl_system/cost_center.db.before_<日期>_<原因>.bak
```

测试数据清理要彻底,避免污染用户的实际数据库。验证完用 `shutil.copy(BACKUP, 'cost_center.db')` 恢复。

### patch 缩进陷阱

用 `patch` 工具替换 Python 代码时,`old_string` 和 `new_string` 的缩进必须**逐字符匹配**。如果 old 缩进是 8 空格,new 必须也是 8 空格;否则整块 if/for 缩进会乱。

**逃生**:patch 报 IndentationError 时,用 `python` 脚本批量调整缩进(每个错误行 `lines[i] = lines[i][4:]`),然后 `python -c "import ast; ast.parse(open('core/report.py').read())"` 验证。

### 平行 SQL 漂移:同一概念两个 SQL,改一个忘另一个

**案例**:`_SUBSTITUTE_PRICE_SQL`(在 `app.py` 里,min/max/all 模式用) 和 `alt_sql`(在 `core/report.py` 里,main 模式报告渲染用) 是**两个不同地方、不同字符串**的 SQL,但查的是**同一个东西**:替代料的价格回退链。

- 历史上 v3.2 简化回退时,只改了 `app.py` 的 `_SUBSTITUTE_PRICE_SQL`(删 bom_items 第一级死路),**顺带把 `materials.max_price` 这一级也删了**。但 `core/report.py` 的 `alt_sql` 一直保留 max_price 三级回退。
- 结果:main 模式报告能拉到替代料价格(`alt_sql` 兜底 max_price),min/max/all 模式显示 0(`_SUBSTITUTE_PRICE_SQL` 漏了 max_price)。
- 用户报"替代料价格没正确拉取"时,只盯着一处 SQL 查不到根因,必须**两处都查**。

**经验**:
- cl_system 里**任何"算替代料价格"的代码路径都要同步改**,搜索关键字 `material_suppliers`, `price_history`, `bom_alt_cost` / `alt_unit_cost`, `alt_price_source` 来定位。
- 修复时,把两处 SQL 拉出来 diff,确保回退链一致。
- 写补丁时,在 INSTALL.txt 里**点名两处都改了**,避免下次又只改一处。

### 用户说"原来可以,现在不行"——别急着甩锅数据

**真实场景**:用户说"现在替代料价格没正确拉取,原来是可以的"。如果第一反应是"你的数据是空的"——很可能是错的。

**正确诊断流程**:
1. **diff 证明**:字节级 diff v3.2 → v4.0 的替代料 SQL,证明补丁没动这部分。
2. **e2e 跑一次**:用种子数据跑 4 种 mode,证明 SQL 端能拉到价。
3. **查 live DB 实际状态**:如果 e2e 跑通但 live 数据空,**才**告诉用户"问题在数据不在 SQL"。
4. **如果第 3 步查到 live DB 又有数据**,但 SQL 拉不到——**继续深挖**,别停。这一步最容易漏,因为你的脚本会复用"上一步的种子验证",会以为 SQL 端是对的。

**本会话实际经过**:
- 第一次查 live DB:bom_items=0, subprojects=0 → 我下了"数据空"结论
- 用户说"看下现在呢"——他中间导了一批数据(subprojects=7, bom_items=278, materials=6873)
- 重新查 live:material_suppliers=0, price_history=0,**materials.max_price 有数据**
- 此时**必须意识到**:有数据但 SQL 拉不到 → 是 SQL bug,不是数据 bug
- grep `max_price` 才发现 `core/report.py::alt_sql` 有 max_price 而 `app.py::_SUBSTITUTE_PRICE_SQL` 没有

**经验**:用户报"原来可以"时,**别用一次 DB 状态判断就甩锅**——数据可能在他中间改了。**重查 + 跨模式对比**才能定位是 SQL bug 还是数据 bug。

## 历史补丁清单 (供参考)

| 版本 | 内容 | 类型 |
|---|---|---|
| v1.0.0 | 报价单解析补丁(PDF/Excel/Word) | bug 修复 |
| v2.0.0 | 替代料列表 + 全价格报告 + pending 落位 | bug 修复 |
| v3.0.0 | 报告中心替代料价格三级回退(引入 bom_items 第一级回退,**死路**) | bug 修复 |
| v3.1.0 | 三级回退放宽 role 限制(max(last_price) 覆盖所有 role) | bug 修复 |
| v3.2.0 | 简化:去掉 bom_items 第一级回退,直接抓材料库价格 |
| **v4.0.0** | **合并版:报告中心增加 Excel 导出(`?format=excel` + 按钮 + JS) + 修复 min/max/all 模式不回退 materials.max_price + all 模式子项目合计行三列分别合计(主/低/高)** |
| **v4.0.0** | **报告中心增加 Excel 导出** (`?format=excel` 出口) | **功能新增** |
| **v4.0.1** | **修复 v3.2 漏回退 `materials.max_price`** (min/max/all 模式替代料价格在 material_suppliers/price_history 都空时显示 0) | bug 修复(回退 v3.2 漂移) |

每次出补丁前 `ls patch_v*.zip` 看一眼,版本号往上走,不要回头。

## 参考资料

- `references/sql-fallback-pitfall.md` —— 多级回退 SQL 的"死路分支"识别模式(v3.0/v3.1 → v3.2 简化的核心教训)。这个模式普遍适用,任何设计 fallback chain 的场景都该读。
- `references/patch-v4.0.0-changelog.md` —— v4.0.0 合并版补丁的完整变更日志(Excel 导出 + max_price 回退 + all 模式三列合计),含平行 SQL 漂移教训和后续注意点。

## 与其他 skill 的关系
- `templates/_build_patch.py` —— 复用 v3.2 / v4.0.0 验证过的 zip 打包脚本(填 `files` 列表 + `INSTALL_TXT` 一键出包)。

## 与其他 skill 的关系

- 不涉及视觉/UI → 不需要 `word-doc-visual-design` / `ui-ux-pro-max`
- 不涉及 Cloudflare 部署 → 不需要 `cloudflare-deployment`
- 纯 SQL/Python 修复 → 不需要 `test-driven-development`(但要种子数据验证)
- 涉及多步骤规划 → 可参考 `software-development/writing-plans`
- **新增功能类补丁**(如 v4.0.0)需要 4 文件联动 → 走本文档"新增功能"工作流,不是 bug 修复工作流
- **新做 Excel 导出** → 必读 `references/excel-export-style.md`,复用 `core/rfq_excel.py` / `core/report_excel.py` 的视觉骨架