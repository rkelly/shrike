---
test_name: No Orphaned Orders
description: Verify all orders reference a valid customer
connection: production_db
tags: data-integrity, orders
success_column: orphan_count
success_value: 0
---
SELECT COUNT(*) AS orphan_count
FROM   orders o
LEFT JOIN customers c ON o.customer_id = c.id
WHERE  c.id IS NULL
