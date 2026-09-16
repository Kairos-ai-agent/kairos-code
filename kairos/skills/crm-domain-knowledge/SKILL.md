---
name: "crm-domain-knowledge"
description: "CRM domain knowledge covering sales pipeline, customer management, lead/opportunity tracking, quotation management, and after-sales service. Use when building CRM features, designing sales workflows,"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\erp\\crm-domain-knowledge\\SKILL.md"
---
# CRM Domain Knowledge

Comprehensive CRM knowledge for building customer relationship management systems, especially for B2B electronics/component manufacturing businesses.

## Core Modules

### 1. Lead Management

**Lead Sources:**
- Website inquiry / contact form
- Trade show / exhibition (慕尼黑电子展、高交会等)
- Referral from existing customer
- Cold outreach / marketing campaign
- Online marketplace (1688, Alibaba, Made-in-China)
- Social media (LinkedIn, WeChat)

**Lead Lifecycle:**
```
New → Contacted → Qualified → Converted to Opportunity
                                └→ Disqualified (Lost/Invalid)
```

**Lead Qualification Criteria (BANT):**
- **Budget**: Customer has budget for the project
- **Authority**: Contact person is decision-maker or influencer
- **Need**: Clear product requirement exists
- **Timeline**: Purchase decision within defined period

### 2. Customer / Account Management

**Customer Master Data:**
- Company name, registration number, tax ID
- Industry, company size, website
- Billing address, shipping address(es)
- Credit limit, payment terms
- Customer tier/classification (VIP/Key/Regular/Prospect)
- Assigned salesperson

**Contact Management:**
- Multiple contacts per company
- Roles: Decision maker, Technical evaluator, Purchasing, End user
- Communication preferences
- Activity history

**Customer 360 View:**
- Open quotations and orders
- Order history and revenue
- Outstanding invoices and payments
- Support tickets
- Communication log
- Products they buy

### 3. Sales Pipeline / Opportunity Management

**Pipeline Stages (B2B Electronics):**
```
1. Initial Contact (10%) → First meeting/inquiry received
2. Requirements Clarified (20%) → Technical specs confirmed
3. Sample/Prototype Sent (40%) → Evaluation in progress
4. Quotation Submitted (60%) → Price negotiation
5. PO Received (80%) → Payment terms confirmed
6. Won (100%) → Order delivered and paid
   Lost (0%) → Closed lost with reason
```

**Opportunity Data:**
- Expected revenue and probability
- Expected close date
- Products/quantities interested
- Competitor analysis
- Decision criteria
- Win/loss reason

### 4. Quotation Management

**Quotation Workflow:**
```
Draft → Reviewed → Sent → Negotiating → Accepted/Rejected
  └→ Revised (new version)
```

**Quotation Components:**
- Quotation number (auto-generated)
- Validity period
- Customer reference/PO number
- Line items: product, qty, unit price, discount, amount
- Terms and conditions
- Payment terms, delivery terms (Incoterms)
- Technical specifications attachment

**Pricing Strategies for Electronics:**
- Volume-based tier pricing
- Project-based pricing (NRE + unit price)
- Long-term agreement (LTA) pricing
- Cost-plus pricing (cost + margin %)
- Competitive matching

### 5. Sales Order Processing

**Order Flow:**
```
Quotation Accepted → Sales Order Created → Confirmed
  → Credit Check → Stock Check / Production Plan
  → Delivery → Invoice → Payment Collection
```

**Key Data:**
- SO number, customer PO reference
- Delivery date, shipping method
- Partial delivery allowed?
- Special instructions (packaging, labeling, testing)

### 6. After-Sales / Service

**Service Types:**
- Technical support (phone/email/on-site)
- RMA (Return Material Authorization)
- Warranty claims
- Quality complaints (8D report)
- Product replacement/repair

**RMA Workflow:**
```
Customer Request → RMA Created → Return Received
  → Inspection → Disposition (Replace/Repair/Refund/Reject)
  → Closed
```

### 7. Marketing & Campaigns

- Campaign planning (email, WeChat, trade show)
- Target audience segmentation
- Campaign execution and tracking
- ROI analysis
- Lead generation from campaigns

## Key CRM Metrics

| Metric | Formula | Target |
|--------|---------|--------|
| Win Rate | Won / (Won + Lost) | >30% |
| Sales Cycle | Avg days from lead to close | <90 days |
| Pipeline Coverage | Pipeline value / Quota | >3x |
| Customer Retention | Repeat customers / Total | >80% |
| Avg Deal Size | Revenue / # of deals | Industry-dependent |
| Lead Response Time | Time from lead to first contact | <24 hours |
| Quotation Win Rate | Accepted quotes / Sent quotes | >25% |

## CRM Integration Points

```
Website/WeChat → Leads → Qualification → Opportunities
                                            ↓
                                     Quotations → Sales Orders
                                          ↓              ↓
                                     Products      Inventory/Delivery
                                          ↓              ↓
                                     Invoices → Payments → GL
```

- **Website/WeChat**: Lead capture
- **Inventory**: ATP check, stock reservation
- **Production**: Make-to-order triggers
- **Finance**: Invoice generation, payment tracking
- **Support**: Ticket creation from customer record

## B2B Electronics CRM Best Practices

1. **Track technical requirements** — specs, certifications, test reports
2. **Sample management** — track samples sent, evaluation status, feedback
3. **Long sales cycles** — nurture leads over months, not days
4. **Multiple decision makers** — map the buying committee
5. **Project-based selling** — link opportunities to customer projects
6. **Competitor tracking** — know which competitors are being evaluated
7. **Volume forecast** — share forecasts with production planning
