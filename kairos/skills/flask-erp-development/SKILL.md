---
name: "flask-erp-development"
description: ">-"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\software-development\\flask-erp-development\\SKILL.md"
---
# Flask ERP Development Skill

Build production ERP/business management systems with Flask backend + vanilla JS SPA frontend + SQLite database.

## Architecture

```
project/
├── app.py              # Flask app factory + ALL API routes
├── models.py           # SQLAlchemy models (all tables)
├── config.py           # Config class (SECRET_KEY, DB URI)
├── init_db.py          # DB creation + seed data
├── requirements.txt    # flask, flask-cors, flask-login, flask-sqlalchemy
├── data/
...
├── templates/
│   ├── login.html      # Standalone login page
│   └── dashboard.html  # SPA shell (sidebar + page sections)
└── static/js/
    └── app.js          # SPA frontend (all module renderers)
```

## Key Patterns

### SQLAlchemy Type Pitfalls
**Reserved attribute names**: SQLAlchemy's Declarative API reserves `metadata` as an attribute name (used by the MetaData registry). Using it causes `InvalidRequestError: Attribute name 'metadata' is reserved`. Fix: use a different Python attribute name, map to the real column name:
```python
# WRONG: metadata = db.Column(db.Text)  → raises InvalidRequestError
# RIGHT:
meta = db.Column('metadata', db.Text)  # column name in DB is 'metadata', Python attr is 'meta'

def to_dict(self):
    return { ..., 'metadata': self.meta, ... }  # expose as 'metadata' in API
```
Same applies to any model that needs a `metadata` column. This affects `Message`, `Event`, and similar models. The `db.Column('metadata', ...)` first argument is the actual DB column name; the Python attribute (`meta`) is what you use in code.

**Decimal**: NEVER use `db.Decimal` — it doesn't exist. Use `db.Numeric`:
```python
# WRONG: db.Column(db.Decimal(15, 4))
# RIGHT:
price = db.Column(db.Numeric(15, 4), default=0)
```

**Date columns + SQLite**: SQLite Date columns do NOT accept raw date strings from JSON. Passing `"2026-06-01"` directly causes a bind-parameter error. You MUST parse to a Python `date` object first:
```python
def clean_dates(data, *fields):
    """空字符串/None转None，日期字符串转date对象"""
    for k in fields:
        v = data.get(k)
        if v == '' or v is None:
            data[k] = None
        elif isinstance(v, str):
            try:
                data[k] = datetime.strptime(v, '%Y-%m-%d').date()
            except ValueError:
                data[k] = None
```
Call `clean_dates(data, 'date_field1', 'date_field2')` on ALL request JSON that contains date fields, BEFORE passing `data` to the model constructor. This fixes the common "save fails only when user picks a date" bug — empty dates get converted to `None` by the form and skip the column binding, so the bug is invisible until a real date is entered.

### SPA Frontend Pattern
Single HTML with sidebar nav + hidden page sections. JS `pageLoaders` object maps page names to async render functions:
```javascript
const pageLoaders = {};
pageLoaders.products = async () => { /* fetch + render */ };
function showPage(name) {
  // hide all .page-section, show #page-{name}, call pageLoaders[name]()
}
```

**Data dependency pitfall**: When a page uses data from another entity (e.g., Messages page needs Agents for dropdown), ALWAYS load that data at the START of the render function. Template literals evaluate `${S.agents.map(...)}` to empty string when the array is empty — no JS error, just silent failure:
```javascript
async function renderMessages(el) {
  S.messages = await api(`messages?channel=${msgChannel}`);
  if (!S.agents.length) S.agents = await api('agents');  // ← Load BEFORE rendering
  el.innerHTML = `...${S.agents.map(a => `...`).join('')}...`;
}
```
This affects BOTH custom dropdowns AND native `<select>`. With native selects, empty options are invisible (blank dropdown). With custom dropdowns, the empty menu is more visible.

### API Helper (complete)
The `api` object MUST check `r.ok` and throw on HTTP errors. Server 500s return HTML that `.json()` can't parse, causing silent failures. ALL methods need the error-parsing pattern:
```javascript
const api = {
  async get(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  },
  async post(url, data) {
    const r = await fetch(url, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) });
    if (!r.ok) {
      try { const e = await r.json(); throw new Error(e.error || `HTTP ${r.status}`); }
      catch(e) { if(e.message) throw e; throw new Error(`HTTP ${r.status}`); }
    }
    return r.json();
  },
  async put(url, data) {
    const r = await fetch(url, { method: 'PUT', headers: {'Content-Type':'application/json'}, body: JSON.stringify(data) });
    if (!r.ok) {
      try { const e = await r.json(); throw new Error(e.error || `HTTP ${r.status}`); }
      catch(e) { if(e.message) throw e; throw new Error(`HTTP ${r.status}`); }
    }
    return r.json();
  },
  async del(url) {
    const r = await fetch(url, { method: 'DELETE' });
    if (!r.ok) {
      try { const e = await r.json(); throw new Error(e.error || `HTTP ${r.status}`); }
      catch(e) { if(e.message) throw e; throw new Error(`HTTP ${r.status}`); }
    }
    return r.json();
  }
};
```
**Pitfall**: the delete method is named `del` (not `delete`) because `delete` is a JS reserved word. Always call `api.del(url)`, never `api.delete(url)`.

### Modal Reuse Pattern
Single modal element, swap content on each call:
```javascript
function showModal(title, bodyHtml, footerHtml = '') {
  let m = document.getElementById('appModal');
  if (!m) {
    document.body.insertAdjacentHTML('beforeend', `<div class="modal fade" id="appModal" tabindex="-1"><div class="modal-dialog modal-lg"><div class="modal-content"><div class="modal-header"><h5 class="modal-title"></h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body"></div><div class="modal-footer"></div></div></div></div>`);
    m = document.getElementById('appModal');
  }
  m.querySelector('.modal-title').textContent = title;
  m.querySelector('.modal-body').innerHTML = bodyHtml;
  m.querySelector('.modal-footer').innerHTML = footerHtml || '<button class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>';
  // CRITICAL: reuse existing instance, don't create new one
  let bs = bootstrap.Modal.getInstance(m);
  if (!bs) bs = new bootstrap.Modal(m);
  bs.show();
  return bs;
}
```
**Pitfall — Backdrop leak blocks entire page**: NEVER call `new bootstrap.Modal(m)` on an element that already has a modal instance. When a modal is open and you call `showModal()` again (e.g. after adding an item inline), creating a second instance causes a duplicate `modal-backdrop` div. When closing, only one backdrop is removed — the other stays, making the page unclickable (grey overlay blocks all clicks). The fix: always check `bootstrap.Modal.getInstance(m)` first and reuse it. This is the #1 cause of "screen not clickable after save" bugs in Bootstrap SPA apps.

### Auto-increment ID Patterns

**Date-based with MAX()** (SO-20260531-001):
```python
today_str = datetime.now().strftime('%Y%m%d')
# WRONG: count() causes duplicates after deletion
# last = Model.query.filter(Model.no.like(f'{PREFIX}-{today_str}-%')).count()
# RIGHT: use MAX to get the highest existing number
max_obj = Model.query.filter(
    Model.no.like(f'{PREFIX}-{today_str}-%')
).order_by(Model.no.desc()).first()
if max_obj:
    last_num = int(max_obj.no.split('-')[-1])
    no = f'{PREFIX}-{today_str}-{str(last_num+1).zfill(3)}'
else:
    no = f'{PREFIX}-{today_str}-001'
```

**Sequential** (SUP-0001, SUP-0002...):
```python
last = Model.query.filter(Model.code.like('SUP-%')).order_by(Model.code.desc()).first()
if last and last.code:
    try:
        seq = int(last.code.split('-')[1]) + 1
    except (ValueError, IndexError):
        seq = Model.query.count() + 1
else:
    seq = Model.query.count() + 1
data['code'] = f'SUP-{str(seq).zfill(4)}'
```
**Pitfall**: Don't use `last.id + 1` for the sequence number — ID gaps from deletions cause numbering holes. Parse the max existing code number instead.
**Pitfall**: Don't use `.count()` for date-based numbering — deleting the last order then creating a new one produces a duplicate number. Always use MAX-based approach.

**Frontend**: Show "自动生成" as readonly placeholder for new records. In `saveSupplier`, strip it before POST:
```javascript
if (!id && data.code === '自动生成') delete data.code;
```
When editing, keep the code field editable so user can override if needed.

### Business Partner (Supplier/Customer) Management

**Full company names**: Store the complete registered name in `name`, short alias in `short_name`:
```python
class Supplier(db.Model):
    name = db.Column(db.String(200))      # 杭州臻盈电子科技有限公司
    short_name = db.Column(db.String(50))  # 杭州臻盈 (for display in POs, lists)
```
Display `short_name` in order lists and detail views. Use `name` in formal documents (invoices, contracts).
**Edit form**: Include both `name` and `short_name` fields. List view shows both columns.

**Standardizing codes**: When existing data has mixed formats (S001, SUP-0012), batch-reassign sequentially:
```python
sups = Model.query.filter_by(is_active=True).order_by(Model.id).all()
for i, s in enumerate(sups, 1):
    s.code = f'SUP-{str(i).zfill(4)}'
db.session.commit()
```

**Detailed category selects**: Use `<optgroup>` for grouped dropdowns:
```html
<select name="category">
  <option value="">请选择</option>
  <optgroup label="电子类">
    <option value="电阻">电阻</option>
    <option value="电容">电容</option>
    <option value="IC芯片">IC芯片</option>
    ...
  </optgroup>
  <optgroup label="结构类">
    <option value="PCB">PCB</option>
    <option value="结构件">结构件</option>
    ...
  </optgroup>
</select>
```
Avoids a flat list of 20+ options with no hierarchy.

### Excel Import/Export (openpyxl)
```python
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
wb = openpyxl.Workbook()
ws = wb.active
ws.title = 'SheetName'
# Write headers with styling
for c, h in enumerate(headers, 1):
    cell = ws.cell(row=1, column=c, value=h)
    cell.font = Font(bold=True, color='FFFFFF')
    cell.fill = PatternFill(start_color='4472C4', end_color='4472C4', fill_type='solid')
```

## Reference Files

- `references/bom-unit-price.md` — BOM cost priority chain: manual > receipt > PO > standard cost
- `references/alternative-parts-dropdown.md` — Show 备用料号 in product dropdowns (bulk API pattern)
- `references/bom-material-sync.md` — Auto-create Product when adding BOM component (dual-mode form)
- `references/stock-management.md` — Stock API BOM integration, manual adjustment, inline editing
- `references/pitfalls-and-patterns.md` — JS template literal corruption, dist rebuild, Flask startup
- `references/quick-add-component.md` — Quick-add new materials inline from BOM page
- `references/report-ratio-pitfalls.md` — Financial ratio calculation: avoid cross-period denominator mismatch
- `references/sqlite-schema-migration.md` — ALTER TABLE + backfill
- `references/contract-pdf-generation.md` — PDF gen, company config, print templates
- `references/pdf-generation-and-downloads.md` — fpdf2 Chinese PDF generation, pywebview download patterns

## Common Module Patterns

### Sales Order with Lines
- Parent table (sales_orders) + child table (sales_order_lines)
- Create: pop `lines` from request data, create parent, flush, create children, compute total, commit
- Detail: return parent dict with `d['lines'] = [to_dict(l) for l in order.lines]`

### BOM (Bill of Materials)
- Links parent product to component products with quantity + scrap_rate
- MRP: iterate SO lines → expand BOM → check stock → calculate shortfall

**Quick-add materials inline**: When adding BOM sub-items, the component dropdown should include a "新增物料" button next to it, so users can create new component materials without leaving the BOM page. Use Bootstrap `input-group` to place a `btn-outline-warning` with `bi-plus-circle` icon next to the `<select>`:

```html
<div class="input-group">
  <select class="form-select" id="bomComp2">${compOpts}</select>
  <button class="btn btn-outline-warning" onclick="quickAddComp()" title="新增物料"><i class="bi bi-plus-circle"></i></button>
</div>
```

The `quickAddComp()` function opens a modal with a minimal product creation form (SKU, name, category, type, unit, spec). Default category is RM (原材料), default type is component. After creation, close the modal and call `bomSelectChange()` to refresh the dropdown — the new material appears immediately. No need to navigate to 产品管理 first.

### Stock Management
- `stock_current` table: one row per (product, location), stores on_hand_qty
- `stock_moves` table: audit trail of every movement
- On move: update stock_current for both from/to locations

### Production Order Workflow
```
released → in_progress → completed
  ↑
  └── recall (撤回) → back to released
