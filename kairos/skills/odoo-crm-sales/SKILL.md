---
name: "odoo-crm-sales"
description: "Odoo CRM and Sales module development guide. Use when building Odoo CRM features, customizing sales workflows, or extending Odoo's customer/opportunity management."
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\erp\\odoo-crm-sales\\SKILL.md"
---
# Odoo CRM & Sales Module Development

Expert guidance for developing Odoo CRM and Sales modules for B2B electronics manufacturing.

## Module Structure

```
custom_crm/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── crm_lead.py          # Extended lead/opportunity
│   ├── sale_order.py         # Sales order customizations
│   ├── res_partner.py        # Customer extensions
│   ├── product_template.py   # Product extensions
│   └── quotation.py          # Custom quotation logic
├── views/
│   ├── crm_lead_views.xml
│   ├── sale_order_views.xml
│   ├── res_partner_views.xml
│   └── menu.xml
├── security/
│   ├── ir.model.access.csv
│   └── crm_security.xml
├── data/
│   ├── crm_stage_data.xml
│   └── email_templates.xml
├── reports/
│   └── quotation_report.xml
└── wizards/
    └── sample_request.py
```

## Key Model Extensions

### CRM Lead Customization

```python
from odoo import models, fields, api

class CrmLead(models.Model):
    _inherit = 'crm.lead'

    # Electronics-specific fields
    project_name = fields.Char(string='Project Name')
    product_category = fields.Selection([
        ('module', '电子模块'),
        ('component', '元器件'),
        ('pcb', 'PCB'),
        ('assembly', '组装件'),
    ], string='Product Category')
    estimated_annual_qty = fields.Integer(string='Estimated Annual Qty')
    sample_requested = fields.Boolean(string='Sample Requested')
    sample_sent_date = fields.Date(string='Sample Sent Date')
    sample_status = fields.Selection([
        ('not_sent', 'Not Sent'),
        ('sent', 'Sent'),
        ('evaluating', 'Under Evaluation'),
        ('passed', 'Passed'),
        ('failed', 'Failed'),
    ], default='not_sent', string='Sample Status')
    competitor_ids = fields.Many2many('res.partner', string='Competitors')
    technical_requirements = fields.Text(string='Technical Requirements')
    certification_required = fields.Char(string='Certifications Required')  # ISO, UL, CE, etc.

    # NRE (Non-Recurring Engineering) charges
    nre_amount = fields.Monetary(string='NRE Charge', currency_field='company_currency')
    nre_paid = fields.Boolean(string='NRE Paid')

    @api.onchange('sample_requested')
    def _onchange_sample_requested(self):
        if self.sample_requested and not self.sample_sent_date:
            self.sample_status = 'not_sent'

    def action_send_sample(self):
        """Create sample delivery order"""
        self.ensure_one()
        self.sample_sent_date = fields.Date.today()
        self.sample_status = 'sent'
        # Create delivery order logic here
```

### Sale Order Extensions

```python
class SaleOrder(models.Model):
    _inherit = 'sale.order'

    customer_po_ref = fields.Char(string='Customer PO Reference')
    project_name = fields.Char(string='Project Name')
    delivery_terms = fields.Selection([
        ('exw', 'EXW'),
        ('fob', 'FOB'),
        ('cif', 'CIF'),
        ('ddp', 'DDP'),
    ], string='Delivery Terms')
    partial_delivery = fields.Boolean(string='Partial Delivery Allowed', default=True)
    quality_requirements = fields.Text(string='Quality Requirements')
    packaging_requirements = fields.Text(string='Packaging Requirements')

    # Link to CRM opportunity
    opportunity_id = fields.Many2one('crm.lead', string='Opportunity',
        domain="[('type', '=', 'opportunity')]")

    @api.onchange('partner_id')
    def _onchange_partner_delivery_terms(self):
        if self.partner_id:
            self.delivery_terms = self.partner_id.delivery_terms or 'fob'
            self.payment_term_id = self.partner_id.property_payment_term_id
```

### Customer (res.partner) Extensions

