---
test_name: Order Count Matches Warehouse
description: >
  Verify the row count in production orders matches
  the warehouse fact table (for recent data).
tags: cross-server, reconciliation

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
  - name: prod_count
    connection: source
  - name: wh_count
    connection: target

success_expression: "steps['prod_count'][0]['cnt'] == steps['wh_count'][0]['cnt']"
---
--- step: prod_count
SELECT COUNT(*) AS cnt
FROM   orders
WHERE  order_date >= DATEADD(DAY, -7, GETDATE())
--- step: wh_count
SELECT COUNT(*) AS cnt
FROM   fact_orders
WHERE  order_date >= DATEADD(DAY, -7, GETDATE())