```
- **released**: created directly (no draft step), deletable, can be recalled (撤回) to restore inventory
- **in_progress**: can be completed with completed_qty + scrap_qty
- Auto-fill BOM lines when creating from finished product
- On create: immediately deduct inventory based on BOM (not on release)
- On complete: record completed_qty and scrap_qty
- **No "下达" button needed**: Orders are created as 'released' status directly. The create endpoint handles everything including inventory deduction.

**Create-with-deduction pattern**: Production orders deduct inventory at creation time, not at release. The create endpoint handles everything:
```python
@app.route('/api/production-orders', methods=['POST'])
def api_create_production_order():
    data = request.json
    location_id = data.pop('location_id', None)  # user selects warehouse location
    # ... create order with status='released' ...
    
    # Get warehouse location
    loc = Location.query.get(location_id) if location_id else Location.query.filter_by(code='SY').first()
    
    # Deduct inventory for each BOM component
    for bom in BOM.query.filter_by(parent_product_id=po.product_id).all():
        required_qty = float(bom.quantity) * order_qty * (1 + float(bom.scrap_rate or 0) / 100)
        sc = StockCurrent.query.filter_by(product_id=bom.component_product_id, location_id=loc.id).first()
        if sc:
            sc.on_hand_qty = float(sc.on_hand_qty or 0) - required_qty
        else:
            sc = StockCurrent(product_id=bom.component_product_id, location_id=loc.id, on_hand_qty=-required_qty)
            db.session.add(sc)
        # Record stock move for audit trail
        db.session.add(StockMove(..., move_type='production_out', reference_id=po.id))
```

**Negative inventory**: Allow negative stock values — do NOT block creation when stock is insufficient. Display negative values with red bold styling:
```javascript
// In stock list and order detail views
const isNegative = s.on_hand_qty < 0;
const qtyStyle = isNegative ? 'color:red;font-weight:bold' : '';
return `<td style="${qtyStyle}">${s.on_hand_qty}</td>`;
```

**Location selection**: Production order form includes warehouse location dropdown:
```javascript
const [prods, locs] = await Promise.all([api.get('/api/products'), api.get('/api/locations')]);
const locOpts = (locs || []).map(l => `<option value="${l.id}">${l.code} - ${l.name}</option>`).join('');
// Include in form: <select name="location_id">${locOpts}</select>
// Pass to API: data.location_id = Number(f.querySelector('[name="location_id"]').value);
```

**Delete restores inventory**: Always reverse all `production_out` stock moves when deleting, regardless of status:
```python
moves = StockMove.query.filter_by(reference_type='production_order', reference_id=po.id, move_type='production_out').all()
for move in moves:
    sc = StockCurrent.query.filter_by(product_id=move.product_id, location_id=move.from_location_id).first()
    if sc:
        sc.on_hand_qty = float(sc.on_hand_qty or 0) + float(move.quantity)
    db.session.delete(move)
```

**Recall restores inventory**: Same reversal logic as delete, plus reset issued_qty to 0 on all lines.

**Online stock (线上库存) as computed field**: Sum of `quantity` from all released production orders for a product. This is a read-only computed field — use the 5-layer protection pattern below.

### Product Receipt (入库) Pattern
Add a "入库" button to the product list for manual inventory receipt:
```javascript
// In loadProducts table row:
<button class="btn btn-sm btn-outline-success" onclick="showProductReceiptForm(${p.id},'${p.sku}','${p.name}')">入库</button>

// Form with location selection:
async function showProductReceiptForm(productId, sku, name) {
  const locs = await api.get('/api/locations');
  const locOpts = (locs || []).map(l => `<option value="${l.id}">${l.code} - ${l.name}</option>`).join('');
  const body = `<form id="receiptForm"><div class="row g-3">
    <div class="col-md-12"><label class="form-label">产品</label><input class="form-control" value="${sku} - ${name}" readonly></div>
    <div class="col-md-6"><label class="form-label">库位</label><select class="form-select" name="location_id">${locOpts}</select></div>
    <div class="col-md-6"><label class="form-label">入库数量</label><input class="form-control" name="quantity" type="number" step="0.01" value="1" required></div>
    <div class="col-md-12"><label class="form-label">备注</label><input class="form-control" name="remark" placeholder="入库原因"></div>
  </div></form>`;
  showModal(`入库 - ${sku}`, body, `<button class="btn btn-success" onclick="doProductReceipt(${productId})">确认入库</button>`);
}

async function doProductReceipt(productId) {
  const f = document.getElementById('receiptForm');
  const data = {
    product_id: productId,
    quantity: Number(f.querySelector('[name="quantity"]').value),
    move_type: 'receipt',
    to_location_id: Number(f.querySelector('[name="location_id"]').value),
    remark: f.querySelector('[name="remark"]').value || '手动入库'
  };
  if (!data.quantity || data.quantity <= 0) { toast('请输入有效数量', 'error'); return; }
  const r = await api.post('/api/stock/move', data);
  if (r.success) { toast(`入库成功，移动单号: ${r.move_no}`); bootstrap.Modal.getInstance(document.getElementById('appModal')).hide(); loadProducts(); }
  else toast(r.error || '入库失败', 'error');
}
```

### Inventory page renaming: When the ERP separates finished products (managed in 产品管理) from raw materials (managed in a separate page), rename the stock page and filter the API:
```python
# Backend: exclude finished products from stock view
query = query.filter(Product.product_type != 'finished')
```
```javascript
// Frontend: stock move form excludes finished products
const prods = await api.get('/api/products?exclude_type=finished&per_page=200');
```
Add `exclude_type` parameter to the products list API alongside the existing `product_type` filter.

**Recall endpoint** (撤回已下达工单) — with inventory restoration:
```python
@app.route('/api/production-orders/<int:oid>/recall', methods=['POST'])
def api_recall_production_order(oid):
    po = ProductionOrder.query.get_or_404(oid)
    if po.status != 'released':
        return jsonify({'error': '只能撤回已下达的工单'}), 400
    # Restore inventory (reverse all production_out moves)
    default_loc = Location.query.filter_by(code='SY', is_active=True).first()
    if default_loc:
        moves = StockMove.query.filter_by(
            reference_type='production_order', reference_id=po.id, move_type='production_out'
        ).all()
        for move in moves:
            sc = StockCurrent.query.filter_by(product_id=move.product_id, location_id=default_loc.id).first()
            if sc:
                sc.on_hand_qty = float(sc.on_hand_qty or 0) + float(move.quantity)
                sc.last_move_date = datetime.now()
            db.session.delete(move)
        for line in po.lines:
            line.issued_qty = 0
    po.status = 'draft'
    db.session.commit()
    return jsonify({'success': True})
```

### BOM-Based Inventory Deduction on Production Order Release
When releasing a production order, automatically deduct raw materials based on BOM:

```python
@app.route('/api/production-orders/<int:oid>/release', methods=['POST'])
def api_release_production_order(oid):
    po = ProductionOrder.query.get_or_404(oid)
    if po.status != 'draft':
        return jsonify({'error': '只能下达草稿状态的工单'}), 400

    order_qty = float(po.quantity or 0)
    boms = BOM.query.filter_by(parent_product_id=po.product_id).all()
    if not boms:
        return jsonify({'error': '该产品没有BOM，无法下达'}), 400

    default_loc = Location.query.filter_by(code='SY', is_active=True).first()
    today_str = datetime.now().strftime('%Y%m%d')
    move_count = StockMove.query.filter(StockMove.move_no.like(f'MV-{today_str}-%')).count()

    for bom in boms:
        component = Product.query.get(bom.component_product_id)
        if not component: continue
        # Required = BOM qty * order qty * (1 + scrap_rate%)
        required_qty = float(bom.quantity) * order_qty * (1 + float(bom.scrap_rate or 0) / 100)

        sc = StockCurrent.query.filter_by(product_id=bom.component_product_id, location_id=default_loc.id).first()
        current_stock = float(sc.on_hand_qty or 0) if sc else 0
        if current_stock < required_qty:
            return jsonify({'error': f'物料 {component.name} 库存不足：需要 {required_qty:.2f}，当前 {current_stock:.2f}'}), 400

        # Deduct
        sc.on_hand_qty = current_stock - required_qty
        sc.last_move_date = datetime.now()

        # Record stock move
        move_count += 1
        move_no = f'MV-{today_str}-{str(move_count).zfill(3)}'
        db.session.add(StockMove(
            move_no=move_no, product_id=bom.component_product_id,
            from_location_id=default_loc.id, quantity=required_qty,
            move_type='production_out', reference_type='production_order',
            reference_id=po.id, remark=f'工单 {po.order_no} 领料'
        ))

        # Update or create production order line
        line = ProductionOrderLine.query.filter_by(order_id=po.id, product_id=bom.component_product_id).first()
        if line:
            line.issued_qty = required_qty
        else:
            db.session.add(ProductionOrderLine(
                order_id=po.id, product_id=bom.component_product_id,
                required_qty=required_qty, issued_qty=required_qty, remark='BOM自动领料'
            ))

    po.status = 'released'
    db.session.commit()
    return jsonify({'success': True})
```

**Pitfall**: Always restore inventory on recall AND delete of released orders. The delete handler must check if status is 'released' and reverse all `production_out` moves before deleting the order:
```python
if po.status == 'released':
    # Same reversal logic as recall
    moves = StockMove.query.filter_by(reference_type='production_order', reference_id=po.id, move_type='production_out').all()
    for move in moves:
        sc = StockCurrent.query.filter_by(product_id=move.product_id, location_id=default_loc.id).first()
        if sc:
            sc.on_hand_qty = float(sc.on_hand_qty or 0) + float(move.quantity)
        db.session.delete(move)
```

**Pitfall**: Return error 400 (not 500) when stock is insufficient. Include the exact shortfall in the message so the user knows which material to procure.

### Warehouse Location Management
Single default location pattern — auto-create on app startup if missing:
```python
# In create_app(), after login_manager setup:
with app.app_context():
    if not Location.query.filter_by(code='SY', is_active=True).first():
        wh = Warehouse.query.first()
        if wh:
            db.session.add(Location(warehouse_id=wh.id, code='SY', name='SY库', location_type='storage'))
            db.session.commit()
```

Dynamic location creation via API:
```python
@app.route('/api/locations', methods=['POST'])
def api_create_location():
    data = request.json
    code, name = data.get('code', '').strip(), data.get('name', '').strip()
    if not code or not name:
        return jsonify({'error': '编码和名称不能为空'}), 400
    if Location.query.filter_by(code=code, is_active=True).first():
        return jsonify({'error': f'库位编码 {code} 已存在'}), 400
    wh = Warehouse.query.first()
    loc = Location(warehouse_id=wh.id, code=code, name=name, location_type='storage')
    db.session.add(loc)
    db.session.commit()
    return jsonify({'success': True, 'id': loc.id})
```

Stock page with location filter dropdown:
```python
# Backend: add location_id filter
location_id = request.args.get('location_id', '', type=str)
if location_id:
    query = query.filter(StockCurrent.location_id == int(location_id))
```
```javascript
// Frontend: location dropdown + filter
<select id="stockLocFilter" onchange="loadStock()"><option value="">全部库位</option></select>
// Populate on page load:
const locs = await api.get('/api/locations');
(locs || []).forEach(l => { /* add <option> */ });
```

**Terminology**: Rename "在手量" (on_hand_qty) to "库存" in the UI. Remove "预留量" (reserved_qty) and "可用量" (available_qty) columns — only show 库存. Stock page table header uses "规格型号" instead of "产品名称" (the `name` field on Product stores the part number/spec, not a friendly name). Excel export headers must match: ['序号', 'SKU', '规格型号', '规格参数', '备用料号', '单位', '库位', '在手库存', '可用库存'].

**Remove outbound option**: Stock page only shows 入库 (receipt) and 调拨 (transfer) buttons. No 出库 (issue) button — outbound is handled automatically by production order release.

### SQLite Schema Migration (ALTER TABLE)
SQLite has no migration framework. When adding a column to an existing table:
```python
# In a one-time migration script or app startup
with app.app_context():
    cols = [row[1] for row in db.session.execute(db.text('PRAGMA table_info(products)')).fetchall()]
    if 'new_column' not in cols:
        db.session.execute(db.text('ALTER TABLE products ADD COLUMN new_column NUMERIC(12,2) DEFAULT 0'))
        db.session.commit()
```
**Pitfall**: SQLite `ALTER TABLE ADD COLUMN` only supports DEFAULT for new rows. Existing rows get NULL, not the default. If you need to backfill:
```python
# Backfill from related tables
products = Product.query.all()
for p in products:
    value = db.session.query(db.func.sum(RelatedTable.qty)).filter_by(product_id=p.id).scalar() or 0
    p.new_column = float(value)
db.session.commit()
```
Always check column existence first — `ALTER TABLE ADD COLUMN` on an existing column raises OperationalError.

### Transitioning: Dynamic → Stored Field
When a computed field (e.g. `on_hand_qty` from StockCurrent sum) needs to become user-editable:
1. Add column to model: `on_hand_qty = db.Column(db.Numeric(12, 2), default=0)`
2. ALTER TABLE + backfill existing data from source table
3. Update list API: remove dynamic query, use stored field via `to_dict(p)`
4. Update edit form: add editable `<input>`, add to numeric forEach, remove from skip set
5. Update Excel export: use stored field

When going the other direction (stored → dynamic), apply the 5-layer computed field protection above.

### Soft Delete Pattern for Products
When removing products that have business history (orders, stock moves, production orders), use soft delete instead of hard delete:
```python
# Soft delete: mark inactive, preserve all history
now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
Product.query.filter_by(is_active=True).update({'is_active': False, 'deleted_at': now})
db.session.commit()
```

**Why soft delete**: Products reference purchase orders, sales orders, production orders, stock moves, and BOMs. Hard deleting breaks foreign key references and loses business history. Soft delete preserves everything while hiding products from active management.

**Cascade analysis before removing products**: Check ALL related tables to understand impact:
```python
# Tables that reference products:
# - boms (parent_product_id, component_product_id)
# - production_orders (product_id)
# - production_order_lines (product_id)
# - purchase_order_lines (product_id)
# - sales_order_lines (product_id)
# - stock_moves (product_id)
# - stock_current (product_id)
```

**Pitfall — Stock API must filter inactive products**: The `/api/stock` endpoint does NOT automatically exclude soft-deleted products. Without filtering, inactive products still appear in the stock management page:
```python
# WRONG: shows stock for ALL products including inactive
query = StockCurrent.query

