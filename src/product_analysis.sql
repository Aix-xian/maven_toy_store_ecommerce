-- One row per order item. Refund events are read separately to avoid fanout.
SELECT
    i.order_item_id, i.created_at AS item_at,
    o.created_at AS order_at, i.order_id, i.product_id,
    p.product_name, p.created_at AS product_launched_at,
    i.is_primary_item, i.price_usd AS sales_usd, i.cogs_usd
FROM order_items i
JOIN orders o ON i.order_id = o.order_id
JOIN products p ON i.product_id = p.product_id
ORDER BY i.order_item_id;
