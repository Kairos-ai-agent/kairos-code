---
name: "inventory-manager"
description: "Track inventory levels, manage stock, forecast demand, and optimize replenishment. Use when building inventory management features, stock tracking, or demand planning systems."
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\erp\\inventory-manager\\SKILL.md"
---
# Inventory Manager

Expert inventory management system for tracking stock levels, forecasting demand, optimizing reorder points, and preventing stockouts while minimizing carrying costs.

## Core Workflows

### 1. Inventory Setup & Classification

**Master Data:**
- Item Basics: SKU, description, category, supplier
- Costs: Unit cost, carrying cost (% of value per year), ordering cost
- Physical: Dimensions, weight, storage requirements
- Lead Times: Supplier lead time, production lead time
- Constraints: MOQ, shelf life, storage capacity

**ABC Classification:**
- **A Items** (20% of items, 80% of value): Tight control, daily monitoring, 95-99% service level
- **B Items** (30% of items, 15% of value): Moderate control, weekly monitoring, 90-95% service level
- **C Items** (50% of items, 5% of value): Loose control, monthly monitoring, 85-90% service level

**Tracking Methods:** Perpetual inventory, periodic inventory, cycle counting, barcode/RFID scanning

### 2. Demand Forecasting

- Gather historical sales data (minimum 12-24 months)
- Identify patterns: Trend, Seasonality, Cyclical, Random
- Methods: Moving average, weighted moving average, exponential smoothing, seasonal decomposition
- Calculate MAPE (Mean Absolute Percentage Error) for accuracy
- Adjust for promotions, market trends, new product launches

### 3. Reorder Point & Safety Stock

```
Reorder Point = (Average Daily Demand × Lead Time Days) + Safety Stock

Safety Stock = Z-Score × (Demand Std Dev) × √(Lead Time)
  Z-Score: 90% → 1.28, 95% → 1.65, 99% → 2.33

EOQ = √((2 × Annual Demand × Ordering Cost) / Carrying Cost per Unit)
```

### 4. Stock Monitoring & Replenishment

- Check stock levels vs. reorder points daily
- Systems: Reorder Point, Periodic Review, Min-Max, Just-in-Time
- Generate POs with calculated quantities
- Inspect incoming shipments, update inventory immediately

### 5. Inventory Optimization

```
Inventory Turnover = Cost of Goods Sold / Average Inventory Value
DIO = 365 / Turnover
```

- Identify dead stock (no sales in 90+ days)
- Aging analysis: 30/60/90+ days
- Disposition: discounts, return to supplier, donate/dispose

## Key Metrics

| Metric | Target |
|--------|--------|
| Inventory turnover | Industry-dependent (retail: 8-12, mfg: 4-8) |
| Inventory accuracy | 95%+ |
| Forecast accuracy (MAPE) | <20% |
| Fill rate | 95%+ for A items |

## Best Practices

- Regular cycle counts (daily A, weekly B, monthly C)
- Use barcode/RFID scanning to eliminate manual errors
- Review forecasts monthly, track accuracy
- Set service levels by item importance
- Review safety stock quarterly
- Track supplier on-time delivery rates

## Common Pitfalls

- Over-ordering: tying up cash in excess inventory
- Under-ordering: frequent stockouts and lost sales
- Ignoring ABC: treating all items equally
- Static reorder points: not adjusting for seasonality
- Siloed systems: inventory not integrated with sales/procurement

## Integration Points

- **POS**: Real-time sales deduction
- **E-commerce**: Sync online and physical inventory
- **ERP/Accounting**: COGS, valuation, financial reporting
- **WMS**: Location tracking, pick/pack/ship
- **Supplier EDI**: Automated POs and ASN