# RIGHT: only show active products' stock
query = StockCurrent.query.join(Product).filter(Product.is_active == True)
```
Apply this filter to the stock API endpoint immediately after soft-deleting products. The products list API (`/api/products`) already filters by `is_active=True`, but the stock API does NOT — this is the #1 cause of "deleted products still showing in inventory" bugs.

**Recovery**: To restore soft-deleted products:
```python
Product.query.filter_by(is_active=False).update({'is_active': True, 'deleted_at': None})
db.session.commit()
```

### DELETE with Foreign Key References
When deleting a record that other tables reference via foreign keys, you MUST nullify FK references first. Otherwise SQLAlchemy raises `IntegrityError: NOT NULL constraint failed`:

```python
@app.route('/api/agents/<int:aid>', methods=['DELETE'])
def delete_agent(aid):
    agent = Agent.query.get_or_404(aid)
    # Nullify foreign key references before deleting
    Message.query.filter_by(sender_id=aid).update({'sender_id': None})
    Message.query.filter_by(receiver_id=aid).update({'receiver_id': None})
    Task.query.filter_by(assigned_agent_id=aid).update({'assigned_agent_id': None})
    db.session.delete(agent)
    db.session.commit()
    return jsonify({'ok': True})
```

**Alternative**: Set `nullable=True` on FK columns in the model so deletions cascade gracefully:
```python
sender_id = db.Column(db.Integer, db.ForeignKey('agents.id'), nullable=True)  # Allow null for deleted agents
```

**Pitfall**: Without nullifying, even `db.session.delete()` fails because SQLAlchemy tries to SET NULL on non-nullable FK columns. Always either: (1) nullify references first, or (2) set `nullable=True` on the FK column.

### DELETE Endpoint Pattern
Guard by status — allow deletion only for reversible states:
```python
@app.route('/api/<resource>/<int:oid>', methods=['DELETE'])
def api_delete_<resource>(oid):
    obj = Model.query.get_or_404(oid)
    if obj.status not in ('draft', 'released'):  # adjust per business rules
        return jsonify({'error': '只能删除草稿或已下达的xxx'}), 400
    db.session.delete(obj)  # cascade='all, delete-orphan' handles children
    db.session.commit()
    return jsonify({'success': True})
```

**Cascade check before deletion**: When deleting a parent record (e.g. supplier), check for related records first:
```python
@app.route('/api/suppliers/<int:sid>', methods=['DELETE'])
def api_delete_supplier(sid):
    s = Supplier.query.get_or_404(sid)
    po_count = PurchaseOrder.query.filter_by(supplier_id=sid).count()
    if po_count > 0:
        return jsonify({'error': f'该供应商有 {po_count} 个关联采购订单，无法删除'}), 400
    db.session.delete(s)
    db.session.commit()
    return jsonify({'success': True})
