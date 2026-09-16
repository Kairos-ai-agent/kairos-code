---
name: "database-schema-designer"
description: "Design production-ready database schemas with best practices for ERP systems. Use when designing database models, creating ER diagrams, defining table structures, or planning data architecture."
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\erp\\database-schema-designer\\SKILL.md"
---
# Database Schema Designer

Design production-ready database schemas with best practices built-in.

## Design Process

### 1. Requirements Analysis
- Identify entities (nouns in requirements)
- Define attributes for each entity
- Determine relationships (1:1, 1:N, M:N)
- Identify business rules and constraints

### 2. Normalization
- **1NF**: No repeating groups, atomic values
- **2NF**: No partial dependencies on composite keys
- **3NF**: No transitive dependencies
- **BCNF**: Every determinant is a candidate key
- Denormalize strategically for read performance

### 3. Naming Conventions
- Tables: plural nouns, snake_case (`sales_orders`, `product_categories`)
- Columns: snake_case, descriptive (`unit_price`, `created_at`)
- Primary keys: `id` (auto-increment) or `uuid`
- Foreign keys: `{referenced_table_singular}_id` (`customer_id`)
- Indexes: `idx_{table}_{columns}` (`idx_orders_customer_id`)
- Unique constraints: `uq_{table}_{columns}`

## Standard ERP Schema Patterns

### Master Data Tables
```sql
-- Products/Items
CREATE TABLE products (
    id BIGSERIAL PRIMARY KEY,
    sku VARCHAR(50) NOT NULL UNIQUE,
    name VARCHAR(200) NOT NULL,
    description TEXT,
    category_id BIGINT REFERENCES product_categories(id),
    unit_of_measure VARCHAR(20) NOT NULL DEFAULT 'PCS',
    standard_cost DECIMAL(15,4),
    list_price DECIMAL(15,4),
    weight DECIMAL(10,3),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- BOM (Bill of Materials)
CREATE TABLE bill_of_materials (
    id BIGSERIAL PRIMARY KEY,
    parent_product_id BIGINT NOT NULL REFERENCES products(id),
    component_product_id BIGINT NOT NULL REFERENCES products(id),
    quantity DECIMAL(12,4) NOT NULL,
    scrap_rate DECIMAL(5,2) DEFAULT 0,
    operation_no INTEGER,
    is_active BOOLEAN DEFAULT TRUE,
    UNIQUE(parent_product_id, component_product_id, operation_no)
);
```

### Transaction Tables
```sql
-- Sales Orders
CREATE TABLE sales_orders (
    id BIGSERIAL PRIMARY KEY,
    order_no VARCHAR(30) NOT NULL UNIQUE,
    customer_id BIGINT NOT NULL REFERENCES customers(id),
    order_date DATE NOT NULL DEFAULT CURRENT_DATE,
    status VARCHAR(20) NOT NULL DEFAULT 'draft',
    currency_code VARCHAR(3) DEFAULT 'CNY',
    total_amount DECIMAL(15,2),
    created_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE sales_order_lines (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES sales_orders(id) ON DELETE CASCADE,
    line_no INTEGER NOT NULL,
    product_id BIGINT NOT NULL REFERENCES products(id),
    quantity DECIMAL(12,4) NOT NULL,
    unit_price DECIMAL(15,4) NOT NULL,
    discount_pct DECIMAL(5,2) DEFAULT 0,
    line_amount DECIMAL(15,2) GENERATED ALWAYS AS (quantity * unit_price * (1 - discount_pct/100)) STORED,
    UNIQUE(order_id, line_no)
);
```

### Inventory Tables
```sql
CREATE TABLE stock_moves (
    id BIGSERIAL PRIMARY KEY,
    move_no VARCHAR(30) NOT NULL UNIQUE,
    product_id BIGINT NOT NULL REFERENCES products(id),
    source_location_id BIGINT REFERENCES locations(id),
    dest_location_id BIGINT REFERENCES locations(id),
    quantity DECIMAL(12,4) NOT NULL,
    move_type VARCHAR(20) NOT NULL, -- receipt, issue, transfer, adjustment
    reference_type VARCHAR(30), -- 'sales_order', 'purchase_order', 'production_order'
    reference_id BIGINT,
    lot_no VARCHAR(50),
    move_date TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by BIGINT REFERENCES users(id)
);

-- Current stock view
CREATE VIEW current_stock AS
SELECT product_id, dest_location_id as location_id,
       SUM(CASE WHEN move_type IN ('receipt','transfer_in','adjustment_pos') THEN quantity ELSE 0 END)
     - SUM(CASE WHEN move_type IN ('issue','transfer_out','adjustment_neg') THEN quantity ELSE 0 END)
       AS on_hand_qty
FROM stock_moves
GROUP BY product_id, dest_location_id;
```

## Indexing Strategy

```sql
-- Foreign keys (always index)
CREATE INDEX idx_so_customer ON sales_orders(customer_id);
CREATE INDEX idx_sol_order ON sales_order_lines(order_id);
CREATE INDEX idx_sol_product ON sales_order_lines(product_id);

-- Query patterns
CREATE INDEX idx_so_date ON sales_orders(order_date);
CREATE INDEX idx_so_status ON sales_orders(status);
CREATE INDEX idx_stock_product ON stock_moves(product_id, dest_location_id);
CREATE INDEX idx_stock_date ON stock_moves(move_date);

-- Composite indexes for common filters
CREATE INDEX idx_so_customer_date ON sales_orders(customer_id, order_date DESC);
```

## Flask-SQLAlchemy Pitfalls

**DECIMAL type**: `db.Column(db.Decimal(15, 4))` raises `AttributeError: Decimal`. Use `db.Column(db.Numeric(15, 4))` instead. SQLAlchemy maps `Numeric` to the database's DECIMAL/NUMERIC type. The `Decimal` name is the Python stdlib type, not an SQLAlchemy column type.

**Numeric defaults**: When using `db.Numeric`, set `default=0` (Python int), not `Decimal('0')`. Flask-SQLAlchemy handles the conversion.

**Soft delete pattern**:
```python
class SoftDeleteMixin:
    deleted_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
```

## Best Practices

1. **Always use transactions** for multi-table writes
2. **Soft deletes** (`deleted_at` column) for master data, hard deletes for line items
3. **Audit columns** on every table: `created_at`, `updated_at`, `created_by`, `updated_by`
4. **DECIMAL for money**, never FLOAT
5. **TIMESTAMPTZ** for all timestamps
6. **UUID** for distributed systems, **BIGSERIAL** for single-database
7. **CHECK constraints** for data validation (`quantity > 0`, `status IN (...)`)
8. **Partial indexes** for active records (`WHERE deleted_at IS NULL`)
9. **Foreign keys with ON DELETE** appropriate action (CASCADE for lines, RESTRICT for masters)
10. **Generated columns** for derived data (line_amount, full_name)
