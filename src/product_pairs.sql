-- Count each unordered product pair at most once per order.
WITH order_products AS (
    SELECT DISTINCT order_id, product_id FROM order_items
)
SELECT DATE_FORMAT(o.created_at, '%Y-%m-01') AS month_start,
       a.product_id AS product_a, b.product_id AS product_b,
       COUNT(*) AS pair_orders
FROM order_products a
JOIN order_products b ON a.order_id = b.order_id AND a.product_id < b.product_id
JOIN orders o ON a.order_id = o.order_id
GROUP BY month_start, a.product_id, b.product_id
ORDER BY month_start, a.product_id, b.product_id;