```
**Pitfall**: Without the cascade check, deleting a supplier with existing POs raises IntegrityError (foreign key violation). Always check related records before deletion.
**Pitfall**: Without `cascade='all, delete-orphan'` on the parent relationship, deleting a parent with child rows raises IntegrityError. Always define it:
```python
lines = db.relationship('ChildModel', backref='parent', lazy=True, cascade='all, delete-orphan')
```

**Supplier categories**: Use `<optgroup>` grouped dropdowns with comprehensive subcategories:
- 电子类: 电阻/电容/电感/IC芯片/连接器/传感器/晶振/二三极管/LED/电源模块
- 结构类: PCB/结构件/模具/紧固件/散热件/弹簧
- 其他: 线材线缆/包材/化工辅料/外协加工/显示器件/电池/胶布

### Computed/Calculated Fields — Multi-Layer Protection
When a field becomes dynamically computed (e.g. `online_stock` = sum of released production orders), you MUST protect it at ALL five layers. Missing any one causes silent data corruption:

1. **Backend list API**: compute value dynamically, don't read stored column
   ```python
   online = db.session.query(db.func.sum(ProductionOrder.quantity)).filter(
       ProductionOrder.product_id == p.id, ProductionOrder.status == 'released').scalar() or 0
   d['online_stock'] = float(online)
   ```
2. **Backend PUT**: skip field in setattr loop
   ```python
   skip = {'online_stock'}  # auto-computed, never overwrite
   for k, v in request.json.items():
       if hasattr(p, k) and k not in skip:
           setattr(p, k, v)
   ```
3. **Frontend form**: `readonly disabled` so the field is visible but non-editable
   ```html
   <input name="online_stock" readonly disabled>
   ```
4. **Frontend save**: remove from numeric conversion forEach — `disabled` fields are excluded from `FormData`, but the forEach would create `data['online_stock'] = 0` from `undefined`, which then overwrites the real value
   ```javascript
   // BEFORE (broken): [..., 'online_stock', ...].forEach(k => data[k] = Number(data[k]) || 0);
   // AFTER (fixed):
   ['standard_cost', 'list_price', 'min_stock', ...].forEach(k => data[k] = Number(data[k]) || 0);
   ```
5. **Excel export**: same dynamic query as the list API, not `p.online_stock`

**Pitfall**: The #4 forEach bug is the sneakiest — the form appears read-only but the save still sends `0` to the backend. If the backend doesn't have the #2 skip guard, the stored value silently zeroes out.

### Save All Data Endpoint (`/api/save-all`)
Frontend "保存" button calls this endpoint to export ALL data to Excel. Separate from the export endpoint — this one is triggered by user action, not browser download:
```python
@app.route('/api/save-all', methods=['POST'])
def api_save_all():
    """保存所有数据到Excel"""
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        # Create sheets for each module: 产品, 客户, 供应商, 销售订单, 采购订单, 库存, 应收款, 应付款
        # Each sheet: headers in row 1, data rows below
        # Use to_dict() or direct field access for data
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'sg_erp_{timestamp}.xlsx'
        filepath = os.path.join(os.path.dirname(__file__), 'backups', filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        wb.save(filepath)
        return jsonify({'success': True, 'filename': filename})
    except ImportError:
        return jsonify({'error': '需要安装 openpyxl: pip install openpyxl'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500
```
Frontend handler:
```javascript
async function saveAll() {
  const btn = document.querySelector('.btn-success[onclick="saveAll()"]');
  if (btn) { btn.disabled = true; btn.innerHTML = '<i class="bi bi-hourglass-split"></i> 保存中...'; }
  try {
    const r = await api.post('/api/save-all', {});
    if (r.success) { toast('数据已保存: ' + r.filename); }
    else { toast(r.error || '保存失败', 'error'); }
  } catch (e) { toast('保存失败: ' + e.message, 'error'); }
  finally { if (btn) { btn.disabled = false; btn.innerHTML = '<i class="bi bi-save"></i> 保存'; } }
}
```
**Pitfall**: Frontend calls `/api/save-all`, not `/api/export/excel`. These are different endpoints — one is user-triggered save, the other is browser download.
```javascript
// In the table row renderer:
${o.status === 'draft' ? `<button class="btn btn-sm btn-outline-danger" onclick="deleteItem(${o.id})">删除</button>` : ''}

// Handler:
async function deleteItem(id) {
  if (!confirm('确认删除？')) return;
  try { await api.del(`/api/resource/${id}`); toast('已删除'); loadList(); }
  catch(e) { toast(e.message, 'error'); }
}
```

### Sales Order Edit (PUT with Line Rebuild)
When editing orders with child lines, the safest approach is DELETE + RECREATE:
```python
@app.route('/api/sales-orders/<int:oid>', methods=['PUT'])
def api_update_sales_order(oid):
    so = SalesOrder.query.get_or_404(oid)
    if so.status not in ('draft', 'confirmed'):
        return jsonify({'error': '只能编辑草稿或已确认的订单'}), 400
```
**Status-gated editing**: Allow editing for multiple statuses (draft + confirmed), not just draft. The frontend shows the edit button for each allowed status:
```javascript
${o.status === 'draft' ? `<button onclick="edit(${o.id})">编辑</button> <button onclick="confirm(${o.id})">确认</button>` : ''}
${o.status === 'confirmed' ? `<button onclick="edit(${o.id})">编辑</button>` : ''}
```
And the edit form function checks:
```javascript
if (!['draft','confirmed'].includes(o.status)) { toast('只能编辑草稿或已确认的订单', 'error'); return; }
```

### Purchase Order Edit (PUT with Line Rebuild)
Same DELETE + RECREATE pattern for child lines. Purchase orders support payment_type and auto-update related payable records:
```python
@app.route('/api/purchase-orders/<int:oid>', methods=['PUT'])
def api_update_purchase_order(oid):
    po = PurchaseOrder.query.get_or_404(oid)
    if po.status != 'draft':
        return jsonify({'error': '只能编辑草稿状态的订单'}), 400
    data = request.json
    lines_data = data.pop('lines', [])
    # Update header fields
    po.supplier_id = data.get('supplier_id', po.supplier_id)
    po.payment_type = data.get('payment_type', po.payment_type)
    po.delivery_date = data.get('delivery_date', po.delivery_date)
    po.remark = data.get('remark', po.remark)
    # Delete old lines, create new ones
    PurchaseOrderLine.query.filter_by(order_id=po.id).delete()
    total = Decimal('0')
    for i, line in enumerate(lines_data, 1):
        qty = Decimal(str(line['quantity']))
        price = Decimal(str(line['unit_price']))
        amt = qty * price
        db.session.add(PurchaseOrderLine(order_id=po.id, line_no=i, product_id=line['product_id'], quantity=qty, unit_price=price, amount=amt))
        total += amt
    po.total_amount = total
    # Update related payable (if credit order)
    if po.payment_type != 'cash':
        payable = Payable.query.filter_by(po_id=po.id).first()
        if payable:
            payable.amount = total
            payable.supplier_id = po.supplier_id
            payable.due_date = po.delivery_date
    db.session.commit()
    return jsonify({'success': True})
```

**Frontend edit function**: Load order detail, pre-fill form, populate line rows:
```javascript
async function editPO(id) { showPOForm(id); }

async function showPOForm(editId) {
  const [sups, prods] = await Promise.all([api.get('/api/suppliers?per_page=200'), api.get('/api/products?per_page=200')]);
  // ... build form HTML with pre-filled values if editId ...
  let po = {};
  if (editId) po = await api.get(`/api/purchase-orders/${editId}`);
  // Set supplier, payment_type, delivery_date, remark from po
  // For each existing line: addPOLine(line) then set product_id, quantity, unit_price
}

function addPOLine(lineData) {
  const line = lineData || {};
  // Insert row with line.quantity, line.unit_price pre-filled
  if (line.product_id) {
    const lastSel = tbody.lastElementChild.querySelector('select');
    if (lastSel) lastSel.value = line.product_id;
  }
}
```

**Pitfall**: When updating related payable, only update if payment_type is 'cash'. Cash orders have no payable record to update.
```python
@app.route('/api/sales-orders/<int:oid>', methods=['PUT'])
def api_update_sales_order(oid):
    so = SalesOrder.query.get_or_404(oid)
    if so.status != 'draft':
        return jsonify({'error': '只能编辑草稿状态的订单'}), 400
    data = request.json
    lines_data = data.pop('lines', [])
    clean_dates(data, 'delivery_date')
    # Update header fields (skip protected ones)
    for k, v in data.items():
        if hasattr(so, k) and k not in ('order_no', 'status'):
            setattr(so, k, v)
    # Rebuild lines: delete all, recreate
    SalesOrderLine.query.filter_by(order_id=so.id).delete()
    total = Decimal('0')
    for i, line in enumerate(lines_data, 1):
        qty = Decimal(str(line['quantity']))
        price = Decimal(str(line['unit_price']))
        disc = Decimal(str(line.get('discount_pct', 0)))
        amt = qty * price * (1 - disc / 100)
        db.session.add(SalesOrderLine(
            order_id=so.id, line_no=i, product_id=line['product_id'],
            quantity=qty, unit_price=price, discount_pct=disc, amount=amt
        ))
        total += amt
    so.total_amount = total
    db.session.commit()
    return jsonify({'success': True})
```

**Frontend edit form**: load detail via GET, pre-fill form fields + loop through lines to populate child rows:
```javascript
async function showEditForm(id) {
  const [o, ...] = await Promise.all([api.get(`/api/orders/${id}`), ...]);
  // Build same form as create, but with values pre-filled
  // For each existing line: addSOLine() then set field values
  (o.lines || []).forEach(l => {
    addSOLine();
    const tr = document.getElementById('linesBody').lastElementChild;
    tr.querySelector('[name="product_id"]').value = l.product_id;
    tr.querySelector('[name="quantity"]').value = l.quantity;
  });
}
```

### BOM Cost Calculation

See `references/bom-unit-price.md` for the full pattern: stored `unit_price` on BOM model with priority chain (manual > receipt > PO > standard_cost), PUT endpoint, inline editable prices with source badges, and SQLite migration.

### Alternative Parts (备用料号) Pattern
Products can have multiple alternative/interchangeable part numbers from different suppliers. Use a separate table (not a column on Product) since it's a 1-to-many relationship:

```python
class AlternativePart(db.Model, TimestampMixin):
    __tablename__ = 'alternative_parts'
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    alt_sku = db.Column(db.String(50), nullable=False)
    alt_name = db.Column(db.String(200))
    remark = db.Column(db.String(200))
    product = db.relationship('Product', backref=db.backref('alternatives', lazy=True, cascade='all, delete-orphan'))
```

**CRUD endpoints**:
```python
@app.route('/api/alternative-parts/<int:pid>')       # GET list by product
@app.route('/api/alternative-parts', methods=['POST']) # create
@app.route('/api/alternative-parts/<int:aid>', methods=['DELETE'])  # delete
```

**Stock API integration**: Include alternatives in the stock response:
```python
alts = AlternativePart.query.filter_by(product_id=s.product_id).all()
d['alternatives'] = [{'id': a.id, 'alt_sku': a.alt_sku, 'alt_name': a.alt_name or '', 'remark': a.remark or ''} for a in alts]
```

**Frontend**: Show alternatives as badges in the stock table. Edit modal includes inline alternative parts management (add/delete without page reload — re-call `showEditMaterial()` after each add/delete to refresh the list).

**Dropdown integration**: See `references/alternative-parts-dropdown.md` for bulk API + frontend pattern to show `[备:ALT1/ALT2]` in product dropdowns (PO form, SO form, etc.).

**Pitfall**: Use `api.del()` (not `api.delete()`) for DELETE requests — `delete` is a JS reserved word.

### Stock Excel Export Pattern
```python
@app.route('/api/stock/export')
def api_stock_export():
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    from io import BytesIO
    from flask import send_file
    # ... query with same filters as /api/stock ...
    # Headers with blue background + white bold font
    # Data rows with thin borders
    # Column widths: [6, 15, 30, 25, 20, 8, 10, 12, 12]
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=f'filename_{timestamp}.xlsx',
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
```

**Frontend**: `window.open('/api/stock/export?q=...', '_blank')` — opens download in new tab, preserves current page state.

### Showing All Items Including Zero Stock (LEFT JOIN)
When the inventory page must show ALL products even if they have no stock records:
```python
query = db.session.query(Product, StockCurrent, Location).outerjoin(
    StockCurrent, StockCurrent.product_id == Product.id
).outerjoin(
    Location, StockCurrent.location_id == Location.id
).filter(Product.product_type != 'finished', Product.is_active == True)
```
Then handle nullable in results:
```python
'on_hand_qty': float(sc.on_hand_qty or 0) if sc else 0,
'location_code': loc.code if loc else '-',
```
**Pitfall**: A regular `join` silently drops products with no stock records. Always use `outerjoin` when the user expects to see all items.

### Scrollable Table Containers
For long data lists, constrain table height to avoid page overflow:
```html
<div class="card-body p-0" style="max-height:85vh;overflow-y:auto">
  <table class="table table-hover">...</table>
</div>
```
And increase `per_page` to 200 so all items load in one page. Default 50 causes items to be hidden on page 2.

### Accounts Receivable / Payable Management
Split "收付款" into two independent modules: 应收款管理 (linked to deliveries) and 应付款管理 (linked to purchase orders).

**Models**:
```python
class Receivable(db.Model, TimestampMixin):
    __tablename__ = 'receivables'
    recv_no = db.Column(db.String(30), unique=True)  # RCV-YYYYMMDD-XXX
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id'))
    delivery_id = db.Column(db.Integer, db.ForeignKey('delivery_orders.id'))
    amount = db.Column(db.Numeric(15, 2))       # 应收金额
    received_amount = db.Column(db.Numeric(15, 2), default=0)  # 已收金额
    due_date = db.Column(db.Date)
    payment_method = db.Column(db.String(20))   # bank/cash/check/online
    status = db.Column(db.String(20), default='pending')  # pending/partial/paid
    customer = db.relationship('Customer')
    delivery = db.relationship('DeliveryOrder')

class Payable(db.Model, TimestampMixin):
    __tablename__ = 'payables'
    pay_no = db.Column(db.String(30), unique=True)  # PAY-YYYYMMDD-XXX
    supplier_id = db.Column(db.Integer, db.ForeignKey('suppliers.id'))
    po_id = db.Column(db.Integer, db.ForeignKey('purchase_orders.id'))
    amount = db.Column(db.Numeric(15, 2))       # 应付金额
    paid_amount = db.Column(db.Numeric(15, 2), default=0)  # 已付金额
    due_date = db.Column(db.Date)
    payment_method = db.Column(db.String(20))
    status = db.Column(db.String(20), default='pending')  # pending/partial/paid
    supplier = db.relationship('Supplier')
    po = db.relationship('PurchaseOrder')
```

**Conditional generation (payment_type)**: Purchase orders can be "现款" (cash) or "欠款" (credit). Cash orders do NOT generate payable records. Add `payment_type` field to PurchaseOrder model:
```python
class PurchaseOrder(db.Model, TimestampMixin):
    payment_type = db.Column(db.String(20), default='credit')  # cash(现款)/credit(欠款)
```
Frontend: add dropdown in PO form, show badge in list (green 现款 / yellow 欠款).
Backend: only generate payable when `payment_type != 'cash'`:
```python
# 自动生成应付款记录（仅欠款订单）
if total > 0 and po.payment_type != 'cash':
    # ... create Payable record
```
**SQLite migration**: When adding `payment_type` column to existing table:
```python
import sqlite3
conn = sqlite3.connect('data/sg_erp.db')
cursor = conn.cursor()
cursor.execute('PRAGMA table_info(purchase_orders)')
columns = [row[1] for row in cursor.fetchall()]
if 'payment_type' not in columns:
    cursor.execute('ALTER TABLE purchase_orders ADD COLUMN payment_type VARCHAR(20) DEFAULT "credit"')
    conn.commit()
conn.close()
```
**Pitfall**: `db.create_all()` only creates new TABLES, not new COLUMNS. Always use sqlite3 + ALTER TABLE for adding columns to existing databases. Use raw sqlite3 connection for migrations:
```python
import sqlite3
conn = sqlite3.connect('data/sg_erp.db')
cursor = conn.cursor()
cursor.execute('PRAGMA table_info(table_name)')
columns = [row[1] for row in cursor.fetchall()]
if 'new_column' not in columns:
    cursor.execute('ALTER TABLE table_name ADD COLUMN new_column VARCHAR(20) DEFAULT "default_value"')
    conn.commit()
conn.close()
```
**Pitfall**: SQLite `ALTER TABLE ADD COLUMN` only supports DEFAULT for new rows. Existing rows get NULL, not the default. If you need to backfill, update existing rows separately after adding the column.

**Auto-generation pattern**: Create financial records automatically when business documents are created. **ALWAYS use MAX pattern for numbering** (never .count()):
```python
# In api_create_purchase_order():
if total > 0 and po.payment_type != 'cash':
    today_str_pay = datetime.now().strftime('%Y%m%d')
    max_pay = Payable.query.filter(
        Payable.pay_no.like(f'PAY-{today_str_pay}-%')
    ).order_by(Payable.pay_no.desc()).first()
    if max_pay:
        last_num = int(max_pay.pay_no.split('-')[-1])
        pay_no = f'PAY-{today_str_pay}-{str(last_num+1).zfill(3)}'
    else:
        pay_no = f'PAY-{today_str_pay}-001'
    db.session.add(Payable(pay_no=pay_no, supplier_id=po.supplier_id, po_id=po.id,
                           amount=total, due_date=po.delivery_date,
                           status='pending', remark=f'采购订单 {po_no} 自动生成'))

# In api_create_delivery():
total_amount = sum(float(l.amount or 0) for l in dl.lines)
if total_amount > 0:
    today_str_rcv = datetime.now().strftime('%Y%m%d')
    max_rcv = Receivable.query.filter(
        Receivable.recv_no.like(f'RCV-{today_str_rcv}-%')
    ).order_by(Receivable.recv_no.desc()).first()
    if max_rcv:
        last_num = int(max_rcv.recv_no.split('-')[-1])
        recv_no = f'RCV-{today_str_rcv}-{str(last_num+1).zfill(3)}'
    else:
        recv_no = f'RCV-{today_str_rcv}-001'
    db.session.add(Receivable(recv_no=recv_no, customer_id=dl.customer_id, delivery_id=dl.id,
                              amount=total_amount, status='pending',
                              remark=f'出库单 {delivery_no} 自动生成'))
```

**PUT endpoint for receivables/payables**: Both modules need GET detail, POST create, PUT update, DELETE. The PUT endpoint auto-calculates status:
```python
@app.route('/api/payables/<int:pid>', methods=['PUT'])
def api_update_payable(pid):
    p = Payable.query.get_or_404(pid)
    data = request.json
    clean_dates(data, 'due_date')
    for k, v in data.items():
        if hasattr(p, k) and k not in ('id', 'pay_no', 'created_at', 'updated_at'):
            setattr(p, k, v)
    # Auto-calculate status
    amount = float(p.amount or 0)
    paid = float(p.paid_amount or 0)
    if paid >= amount:
        p.status = 'paid'
    elif paid > 0:
        p.status = 'partial'
    else:
        p.status = 'pending'
    db.session.commit()
    return jsonify({'success': True})
```
Same pattern for receivables (use `received_amount` instead of `paid_amount`).

### Purchase Order Confirmation Adds Inventory
When confirming a purchase order, automatically increase raw materials inventory at the SY location. This is separate from the auto-generation of payable records.

```python
@app.route('/api/purchase-orders/<int:oid>/confirm', methods=['POST'])
def api_confirm_purchase_order(oid):
    po = PurchaseOrder.query.get_or_404(oid)
    if po.status != 'draft':
        return jsonify({'error': '只能确认草稿状态的订单'}), 400

    sy_loc = Location.query.filter_by(code='SY', is_active=True).first()
    if not sy_loc:
        return jsonify({'error': '未找到SY库位'}), 400

    today_str = datetime.now().strftime('%Y%m%d')
    move_count = StockMove.query.filter(StockMove.move_no.like(f'MV-{today_str}-%')).count()

    for line in po.lines:
        qty = float(line.quantity or 0)
        if qty <= 0:
            continue

        # Update or create stock record
        sc = StockCurrent.query.filter_by(
            product_id=line.product_id, location_id=sy_loc.id
        ).first()
        if sc:
            sc.on_hand_qty = float(sc.on_hand_qty or 0) + qty
            sc.last_move_date = datetime.now()
        else:
            sc = StockCurrent(
                product_id=line.product_id, location_id=sy_loc.id,
                on_hand_qty=qty, last_move_date=datetime.now()
            )
            db.session.add(sc)

        # Record stock move for audit trail
        move_count += 1
        move_no = f'MV-{today_str}-{str(move_count).zfill(3)}'
        db.session.add(StockMove(
            move_no=move_no, product_id=line.product_id,
            to_location_id=sy_loc.id, quantity=qty,
            move_type='receipt', reference_type='purchase_order',
            reference_id=po.id, move_date=datetime.now(),
            remark=f'采购订单 {po.po_no} 入库'
        ))

    po.status = 'confirmed'
    db.session.commit()
    return jsonify({'success': True})
```

**Delete reverses inventory**: When deleting a confirmed PO, reverse all receipt stock moves:
```python
if po.status == 'confirmed':
    moves = StockMove.query.filter_by(
        reference_type='purchase_order', reference_id=po.id, move_type='receipt'
    ).all()
    for move in moves:
        sc = StockCurrent.query.filter_by(
            product_id=move.product_id, location_id=move.to_location_id
        ).first()
        if sc:
            sc.on_hand_qty = float(sc.on_hand_qty or 0) - float(move.quantity)
            sc.last_move_date = datetime.now()
        db.session.delete(move)
```

**Auto-status calculation**: Update status based on paid/received amounts:
```python
amount = float(obj.amount or 0)
paid = float(obj.paid_amount or 0)  # or received_amount for receivables
if paid >= amount:
    obj.status = 'paid'
elif paid > 0:
    obj.status = 'partial'
else:
    obj.status = 'pending'
```

**Delete cascade**: When deleting a purchase order, also delete its auto-generated payable:
```python
Payable.query.filter_by(po_id=po.id).delete()
db.session.delete(po)
```

**Confirmed PO deletion**: Allow deleting confirmed purchase orders (not just draft). When deleting confirmed, reverse all receipt stock moves:
```python
if po.status == 'confirmed':
    moves = StockMove.query.filter_by(
        reference_type='purchase_order', reference_id=po.id, move_type='receipt'
    ).all()
    for move in moves:
        sc = StockCurrent.query.filter_by(
            product_id=move.product_id, location_id=move.to_location_id
        ).first()
        if sc:
            sc.on_hand_qty = float(sc.on_hand_qty or 0) - float(move.quantity)
            sc.last_move_date = datetime.now()
        db.session.delete(move)
```

**Dashboard summary**: Use the new models for accounts_receivable and accounts_payable:
```python
ar = db.session.query(db.func.sum(Receivable.amount - Receivable.received_amount)).filter(
    Receivable.status.in_(['pending', 'partial'])).scalar() or 0
ap = db.session.query(db.func.sum(Payable.amount - Payable.paid_amount)).filter(
    Payable.status.in_(['pending', 'partial'])).scalar() or 0
```

**Frontend page structure**: Each module has its own page with status filter + CRUD:
- 应收款管理: columns = 单号|客户|关联出货单|应收金额|已收金额|未收金额|到期日|状态|操作
- 应付款管理: columns = 单号|供应商|关联采购单|应付金额|已付金额|未付金额|到期日|状态|操作
- Both support: 新增(edit form), 编辑(PUT), 删除(DELETE)
- Edit form: dropdown for customer/supplier, dropdown for delivery/PO (optional), amount, paid amount, due date, payment method, remark

### Order List with Aggregated Line Data
When the parent list needs to show data from child lines (product SKUs, undelivered qty, pending amount):
```python
for o in pagination.items:
    d = to_dict(o)
    skus, prices = [], []
    total_undelivered, pending_amount = 0, 0
    for line in o.lines:
        if line.product:
            skus.append(line.product.sku)
        prices.append(str(float(line.unit_price or 0)))
        undelivered = float(line.quantity or 0) - float(line.delivered_qty or 0)
        if undelivered > 0:
            total_undelivered += undelivered
            pending_amount += undelivered * float(line.unit_price or 0)
    d['product_skus'] = ', '.join(skus)
    d['unit_prices'] = ', '.join(prices)
    d['undelivered_qty'] = total_undelivered
    d['pending_amount'] = round(pending_amount, 2)
    items.append(d)
```
**Pitfall**: SKUs and prices arrays must stay aligned — append both per line or display will mismatch.

### Delivery/Shipment Management (出货管理)

**Card-based list with inline details**: Show each delivery as a Bootstrap card with a detail table inside (not a flat table):
```javascript
el.innerHTML = d.items.map(dl => {
  const linesHtml = (dl.lines || []).map(l =>
    `<tr><td>${l.product_sku}</td><td>${l.product_name}</td><td>${l.quantity}</td><td>${l.lot_no || '-'}</td></tr>`
  ).join('');
  return `<div class="card mb-2">
    <div class="card-header d-flex justify-content-between align-items-center">
      <span><strong>${dl.delivery_no}</strong> &nbsp; 客户：${dl.customer_name} &nbsp; ${fmtDate(dl.delivery_date)}</span>
      <span><button class="btn btn-sm btn-outline-secondary" onclick="recallDL(${dl.id})">撤回</button>
             <button class="btn btn-sm btn-outline-danger" onclick="deleteDL(${dl.id})">删除</button></span>
    </div>
    <div class="card-body p-0">
      <table class="table table-sm mb-0"><thead>...</thead><tbody>${linesHtml}</tbody></table>
    </div>
  </div>`;
}).join('');
```

**FIFO deduction WITH customer filter**: Always filter by `customer_id` — only deduct from the same customer's sales orders:
```python
remaining = delivered_qty
sols = SalesOrderLine.query.join(SalesOrder).filter(
    SalesOrderLine.product_id == line['product_id'],
    SalesOrder.customer_id == dl.customer_id,  # MUST match customer
    SalesOrder.status.in_(['confirmed', 'partial_delivered'])
).order_by(SalesOrder.created_at.asc()).all()
for sol in sols:
    if remaining <= 0: break
    undelivered = float(sol.quantity or 0) - float(sol.delivered_qty or 0)
    if undelivered <= 0: continue
    deduct = min(remaining, undelivered)
    sol.delivered_qty = float(sol.delivered_qty or 0) + deduct
    remaining -= deduct
```
**Pitfall**: Without the `customer_id` filter, shipping to customer A would deduct from customer B's orders. Always include it.

**FIFO with PO Splitting (一行拆多行)**: When delivery quantity exceeds one PO's remaining, auto-split into multiple delivery lines, each with its own PO and price. DeliveryOrderLine stores `customer_po` and `so_id` for precise tracking:
```python
line_no = 0
for line in lines_data:
    remaining = float(line['quantity'])
    sols = SalesOrderLine.query.join(SalesOrder).filter(
        SalesOrderLine.product_id == line['product_id'],
        SalesOrder.customer_id == dl.customer_id,
        SalesOrder.status.in_(['confirmed', 'partial_delivered'])
    ).order_by(SalesOrder.created_at.asc()).all()
    for sol in sols:
        if remaining <= 0: break
        undelivered = float(sol.quantity or 0) - float(sol.delivered_qty or 0)
        if undelivered <= 0: continue
        deduct = min(remaining, undelivered)
        line_no += 1
        price = float(sol.unit_price or 0)
        db.session.add(DeliveryOrderLine(
            delivery_id=dl.id, line_no=line_no,
            product_id=line['product_id'],
            quantity=Decimal(str(deduct)),
            unit_price=price, amount=round(deduct * price, 2),
            customer_po=sol.order.customer_po if sol.order else '',
            so_id=sol.order_id, lot_no=lot_no
        ))
        sol.delivered_qty = float(sol.delivered_qty or 0) + deduct
        remaining -= deduct
    # Remaining unmatched qty still creates a line (price=0)
    if remaining > 0:
        line_no += 1
        db.session.add(DeliveryOrderLine(
            delivery_id=dl.id, line_no=line_no,
            product_id=line['product_id'],
            quantity=Decimal(str(remaining)),
            unit_price=0, amount=0,
            customer_po='', so_id=None, lot_no=lot_no
        ))
```
**Model additions**: `DeliveryOrderLine` needs `customer_po`, `so_id`, `unit_price`, `amount` columns. `DeliveryOrder` also needs `customer_po` (auto-filled from customer's latest SO).

**Pitfall**: Without `so_id` on each line, recall/reverse can't know which SO to restore. Always store the matched SO ID per line.

**Precise recall via so_id**: Since each line stores its SO ID, recall is exact — no need to reverse-engineer the FIFO match:
```python
def _reverse_delivery(dl):
    for line in dl.lines:
        if line.so_id:
            sol = SalesOrderLine.query.get(line.so_id)
            if sol:
                sol.delivered_qty = max(0, float(sol.delivered_qty or 0) - float(line.quantity or 0))
```

**Reverse deduction on recall/delete (legacy, when so_id not stored)**: Reverse from newest order first (opposite of FIFO creation order), also filtered by customer:
```python
sols = SalesOrderLine.query.join(SalesOrder).filter(
    SalesOrderLine.product_id == line.product_id,
    SalesOrder.customer_id == dl.customer_id,  # same customer filter
    SalesOrderLine.delivered_qty > 0
).order_by(SalesOrder.created_at.desc()).all()
```

**Delivery line pricing from sales orders**: When creating a delivery, auto-fill `unit_price` and `amount` from the customer's latest matching sales order line:
```python
sol_for_price = SalesOrderLine.query.join(SalesOrder).filter(
    SalesOrderLine.product_id == line['product_id'],
    SalesOrder.customer_id == dl.customer_id,
    SalesOrder.status.notin_(['draft', 'cancelled'])
).order_by(SalesOrder.created_at.desc()).first()
price = float(sol_for_price.unit_price or 0) if sol_for_price else 0
amt = float(qty) * price
```
**Pitfall**: DeliveryOrderLine needs `unit_price` and `amount` columns. Without them, monthly sales cannot be calculated from deliveries.

**Monthly sales from delivery data**: When the business tracks sales by actual shipments (not SO creation):
```python
month_sales = db.session.query(db.func.sum(DeliveryOrderLine.amount)).join(
    DeliveryOrder, DeliveryOrderLine.delivery_id == DeliveryOrder.id
).filter(
    DeliveryOrder.delivery_date >= month_start,
    DeliveryOrder.status != 'cancelled'
).scalar() or 0
```

**Delivery form without sales order linkage**: The form has customer selector (short_name) + product lines. No SO dropdown. System auto-matches SOs via FIFO by customer. Product list shows only finished products (`product_type=finished`).

**Auto-fill customer_po on creation**: When creating a delivery, auto-populate customer_po from the customer's most recent SO that has one:
```python
if not dl.customer_po and dl.customer_id:
    recent_so = SalesOrder.query.filter(
        SalesOrder.customer_id == dl.customer_id,
        SalesOrder.customer_po.isnot(None),
        SalesOrder.customer_po != ''
    ).order_by(SalesOrder.created_at.desc()).first()
    if recent_so:
        dl.customer_po = recent_so.customer_po
```

**Real-time list refresh after creation**: Always call `loadList()` inside the success callback so the new item appears without manual refresh:
```javascript
const r = await api.post('/api/deliveries', data);
if (r.success) { toast('已创建'); bootstrap.Modal.getInstance(document.getElementById('appModal')).hide(); loadDeliveries(); }
```

### Print Layout (出库单打印)
Use `window.open()` to render a styled printable document:
```javascript
async function printDL(id) {
  const dl = await api.get(`/api/deliveries/${id}`);
  // Build HTML with company header, customer info, line table, signature area
  const html = `<!DOCTYPE html><html><head>
    <style>
      body { font-family: "Microsoft YaHei", sans-serif; padding: 30px; }
      .header { text-align:center; margin-bottom:20px; }
      table { width:100%; border-collapse:collapse; }
      th, td { border:1px solid #333; padding:6px 8px; text-align:center; }
      th { background:#f0f0f0; }
      .sign { margin-top:40px; display:flex; justify-content:space-between; }
      .sign div { width:30%; text-align:center; border-top:1px solid #333; padding-top:6px; }
      @media print { body { padding:15px; } }
    </style></head><body>
    <!-- Company header, customer info, table, signature -->
  </body></html>`;
  const win = window.open('', '_blank');
  win.document.write(html);
  win.document.close();
  win.onload = () => { win.print(); };
}
```
**Key layout sections**: Company name (centered, large), document title, order number + date, customer info (left) + shipping info (right), detail table (序号/编号/名称/单位/数量/单价/金额/PO号/批号), summary row, remarks, signature area (制单人/仓库主管/收货人).

### Invoice OCR Upload (发票识别)
See `references/invoice-ocr-upload.md` for the Vision LLM approach (Qwen VL Plus).
See `references/pdf-invoice-parsing.md` for the local pdfplumber + regex approach (no external API). Handles line-break splitting, mixed Chinese/English units, and spec continuation across lines.

### Report Summary API (报表统计)
Professional report dashboard with aggregated data from multiple modules. The API must return flat keys matching what the frontend expects (not nested objects):

```python
@app.route('/api/reports/summary')
def api_report_summary():
    today = date.today()
    month_start = today.replace(day=1)
    # Monthly aggregates with date filter
    month_sales = db.session.query(db.func.sum(SalesOrder.total_amount)).filter(
        SalesOrder.order_date >= month_start,
        SalesOrder.status.in_(['confirmed', 'partial_delivered', 'delivered', 'invoiced', 'paid'])
    ).scalar() or 0
    # Financial aggregates from Receivable/Payable models
    accounts_receivable = db.session.query(
        db.func.sum(Receivable.amount - Receivable.received_amount)
    ).filter(Receivable.status.in_(['pending', 'partial'])).scalar() or 0
    # Inventory value = sum(qty * cost)
    inventory_value = db.session.query(
        db.func.sum(StockCurrent.on_hand_qty * Product.standard_cost)
    ).join(Product, StockCurrent.product_id == Product.id).scalar() or 0
    # Count aggregates
    total_products = Product.query.filter_by(is_active=True).count()
    low_stock_count = StockCurrent.query.filter(StockCurrent.on_hand_qty < 10).count()
    return jsonify({
        'month_sales': float(month_sales),
        'month_purchase': float(month_purchase),
        'inventory_value': float(inventory_value),
        'accounts_receivable': float(accounts_receivable),
        'accounts_payable': float(accounts_payable),
        'total_orders': total_orders,
        'total_customers': total_customers,
        'total_products': total_products,
        'low_stock_count': low_stock_count,
        # ... more fields as needed
    })
```

**Pitfall**: Frontend and backend field names MUST match exactly. If frontend uses `s.month_sales` but backend returns `{sales: {total_amount: ...}}`, the dashboard shows zeros. Always verify the JSON structure matches the JS property access.

**Professional dashboard layout**: Use Bootstrap grid with stat-cards for KPIs, then info cards for category breakdowns, then a table for detailed analysis:
```
Row 1: [本月销售] [本月采购] [库存金额] [发票金额]  (4 cols)
Row 2: [应收账款] [应付账款] [净现金流]            (3 cols)
Row 3: [客户/供应商] [库存状态] [生产状态]         (3 cards)
Row 4: [经营分析表格 - 采购销售比/周转率/占比]     (full width)
```

### Missing API Route Causes Silent Form Failure
When a JS form uses `Promise.all` to load dropdown data, a missing API route (404) causes the ENTIRE form to fail silently — no error toast, no modal, nothing. This is the #1 cause of "无法编辑" (cannot edit) bugs.

**Symptom**: Clicking "编辑" button does nothing. No modal opens. No error message.

**Root cause**: The edit form loads related data (customers, deliveries, products) via `Promise.all([api.get('/api/customers'), api.get('/api/deliveries')])`. If ANY of those endpoints returns 404, the Promise rejects, and the `showForm()` function throws before reaching `showModal()`.

**Example**: Receivables edit form calls `/api/deliveries` for the "关联出货单" dropdown. If the delivery API endpoints were never implemented, the form silently fails.

**Fix**: Ensure ALL entity APIs referenced by form dropdowns exist. The minimum set per entity:
```python
@app.route('/api/<entity>')           # GET list
@app.route('/api/<entity>/<int:id>')  # GET detail
@app.route('/api/<entity>', methods=['POST'])    # create
@app.route('/api/<entity>/<int:id>', methods=['PUT'])     # update
@app.route('/api/<entity>/<int:id>', methods=['DELETE'])  # delete
```

**Prevention**: When adding a new form that references another entity, check if that entity's API exists FIRST. If not, add the endpoints before the form code.

**Debugging**: If a form won't open, check browser console for `TypeError: Failed to fetch` or `HTTP 404` errors on the data-loading calls. The error is always in the network tab, not in the UI.

### Duplicate Return Statement Bug
When refactoring code (e.g., replacing old logic with new), old return statements can be left behind:
```python
# BUG: Two return statements — second one uses undefined variables
def api_report_summary():
    # ... new logic ...
    return jsonify({ 'month_sales': float(month_sales), ... })
    return jsonify({ 'sales': { 'total_orders': total_so, ... } })  # DEAD CODE
```
**Symptom**: Server returns 500 error because dead code references variables from old implementation.
**Fix**: Search for duplicate `return jsonify` in the function and remove the dead one.
**Prevention**: After major refactoring, verify only one return path exists per function.

### Backorder Statistics (未交货统计)
**CORRECTION**: Backorder stats come from CUSTOMER ORDERS (sales orders), NOT purchase orders. The user explicitly corrected this — "未交货统计应统计客户订单管理中的数据".

Query unfulfilled customer orders grouped by product for reports:
```python
backorders = db.session.query(
    Product.sku, Product.name, Product.spec,
    db.func.sum(SalesOrderLine.quantity).label('ordered_qty'),
    db.func.sum(SalesOrderLine.delivered_qty).label('delivered_qty')
).join(SalesOrder, SalesOrderLine.order_id == SalesOrder.id
).join(Product, SalesOrderLine.product_id == Product.id
).filter(
    SalesOrder.status.in_(['confirmed', 'partial_delivered'])
).group_by(Product.sku, Product.name, Product.spec).all()

backorder_list = []
for bo in backorders:
    ordered = float(bo.ordered_qty or 0)
    delivered = float(bo.delivered_qty or 0)
    pending = ordered - delivered
    if pending > 0:
        backorder_list.append({
            'sku': bo.sku, 'name': bo.name, 'spec': bo.spec or '',
            'ordered_qty': ordered, 'delivered_qty': delivered, 'pending_qty': pending
        })
```
Frontend columns: 产品SKU | 产品名称 | 规格型号 | 订单数量 | 已交数量 | 未交数量 (red bold)
**Pitfall**: Do NOT use PurchaseOrderLine for backorder stats — that measures supplier delivery, not customer fulfillment. The business cares about what customers ordered but haven't received.

### File Truncation Recovery
When app.py gets corrupted (truncated to 500 lines by Python script, or broken by bad patches):
1. Use `delegate_task` to rebuild — pass models.py content, the current truncated file, and a list of all required API endpoints
2. The subagent reads the truncated file + models.py, then writes a complete new file
3. Verify with `python -c "import app; print('OK')"` before restarting
4. This is faster than trying to patch a corrupted file piece by piece

### Product List API — exclude_type Filter
When a page needs all products EXCEPT a specific type (e.g. stock page shows everything except finished goods):
```python
product_type = request.args.get('product_type', '')
exclude_type = request.args.get('exclude_type', '')
query = Product.query.filter_by(is_active=True)
if product_type:
    query = query.filter_by(product_type=product_type)
if exclude_type:
    query = query.filter(Product.product_type != exclude_type)
```
```javascript
// Stock move form: show semi/component/material, not finished
const prods = await api.get('/api/products?exclude_type=finished&per_page=200');
```

## Scope Interpretation for Page-Level Requests

**CRITICAL**: When the user says "XX页面 去掉YY" (remove YY from XX page), they mean **filter YY from the page view**, NOT delete YY from the database. This is a view-level operation, not a data operation.

**Example**: "原材料管理 去掉SGC1601B,NS16,SSD-X5" means:
- ✅ CORRECT: Add `Product.product_type != 'finished'` filter to the `/api/stock` endpoint so finished goods don't appear in the raw materials page
- ❌ WRONG: Soft-delete the products from the database (breaks product management, BOM, orders)
- ❌ WRONG: Soft-delete ALL products including raw materials (catastrophic over-reaction)

**Rule of thumb**: "从XX页面去掉YY" = filter in the API query. Never touch the product's `is_active` or `deleted_at` unless the user explicitly says "删除产品" or "停用产品".

**Layered scope check** (ask yourself before acting):
1. Is this a PAGE-LEVEL request? → Filter the API query only
2. Is this a PRODUCT-LEVEL request? → Soft-delete specific products
3. Is this a SYSTEM-LEVEL request? → Full database cleanup

**Separation of concerns**: 原材料管理 and 产品管理 are DIFFERENT views of the SAME data.

## Critical Pitfalls

### File Corruption via Python Scripts
**NEVER use Python scripts (execute_code or terminal) to modify app.py or other source files.** The `read_file` tool has a 500-line pagination limit. If you read a file with Python and write it back, only the first 500 lines are preserved — the rest is silently truncated.

**Wrong approach:**
```python
# DON'T: This truncates files > 500 lines
with open('app.py', 'r') as f:
    content = f.read()
content = content.replace(old, new)
with open('app.py', 'w') as f:
    f.write(content)
```

**Correct approach:** Use the `patch` tool for targeted edits, or `write_file` tool for complete rewrites. For batch replacements across a large file, use `delegate_task` to have a subagent handle it properly.

**Recovery**: If a file gets truncated, use `delegate_task` to rebuild it from context (models.py, other files, session history).

### SQLite Column Addition
`db.create_all()` only creates new TABLES, not new COLUMNS. When adding a field to an existing model:
1. Add the column to models.py
2. Use raw sqlite3 + ALTER TABLE to add the column
3. `db.create_all()` won't add it automatically

```python
import sqlite3
conn = sqlite3.connect('data/sg_erp.db')
cursor = conn.cursor()
cursor.execute('PRAGMA table_info(table_name)')
columns = [row[1] for row in cursor.fetchall()]
if 'new_column' not in columns:
    cursor.execute('ALTER TABLE table_name ADD COLUMN new_column VARCHAR(20) DEFAULT "value"')
    conn.commit()
conn.close()
```

### Number Generation After Deletion
Using `.count()` for sequential numbers causes UNIQUE constraint errors when records are deleted. Always use MAX pattern:
```python
# WRONG: count returns lower number after deletion
last = Model.query.filter(Model.no.like(f'PREFIX-{today}-%')).count()
no = f'PREFIX-{today}-{str(last+1).zfill(3)}'

# RIGHT: get highest existing number
max_obj = Model.query.filter(Model.no.like(f'PREFIX-{today}-%')).order_by(Model.no.desc()).first()
if max_obj:
    last_num = int(max_obj.no.split('-')[-1])
    no = f'PREFIX-{today}-{str(last_num+1).zfill(3)}'
else:
    no = f'PREFIX-{today}-001'
```

**Apply this to ALL number generation**: PO, SO, MO, DL, invoice, payment, receivable, payable, stock move.

### Purchase Order Contract Generation (采购合同)
Generate legally compliant purchase contracts from PO data. Opens in new window for printing.

**Contract structure (12 clauses per 民法典合同编)**:
1. 合同双方 — 供方(卖方)/需方(买方) full legal names
2. 供方信息 — name, address, contact, phone, tax_no, bank_name, bank_account
3. 需方信息 — same fields from company config
4. 采购产品明细 — table with 序号/产品名称/规格型号/单位/数量/单价/金额 + 金额大写
5. 质量标准 — national/industry standards, certificates, 12-month warranty
6. 交货方式 — date, location, shipping responsibility, late penalty (0.5%/day, 15-day termination right)
7. 验收标准 — 7-day inspection, 5-day defect handling
8. 付款方式 — cash/credit terms, VAT invoice (13%), payment after acceptance
9. 违约责任 — quality defects, third-party claims, late payment (0.05%/day)
10. 保密条款 — 2-year post-termination confidentiality
11. 不可抗力 — 5-day written notice, proof required
12. 争议解决 — negotiate first, buyer's jurisdiction court
Plus: 一式两份, signature/seal, supplementary agreements clause

**Amount to Chinese uppercase** (金额大写):
```python
def amount_to_chinese(num):
    digits = '零壹贰叁肆伍陆柒捌玖'
    units = ['', '拾', '佰', '仟']
    big_units = ['', '万', '亿']
    num = round(float(num), 2)
    integer_part = int(num)
    decimal_part = round((num - integer_part) * 100)
    # ... convert integer part digit by digit with units
    # jiao (角) + fen (分), or 整 if no decimal
```

**API endpoints**:
```python
@app.route('/api/purchase-orders/<int:oid>/contract')
def api_po_contract(oid):
    # Gather PO lines with product details (name, spec, unit)
    # Build company dict from app.config
    # Render po_contract.html template
    # Return HTML (opens in new window)

@app.route('/api/purchase-orders/<int:oid>/send-contract', methods=['POST'])
def api_send_po_contract(oid):
    # Log send action in PO remark: [合同发送 2026-06-02 10:30] 通过print发送给供应商
    # Auto-confirm if still draft
```

**Company info in config.py**: Add contract-specific fields:
```python
COMPANY_ADDRESS = '浙江省杭州市余杭区良渚街道金家渡南路4号S65-1室'
COMPANY_LEGAL_PERSON = ''
COMPANY_TEL = '15990127505'
COMPANY_FAX = ''
COMPANY_TAX_NO = '91330110MACYJT256X'
COMPANY_BANK_NAME = '中国农业银行股份有限公司杭州金昌路支行'
COMPANY_BANK_ACCOUNT = '19052301040013426'
```
**Pitfall**: These fields are initially empty. Contract template shows "（待填）" for missing values. User must fill them in config.py before sending real contracts.

**Extracting company info from docx**: Business registration docs (开票资料) are often Word documents. Use python-docx to extract:
```python
from docx import Document
doc = Document(r'D:\企业资料\开票资料.docx')
for p in doc.paragraphs:
    if p.text.strip():
        print(p.text)
for t in doc.tables:
    for row in t.rows:
        cells = [c.text.strip() for c in row.cells]
        print(' | '.join(cells))
```
Typical output: company name, 统一社会信用代码 (tax_no), address, phone, 开户银行, 账户号码. Map these directly to config.py fields.

**Frontend**: Add "合同" button to each PO list row, calls `viewContract(id)` which opens `/api/purchase-orders/{id}/contract` in new tab. Also add `sendContract(id)` for marking as sent.

**Template design**: See `references/po-contract-template.html` for the complete Jinja2 template. Key CSS: `@page { size: A4; margin: 20mm 15mm; }`, `@media print { .no-print { display: none; } }`, 宋体/黑体 fonts, stamp area with dashed border.

## Desktop Packaging (pywebview + PyInstaller)

When the user wants to run the Flask ERP as a native desktop app (instead of in a browser), use **pywebview** — a lightweight Python library that wraps Edge WebView2 on Windows. For standalone .exe distribution, combine with **PyInstaller**.

### Setup

```bash
pip install pywebview pyinstaller
```

Add to requirements.txt: `pywebview>=5.0`

### Phase 1: Development Launcher (`desktop.py`)

For development, use a simple launcher without PyInstaller path handling:

```python
"""Desktop launcher — wraps Flask in a native window via pywebview"""
import os, sys, time, threading, socket, webview

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

PORT = 5000
URL = f'http://127.0.0.1:{PORT}'

def is_port_open(port, timeout=0.5):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except (ConnectionRefusedError, OSError):
        return False

def start_flask():
    from app import create_app
    app = create_app()
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)

def main():
    if is_port_open(PORT):
        print(f'Port {PORT} already in use, connecting...')
    else:
        print('Starting ERP service...')
        flask_thread = threading.Thread(target=start_flask, daemon=True)
        flask_thread.start()
        for _ in range(30):
            if is_port_open(PORT): break
            time.sleep(0.3)
        else:
            print('Error: Flask startup timeout')
            sys.exit(1)

    window = webview.create_window(
        title='赛光智能ERP管理系统', url=URL,
        width=1400, height=900, min_size=(1024, 600),
        resizable=True, text_select=True,
    )
    webview.start(debug=False)

if __name__ == '__main__':
    main()
```

### Phase 2: PyInstaller Packaging

**Critical path pattern** — PyInstaller bundles code into a read-only temp dir (`sys._MEIPASS`), but databases/uploads/backups must be writable in the exe's directory:

```python
# At top of desktop.py, BEFORE any Flask imports
if getattr(sys, 'frozen', False):
    BUNDLE_DIR = sys._MEIPASS       # read-only: templates, static, .pyc
    APP_DIR = os.path.dirname(sys.executable)  # writable: data, uploads, backups
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR = BUNDLE_DIR
```

**Config override** — SQLAlchemy URI is evaluated at class definition time. You MUST override the class attribute directly, not the module variable:

```python
def start_flask():
    import config as cfg
    db_path = os.path.join(APP_DIR, 'data', 'sg_erp.db')
    cfg.Config.SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    # ... rest of Flask startup
```

**Template/static folders** — after PyInstaller, Flask must find templates in BUNDLE_DIR:

```python
    if BUNDLE_DIR != APP_DIR:
        app.template_folder = os.path.join(BUNDLE_DIR, 'templates')
        app.static_folder = os.path.join(BUNDLE_DIR, 'static')
```

**First-run DB initialization** — when exe runs on a new machine with no database:

```python
    first_run = not os.path.exists(db_path)
    # ... after create_app() ...
    if first_run:
        with app.app_context():
            from models import db
            db.create_all()
        from init_db import init_db
        init_db()
```

**PyInstaller spec file** (`sg_erp.spec`) — use `--onedir` (not `--onefile`) for webview apps:

```python
a = Analysis(
    ['desktop.py'],
    pathex=[base_dir],
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
    ],
    hiddenimports=[
        'clr', 'webview.platforms', 'webview.platforms.edgechromium',
        'flask', 'flask_cors', 'flask_login', 'flask_sqlalchemy',
        'flask_wtf', 'werkzeug', 'sqlalchemy',
        'sqlalchemy.sql.default_comparator', 'jinja2', 'markupsafe', 'dateutil',
    ],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas'],
)
# console=True initially for debugging; change to False once stable
exe = EXE(..., name='赛光智能ERP', console=True)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name='赛光智能ERP')
```

**Post-build setup** — create writable directories and copy existing database:

```bash
cd dist/赛光智能ERP
mkdir -p data uploads/invoices backups
cp ../../data/sg_erp.db data/   # preserve existing data
```

**Output structure**:
```
dist/赛光智能ERP/
├── 赛光智能ERP.exe    (double-click to run)
├── _internal/          (Python runtime + templates + static, read-only)
├── data/               (SQLite DB, writable)
├── uploads/            (uploaded files, writable)
└── backups/            (Excel exports, writable)
```

### Batch Launchers

**Pitfall — Chinese character encoding**: Windows CMD defaults to the system codepage (CP936 for Chinese Windows). .bat files containing UTF-8 Chinese characters will display as garbled text (乱码). Fix: add `chcp 65001 >nul 2>&1` as the VERY FIRST line (before any echo). Even with chcp, it's safest to use ASCII text in bat files and keep Chinese only in the Python code.

**Development** (`启动ERP.bat`):
```bat
@echo off
chcp 65001 >nul 2>&1
title SG_ERP
cd /d "%~dp0"
python desktop.py
if %errorlevel% neq 0 ( echo. & echo Startup failed & pause )
```

**Rebuild** (`打包.bat`):
```bat
@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
pyinstaller sg_erp.spec --distpath dist --workpath build --clean -y
mkdir "dist\赛光智能ERP\data" 2>nul
mkdir "dist\赛光智能ERP\uploads\invoices" 2>nul
mkdir "dist\赛光智能ERP\backups" 2>nul
if exist "data\sg_erp.db" copy "data\sg_erp.db" "dist\赛光智能ERP\data\" >nul
if exist "app.ico" copy "app.ico" "dist\赛光智能ERP\" >nul
echo Done!
pause
```

### Key Patterns

- Flask runs in a **daemon thread**, GUI on main thread
- **Port detection**: if port already occupied, connect directly
- **Startup wait loop**: poll port for up to 12s before timing out
- **debug=False, use_reloader=False**: prevents Flask from spawning extra processes that conflict with pywebview
- **Suppress werkzeug logs**: set log level to ERROR
- Window close exits the process (daemon thread dies automatically)
- `--onedir` is better than `--onefile` for webview apps (faster startup, no extraction)

### App Icon (Company Logo → .ico)

Convert a company logo PNG to a multi-resolution .ico for the exe and window icon:

```python
from PIL import Image
src = 'logo.png'
dst = 'app.ico'
img = Image.open(src).convert('RGBA')
# Add padding for small sizes
w, h = img.size
size = max(w, h)
pad = int(size * 0.05)
canvas_size = size + pad * 2
canvas = Image.new('RGBA', (canvas_size, canvas_size), (0, 0, 0, 0))
canvas.paste(img, ((canvas_size - w) // 2, (canvas_size - h) // 2), img)
sizes = [(16,16), (32,32), (48,48), (64,64), (128,128), (256,256)]
icons = [canvas.resize(s, Image.LANCZOS) for s in sizes]
icons[0].save(dst, format='ICO', sizes=sizes, append_images=icons[1:])
```

**Embed in PyInstaller** — add `icon='app.ico'` to EXE and `('app.ico', '.')` to datas:
```python
a = Analysis(..., datas=[('templates','templates'), ('static','static'), ('app.ico','.')])
exe = EXE(..., icon='app.ico')
```

**Set pywebview window icon via ctypes** — `create_window()` does NOT have an `icon` parameter (causes `TypeError: unexpected keyword argument 'icon'`). Instead, use Windows API via ctypes in the `webview.start()` callback:

```python
def set_icon():
    import ctypes
    icon_path = os.path.join(APP_DIR, 'app.ico')
    if not os.path.exists(icon_path):
        icon_path = os.path.join(BUNDLE_DIR, 'app.ico')
    if os.path.exists(icon_path):
        try:
            hwnd = window.native_handle
            if hwnd:
                hicon = ctypes.windll.user32.LoadImageW(
                    0, icon_path, 1, 0, 0, 0x00000010 | 0x00000020)
                if hicon:
                    ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, hicon)  # ICON_SMALL
                    ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, hicon)  # ICON_BIG
        except Exception:
            pass

