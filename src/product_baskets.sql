-- One row per order: aggregate items before joining to order-level money.
WITH item_totals AS (
    SELECT order_id, COUNT(*) AS item_count,
           COUNT(DISTINCT product_id) AS distinct_products,
           SUM(is_primary_item) AS primary_items,
           SUM(CASE WHEN is_primary_item = 0 THEN 1 ELSE 0 END) AS secondary_items,
           SUM(CASE WHEN is_primary_item = 0 THEN price_usd ELSE 0 END) AS secondary_sales_usd,
           SUM(price_usd) AS item_sales_usd, SUM(cogs_usd) AS item_cogs_usd
    FROM order_items GROUP BY order_id
)
SELECT o.order_id, o.created_at AS order_at, o.primary_product_id,
       o.items_purchased, o.price_usd AS sales_usd, o.cogs_usd,
       i.item_count, i.distinct_products, i.primary_items, i.secondary_items,
       i.secondary_sales_usd, i.item_sales_usd, i.item_cogs_usd
FROM orders o
JOIN item_totals i ON o.order_id = i.order_id
ORDER BY o.order_id;
