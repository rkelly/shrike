---
test_name: Revenue Reconciliation (within 1%)
description: >
  Compare total revenue between source and warehouse.
  Allow up to 1% variance to account for timing differences.
tags: cross-server, finance, reconciliation

connections:
  source:
    server: prod-sql.example.com
    database: app_production
    trusted_connection: true
  target:
    server: warehouse-sql.example.com
    database: data_warehouse
    trusted_connection: true

steps:
  - name: prod_revenue
    connection: source
  - name: wh_revenue
    connection: target

success_expression: >
  abs(steps['prod_revenue'][0]['total'] - steps['wh_revenue'][0]['total'])
  / max(steps['prod_revenue'][0]['total'], 1)
  < 0.01
---
--- step: prod_revenue
SELECT SUM(total_amount) AS total
FROM   orders
WHERE  order_date >= '2024-01-01'
  AND  order_date < '2024-02-01'
--- step: wh_revenue
SELECT SUM(revenue) AS total
FROM   fact_orders
WHERE  order_date >= '2024-01-01'
  AND  order_date < '2024-02-01'