window = webview.create_window(title=..., url=..., ...)
webview.start(set_icon, debug=False)  # callback runs after window is ready
```

**Post-build**: Copy ico to dist dir for window icon: `copy app.ico dist\AppName\`

**Logo auto-crop for ICO**: Company logos often have large white backgrounds that make the icon tiny. Auto-crop before converting:

```python
from PIL import Image, ImageDraw

img = Image.open('logo.png').convert('RGBA')
w, h = img.size
# Find non-white pixel bounds (threshold 240)
pixels = img.load()
min_x, min_y, max_x, max_y = w, h, 0, 0
for y in range(h):
    for x in range(w):
        r, g, b, a = pixels[x, y]
        if r < 240 or g < 240 or b < 240:
            min_x, min_y = min(min_x, x), min(min_y, y)
            max_x, max_y = max(max_x, x), max(max_y, y)

margin = 6
cropped = img.crop((max(0,min_x-margin), max(0,min_y-margin),
                     min(w,max_x+margin), min(h,max_y+margin)))
# Make square with white fill
cw, ch = cropped.size
sq = max(cw, ch)
canvas = Image.new('RGBA', (sq, sq), (255, 255, 255, 255))
canvas.paste(cropped, ((sq-cw)//2, (sq-ch)//2), cropped)

# Optional: rounded corners
rad = int(min(canvas.size) * 0.10)
mask = Image.new('L', canvas.size, 0)
ImageDraw.Draw(mask).rounded_rectangle(
    [(0,0),(canvas.size[0]-1,canvas.size[1]-1)], radius=rad, fill=255)
canvas.putalpha(mask)

# Save as multi-resolution ICO
base = canvas.resize((256, 256), Image.LANCZOS)
base.save('app.ico', format='ICO',
          sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
```

**Pitfall**: `PIL.Image.open()` can't read .ico files for analysis — save a .png preview separately if you need to verify the icon visually.
**Pitfall**: If the logo's effective area is only ~36% of the image (common with design exports), the icon will look tiny and blurry. Always auto-crop white borders first.
**Pitfall**: The `base.save(..., append_images=[...])` approach can produce tiny ICO files (~670 bytes). Use `base.save(dst, format='ICO', sizes=[...])` on a 256x256 base image instead — Pillow handles the resizing internally.

### Pitfalls

| Issue | Fix |
|-------|-----|
| pywebview needs GUI backend | Win10+ has Edge WebView2 built-in. Linux needs `webkit2gtk`. |
| `debug=True` conflicts with pywebview | Always use `debug=False, use_reloader=False` |
| Thread not daemon → process won't exit | Always `threading.Thread(..., daemon=True)` |
| PyInstaller can't find templates | Add `('templates', 'templates')` to `datas` in spec |
| Database path wrong after packaging | Override `cfg.Config.SQLALCHEMY_DATABASE_URI` at runtime |
| `BASE_DIR` module var already evaluated | Don't patch module var; override the Config class attribute directly |
| First run on new machine has no DB | Check `os.path.exists(db_path)`, call `init_db()` if missing |
| `--onefile` slow startup with webview | Use `--onedir` instead — webview apps work better with external files |
| console=True shows black window | Keep True for debugging; set False in spec once stable |
| Hidden import missing for webview | Add `clr`, `webview.platforms.edgechromium` to spec |
| Port 5000 conflict | Desktop.py checks port first and connects if occupied |
| `_internal` dir confusion | `sys._MEIPASS` points to `_internal/`; `sys.executable` points to exe dir |
| `create_window(icon=...)` TypeError | pywebview has NO icon param. Use ctypes SendMessageW in start() callback |
| .bat Chinese garbled text | Add `chcp 65001 >nul 2>&1` as first line. Prefer ASCII in bat files |
| Logo ICO looks tiny/blurry | Auto-crop white background before converting. Effective area often <40% |
| ICO save produces tiny file | Use `base.save(dst, format='ICO', sizes=[...])` not append_images approach |

### Templates

See `templates/pyinstaller-desktop.py` for a complete copy-paste-ready launcher (PyInstaller-compatible with BUNDLE_DIR/APP_DIR split, first-run init, port detection).

See `templates/pyinstaller.spec` for a starter PyInstaller spec file with webview hidden imports and common excludes.

See `templates/build.bat` for a one-click rebuild script (clean → build → copy database).

## CLI Integration

When building Flask apps that wrap CLI tools (hermes, git, docker, etc.), see `references/cli-integration-patterns.md` for:
- Parsing table-formatted CLI output (box-drawing characters)
- Hermes CLI command quirks (no --json flag, no agents command)
- Status/key-value output parsing
- Error handling patterns
- LLM connectivity test endpoint pattern

## LLM Agent Chat Integration

See `references/llm-agent-chat.md` for:
- User-to-agent chat with real LLM responses (hermes -z)
- Async polling pattern (track message ID, poll for m.id > myMsgId)
- sender_id=null for user, receiver_id=null for agent replies
- Conversation context (last 10 messages)
- Thread-based async LLM calls with app_context

See `references/upward-dropdown.md` for:
- Custom upward-opening dropdown component (CSS + JS)
- Bottom-positioned menus for chat input bars
- Click-outside-to-close pattern

See `references/sqlite-schema-migration.md` for:
- Changing column nullability via table recreation
- Adding columns to existing tables
- Migration in Flask startup
- SQLAlchemy reserved names (metadata, type, query)

## Multi-Agent Dashboard

When building agent management systems, see `references/multi-agent-dashboard.md` for:
- Agent/Task/Workflow/Message data models
- CLI integration (hermes skills list, hermes status)
- Agent-skill assignment patterns
- Workflow orchestration design
- SQLite schema migration (nullable FK, table recreation)

## Debugging

See reference files:
- `references/debugging-flask-spa.md` — SPA debugging, port conflicts
- `references/desktop-print-pywebview.md` — WebView2 print orientation fix
- `references/financial-auto-create.md` — Receivable/Payable auto-creation

## ERP Maintenance and Patching Patterns

For ongoing maintenance of an existing Flask + SQLite ERP system: diagnostic → simplify → seed-data verify → package as `patch_vX.Y.Z_*.zip`. Covers patterns validated across multiple patch cycles on production ERP systems (e.g., cl_system E:\cl_system).

### Diagnostic workflow

**Read code, don't guess** — use ripgrep for key patterns, not full file reads:
- Report issues → `core/report.py`
- Quotation issues → `core/quotation_parser.py`
- Project/subproject/material → `app.py` route handlers
- DB table structure → `core/db.py` `CREATE TABLE`

**Look at actual DB state** — user reports may have stale data:

```python
import sqlite3
conn = sqlite3.connect('cost_center.db', timeout=30)
conn.row_factory = sqlite3.Row
for tbl in ['materials','material_suppliers','bom_substitutes','bom_items', ...]:
    cnt = conn.execute(f'SELECT COUNT(*) FROM {tbl}').fetchone()[0]
    print(f'  {tbl}: {cnt}')
```

Key insight: `materials=0` + `pending_bom_substitutes>0` = materials cleared but staging pool still there. `bom_substitutes=0` = alternatives never staged. Data missing ≠ bug, but may explain why user sees 0.

### New feature workflow (4-step minimum)

When adding a new feature (not a bug fix) — usually a 4-place change that fails silently if any one is missed:

1. **New file** (`core/<feature>.py` or `core/<feature>_excel.py`) — reads existing data, outputs bytes/file/dict, **does NOT touch DB**
2. **app.py route** — add `?format=excel/pdf/word` branch in existing route BEFORE html rendering (HTML path untouched)
3. **templates/index.html** — add a button in a user-discoverable location
4. **static/js/app.js** — fetch + blob + `<a download>` pattern (with `credentials: 'same-origin'` for login-protected routes)

`?format=` fork template (in app.py before HTML rendering):

```python
fmt = request.args.get('format', 'html')
if fmt == 'excel':
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
# HTML path follows unchanged
```

Frontend fetch+blob pattern (cl_system requires `same-origin` for login):

```javascript
function exportXxx() {
  const params = new URLSearchParams();
  params.set('format', 'excel');
  const url = `/api/xxx/${pid}?` + params.toString();
  fetch(url, { credentials: 'same-origin' })
    .then(async (resp) => {
      if (!resp.ok) {
        let msg = `HTTP ${resp.status}`;
        try { const j = await resp.json(); if (j?.error) msg = j.error; } catch (_) {}
        throw new Error(msg);
      }
      return resp.blob();
    })
    .then((blob) => {
      const dlUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = dlUrl; a.download = `xxx_${pid}_${Date.now()}.xlsx`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(dlUrl);
      showToast('已开始下载', 'success');
    })
    .catch((e) => showToast('导出失败: ' + e.message, 'error'));
}
```

Why fetch+blob not `<a download href="?format=excel">`? Error responses get saved as .xlsx with garbage filename; can't toast errors; fetch+blob lets JS show backend error.

### Simplification principle

**User preference: simplicity**. Common phrases: "弄复杂了" / "不用这么麻烦" / "直接 X 不就可以了" / "瞎折腾". Every fallback branch must answer "can this be reached?":

- If `bom_items.unit_cost` branch can never match (替代料 if also in BOM isn't a substitute) → remove that branch
- Don't route through `bom_items` for "current price" — use `material_suppliers.last_price` + `price_history` only
- SQL: prefer `COALESCE(a, b, 0)` over multi-layer `CASE WHEN`
- Scope strictly: "I only want X" → deliver minimal viable X, don't add admin UI / config abstractions / test endpoints

### Seed data verification (mandatory)

Not just SQL test or `print("OK")`. Real function call + known input/output:

```python
import sqlite3, shutil, os, sys
BACKUP = os.path.expanduser('~/AppData/Local/Temp/cost_center.db.backup')
shutil.copy('cost_center.db', BACKUP)
conn = sqlite3.connect('cost_center.db', timeout=30)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
# Insert seed: 主料 + 替代料 + 供应商 + 物料-供应商关联 + 价格
cur.execute("INSERT OR IGNORE INTO materials(id, code, name, is_temporary) VALUES (15054, 'TEST_M1', '主料一', 0)")
cur.execute("INSERT OR IGNORE INTO material_suppliers(id, material_id, supplier_id, role, last_price) VALUES (12, 15055, 9, 'secondary', 3.0)")
conn.commit()
# Call real function
sys.path.insert(0, '.')
from core import report as rep
data = rep.get_project_report_data(project_id)
for s in data['sub_reports']:
    for it in s['items']:
        for a in it.get('alt_candidates', []):
            print(f"  {a['alt_code']} = {a['alt_unit_cost']} ({a['alt_price_source']})")
# Cleanup + restore from backup
```

**Mandatory fallback chain coverage**: seed with ALL data sources populated always hits the first SQL level, never tests later fallbacks. Must also run a test where `material_suppliers` AND `price_history` are cleared for the substitute IDs, so SQL walks the last fallback. Then verify `alt_price_source` reaches the expected least-priority source.

### Patch packaging (vX.Y.Z format)

Each patch delivers `patch_vX.Y.Z_<desc>.zip` in the project root. **Combine multiple changes in one zip**, don't increment version per change:

```
patch_v4.0.0_<desc>.zip
├── app.py                      (only if app.py changed)
├── core/<modified_file>.py     (only if changed)
├── core/<new_module>.py        (only if new, e.g., report_excel.py)
├── templates/index.html        (only if button added)
├── static/js/app.js             (only if function added)
└── INSTALL.txt                 (install instructions)
```

Increment version only when old patch is already deployed in production and new change is incompatible. Otherwise keep same version, distinguish via INSTALL.txt "第 N 次重打".

INSTALL.txt template:

```
翔泰成本中心 - 补丁 vX.Y.Z
【本补丁修复内容】<one-line bug description>
【补丁文件清单】<files to overwrite>
【安装步骤】1. 关闭运行中的 start.py; 2. 解压到 E:\\cl_system\\ 根目录覆盖; 3. 双击 start.bat 重启
【修复方案】<why changed, what SQL/function changed>
【兼容性】<compatibility with prior patches>
【回滚】<backup overwrite path>
【验证情况】<seed data and result>
【补丁日期 / 版本 / 前置补丁 / 替代补丁>
```

### Common pitfalls (lessons learned)

- **Backup files older than schema era may lack current tables** — verify with `SELECT name FROM sqlite_master WHERE type='table'` before restoring. Schema evolves; old backups may not have `bom_substitutes` table.
- **Duplicate SQL drift**: same business logic in 2+ files (e.g., `_SUBSTITUTE_PRICE_SQL` in app.py + `alt_sql` in core/report.py). Fix one, forget the other → inconsistent behavior between modes. **Always grep for SQL patterns before changing any**; verify with e2e covering multiple `alt_price_source` values.
- **diff ≠ e2e**: user says "原来可以, 现在不行" — diff proves "I didn't break it" but not "it works now". Always run e2e against real functions, then check live DB. Pre-existing bugs may surface.
- **patch indentation trap**: `patch` tool may add one level of nesting. After patching Python, run `ast.parse` to validate. If indentation off, dedent by 4 spaces per over-indent level.
- **DB modification before backup**: `cp cost_center.db cost_center.db.before_<reason>.bak` before any data manipulation
- **v_old backup didn't have schema you expected**: schema-version-check before restore
- **font/code style**: patches must not change text content. Punctuation, sentence reorder, character substitution are off-limits.

### User blame vs DB problem

When user says "now broken, did you change it?":

1. **Byte-level diff** (use `re.search` to extract function, not line numbers) — prove patch didn't touch this code
2. **Real e2e** — run function with seed data, prove SQL returns correct result
3. **Check live DB** — if e2e passes but live fails, **check again** — user may have imported data between your checks. Data can change. The classic signature: bom_items count went from 0 → 278 between checks.
4. **Report** with evidence: `[diff] bytes match`, `[e2e] all 4 modes correct`, `[live DB] materials.max_price has data, material_suppliers=0` → SQL bug, not data bug

Full case studies: `references/patch-v4.0.0-changelog.md`, `references/duplicate-sql-drift.md`, `references/sql-fallback-pitfall.md`.

## User Preferences (赛光智能)
- Product management page shows ONLY finished products (product_type='finished')
- Sales order product dropdown: only finished products
- Production order: auto-fill BOM components from parent product
- "保存" button exports ALL data to Excel in backups/ directory
- Customers use Chinese company names with short aliases
- Products have real BOMs with 20+ components each
- New order forms default to 1 line item (not 2), user adds more via "添加行" button. Purchase order forms include payment_type dropdown (欠款/现款) and delivery_date field.
- User gives step-by-step instructions for multi-step tasks — respond one step at a time, wait for confirmation
- **Inventory terminology**: 产品管理 = finished goods; 原材料管理 = raw materials/components/semi-finished. Stock page must exclude finished products. "在手量" renamed to "库存" in UI. Remove "预留量" and "可用量" columns.
- **Scope precision**: User is EXTREMELY precise with instructions. "XX页面去掉YY" means filter YY from that page's API, NOT delete from database. NEVER over-interpret scope. When user says "删除原材料管理中的成品", they want a page-level filter, not a database operation. If unsure about scope, ask — do NOT assume.
- **Warehouse locations**: Single default location "SY" (SY库). Auto-created on app startup. Support adding new locations via UI "添加库位" button. Support filtering stock by location. No outbound (出库) button — outbound is automatic via production order release. When consolidating inventory from multiple old locations to a new one, use bulk migration:
```python
# Migrate stock from old location to new
for s in StockCurrent.query.filter_by(location_id=old_loc.id).all():
    existing = StockCurrent.query.filter_by(product_id=s.product_id, location_id=new_loc.id).first()
    if existing:
        existing.on_hand_qty = float(existing.on_hand_qty or 0) + float(s.on_hand_qty or 0)
    else:
        db.session.add(StockCurrent(product_id=s.product_id, location_id=new_loc.id, on_hand_qty=s.on_hand_qty))
    db.session.delete(s)
db.session.delete(old_loc)
db.session.commit()
```
- **Production order inventory**: On create (新增), auto-deduct raw materials based on BOM. Required = BOM_qty × order_qty × (1 + scrap_rate%). Allow negative inventory — do NOT block creation when stock is insufficient. Display negative values with red bold styling. On recall (撤回) or delete, auto-restore all deducted inventory. User selects warehouse location in the form. Order is created directly as 'released' status (no draft step, no "下达" button). Create endpoint handles everything including BOM expansion and inventory deduction.
- **Accounts Receivable/Payable**: Split "收付款" into "应收款管理" (linked to delivery orders) and "应付款管理" (linked to purchase orders). Auto-generate payable when creating purchase order, auto-generate receivable when creating delivery order. Status auto-calculates: pending/partial/paid based on amounts. Delete cascades to related financial records. Purchase orders have payment_type (现款/欠款) — cash orders skip payable generation.
- **出货管理**: No sales order linkage in delivery form. Use customer selector + product lines. System auto-deducts from SOs via FIFO. Support recall (撤回) and delete with reverse deduction.
- **Supplier categories**: Use `<optgroup>` grouped dropdowns — 电子类/结构类/其他, each with specific subcategories (电阻/电容/IC芯片/连接器/PCB/结构件/模具/线材线缆/包材/外协加工/弹簧/胶布 etc.)
- **Product edit form**: 售价(list_price) removed from edit form; 现货库存(on_hand_qty) is editable; 线上库存(online_stock) is auto-computed readonly.
- **Inline entity creation**: User prefers creating related entities inline (without leaving the current page). Example: adding a new component material directly from the BOM page instead of going to 产品管理 first. When a form references another entity via dropdown, add a "新增" button next to the dropdown that opens a quick-create modal.
- **BOM unit_price**: BOM table prices must be inline-editable (input-group with onchange). User sets manual prices for cost estimation. Show price source badge (手动=red, 入库/采购/标准=grey). Add form includes optional单价 field (0=auto-detect). PUT endpoint updates individual BOM rows.
- **Alternative parts in dropdowns**: Product dropdowns in order forms (PO, SO) must show alternative part numbers: `SKU - 名称 [备:ALT1/ALT2]`. Use bulk GET /api/alternative-parts + altMap grouping. No alt suffix if product has no alternatives.
- **Naming convention**: "销售订单" renamed to "客户订单管理" — these are customer orders, not direct sales. "采购订单" renamed to "采购订单管理". "供应商" renamed to "供应商管理". Monthly sales figures come from delivery/shipment data, not from order creation. MRP运算 module removed entirely — not needed. Menu items use "管理" suffix for consistency.
- **Invoice management**: No manual form. Upload invoice PDF → pdfplumber extracts text → regex parses invoice fields (发票号码/日期/购买方/销售方/金额/税额/明细行) → user confirms → save. Pure local processing, no external API. See `references/pdf-invoice-parsing.md` for the complete parser. Invoice page shows 进项总和(采购发票) and 销项总和(销售发票) summary cards at top, always showing full totals regardless of type filter. Detail modal shows full item breakdown: 类别|项目名称|规格型号|单位|数量|单价|税率|金额|税额.
- **Invoice detail storage**: OCR items stored in `remark` field as `||OCR_ITEMS:{json}`. Frontend parses from remark to display in detail modal. `description` field stores `项目名称 + 规格型号` combined. Detail modal columns: 序号|类别|项目名称|规格型号|单位|数量|单价|税率|金额|税额. The spec (规格型号) is extracted separately from the item name during OCR parsing — first Chinese word group is the description, remainder is the spec.
- **Delivery list UI**: Use card-based layout (not flat table) — each delivery is a Bootstrap card with header (单号+客户+PO+日期+状态+撤回/打印/删除按钮) and body (detail table with columns: 产品SKU|产品名称|数量|单价|金额|PO号|批号). Cancelled deliveries still show delete button; only non-cancelled show recall button. All deliveries show print button.
- **Delivery print**: Use window.open() approach with company branding (杭州赛光智能科技有限公司). Sections: company header, 出库单 title, order number + date, customer info (name/PO/contact/phone/address), shipping info (method/tracking), detail table, summary, signature area (制单人/仓库主管/收货人).
- **Delivery PO splitting**: One input line auto-splits across multiple POs via FIFO. Each delivery line stores its own PO number and price. Table columns: 产品SKU|产品名称|数量|单价|金额|PO号|批号.
- **Report dashboard**: Professional layout with stat-cards for KPIs (本月销售/采购/库存/发票), financial cards (应收/应付/净现金流), category cards (客户供应商/库存状态/生产状态), and analysis table (采购销售比/周转率/占比). API returns flat keys matching frontend property access (e.g. `month_sales` not `{sales: {total_amount}}`). Frontend uses try/catch with error display. Backorder statistics come from customer orders (SalesOrderLine), not purchase orders.
- **Supplier delete**: Check for related purchase orders before deleting. Return error 400 with count if related records exist.
- **Save-all endpoint**: Frontend calls `/api/save-all` (not `/api/export/excel`). These are separate endpoints. save-all exports ALL data to Excel in backups/ directory.
- **app.py file safety**: NEVER use Python scripts to modify app.py (read_file has 500-line limit, causes truncation). Use `patch` tool for edits, `delegate_task` for batch changes. If truncated, use delegate_task to rebuild from context.