```python
class ResPartner(models.Model):
    _inherit = 'res.partner'

    customer_tier = fields.Selection([
        ('vip', 'VIP'),
        ('key', 'Key Account'),
        ('regular', 'Regular'),
        ('prospect', 'Prospect'),
    ], string='Customer Tier', default='prospect')
    delivery_terms = fields.Selection([
        ('exw', 'EXW'), ('fob', 'FOB'), ('cif', 'CIF'), ('ddp', 'DDP'),
    ], string='Default Delivery Terms')
    industry_segment = fields.Selection([
        ('automotive', 'Automotive'),
        ('industrial', 'Industrial'),
        ('consumer', 'Consumer Electronics'),
        ('medical', 'Medical'),
        ('telecom', 'Telecom'),
        ('iot', 'IoT'),
        ('other', 'Other'),
    ], string='Industry Segment')
    certification_ids = fields.Many2many('product.certification', string='Required Certifications')
    annual_revenue = fields.Monetary(string='Annual Revenue')
    credit_limit = fields.Monetary(string='Credit Limit')
```

## CRM Pipeline Stages (Electronics)

```xml
<!-- data/crm_stage_data.xml -->
<odoo>
    <data noupdate="1">
        <record id="crm_stage_initial" model="crm.stage">
            <field name="name">Initial Contact</field>
            <field name="sequence">1</field>
            <field name="probability">10</field>
            <field name="fold">False</field>
        </record>
        <record id="crm_stage_specs" model="crm.stage">
            <field name="name">Specs Confirmed</field>
            <field name="sequence">2</field>
            <field name="probability">20</field>
        </record>
        <record id="crm_stage_sample" model="crm.stage">
            <field name="name">Sample Sent</field>
            <field name="sequence">3</field>
            <field name="probability">40</field>
        </record>
        <record id="crm_stage_quotation" model="crm.stage">
            <field name="name">Quotation</field>
            <field name="sequence">4</field>
            <field name="probability">60</field>
        </record>
        <record id="crm_stage_negotiation" model="crm.stage">
            <field name="name">Negotiation</field>
            <field name="sequence">5</field>
            <field name="probability">80</field>
        </record>
        <record id="crm_stage_won" model="crm.stage">
            <field name="name">Won</field>
            <field name="sequence">6</field>
            <field name="probability">100</field>
            <field name="is_won">True</field>
        </record>
    </data>
</odoo>
```

## View Customization

```xml
<!-- views/crm_lead_views.xml -->
<odoo>
    <record id="crm_lead_view_form_inherit" model="ir.ui.view">
        <field name="name">crm.lead.form.inherit.custom</field>
        <field name="model">crm.lead</field>
        <field name="inherit_id" ref="crm.crm_case_form_view_oppor"/>
        <field name="arch" type="xml">
            <xpath expr="//group[@name='opportunity_partner']" position="after">
                <group string="Electronics Project Info">
                    <field name="project_name"/>
                    <field name="product_category"/>
                    <field name="estimated_annual_qty"/>
                    <field name="technical_requirements"/>
                    <field name="certification_required"/>
                </group>
                <group string="Sample Tracking">
                    <field name="sample_requested"/>
                    <field name="sample_sent_date" attrs="{'invisible': [('sample_requested', '=', False)]}"/>
                    <field name="sample_status" widget="statusbar" attrs="{'invisible': [('sample_requested', '=', False)]}"/>
                </group>
                <group string="NRE">
                    <field name="nre_amount"/>
                    <field name="nre_paid"/>
                </group>
            </xpath>
        </field>
    </record>
</odoo>
```

## Security

```csv
# security/ir.model.access.csv
id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
access_crm_lead_custom,crm.lead.custom,crm.model_crm_lead,sales_team.group_sale_salesman,1,1,1,0
access_sale_order_custom,sale.order.custom,sale.model_sale_order,sales_team.group_sale_salesman,1,1,1,0
```

## Best Practices for Odoo CRM in Electronics

1. **Use Studio or code** — Studio for quick fields, code for complex logic
2. **Sample tracking workflow** — Critical for B2B electronics sales
3. **Link SO to opportunity** — Track full customer journey
4. **Custom reports** — Pipeline by product category, win/loss analysis
5. **Automated emails** — Sample follow-up, quotation reminders
6. **Integration with inventory** — ATP check when creating quotations
7. **Customer tier automation** — Auto-upgrade tier based on revenue
